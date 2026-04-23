"""
Geo-Aware Peer Selection for TopoTorrent.

Estimates geographic proximity between peers to prefer closer ones.
Closer peers generally have:
- Lower latency
- Higher throughput
- Less impact on backbone congestion

Uses a built-in IP-to-region mapping based on IANA allocations.
No external database required.
"""

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import math


@dataclass
class GeoInfo:
    """Geographic information for an IP address."""
    ip: str
    country_code: str = "??"
    region: str = "Unknown"
    latitude: float = 0.0
    longitude: float = 0.0
    is_local: bool = False  # Same LAN / private IP
    is_same_isp: bool = False  # Same /16 subnet heuristic


# Major RIR allocations to approximate country/region from IP
# Format: (start_octet, end_octet, country_code, region, lat, lon)
# This is a simplified mapping — covers major allocations
_IP_REGIONS = [
    # North America
    (3, 3, "US", "North America", 37.0, -96.0),
    (4, 4, "US", "North America", 37.0, -96.0),
    (6, 6, "US", "North America", 37.0, -96.0),
    (7, 7, "US", "North America", 37.0, -96.0),
    (8, 8, "US", "North America", 37.0, -96.0),
    (9, 9, "US", "North America", 37.0, -96.0),
    (11, 11, "US", "North America", 37.0, -96.0),
    (12, 15, "US", "North America", 37.0, -96.0),
    (16, 16, "US", "North America", 37.0, -96.0),
    (17, 17, "US", "North America", 37.0, -96.0),
    (18, 19, "US", "North America", 37.0, -96.0),
    (20, 20, "US", "North America", 37.0, -96.0),
    (21, 22, "US", "North America", 37.0, -96.0),
    (23, 23, "CA", "North America", 56.0, -106.0),
    (24, 24, "US", "North America", 37.0, -96.0),
    (32, 35, "US", "North America", 37.0, -96.0),
    (38, 38, "US", "North America", 37.0, -96.0),
    (40, 40, "US", "North America", 37.0, -96.0),
    (44, 44, "US", "North America", 37.0, -96.0),
    (45, 45, "US", "North America", 37.0, -96.0),
    (47, 48, "US", "North America", 37.0, -96.0),
    (50, 50, "US", "North America", 37.0, -96.0),
    (52, 54, "US", "North America", 37.0, -96.0),
    (55, 55, "BR", "South America", -14.0, -51.0),
    (56, 56, "US", "North America", 37.0, -96.0),
    (57, 57, "US", "North America", 37.0, -96.0),
    (63, 63, "US", "North America", 37.0, -96.0),
    (64, 65, "US", "North America", 37.0, -96.0),
    (66, 66, "US", "North America", 37.0, -96.0),
    (67, 68, "US", "North America", 37.0, -96.0),
    (69, 72, "US", "North America", 37.0, -96.0),
    (73, 76, "US", "North America", 37.0, -96.0),
    (96, 96, "US", "North America", 37.0, -96.0),

    # Europe
    (77, 77, "EU", "Europe", 50.0, 10.0),
    (78, 79, "EU", "Europe", 50.0, 10.0),
    (80, 80, "EU", "Europe", 50.0, 10.0),
    (81, 82, "EU", "Europe", 50.0, 10.0),
    (83, 83, "EU", "Europe", 50.0, 10.0),
    (84, 86, "EU", "Europe", 50.0, 10.0),
    (87, 88, "EU", "Europe", 50.0, 10.0),
    (89, 89, "EU", "Europe", 50.0, 10.0),
    (90, 95, "EU", "Europe", 50.0, 10.0),
    (109, 109, "EU", "Europe", 50.0, 10.0),
    (141, 141, "EU", "Europe", 50.0, 10.0),
    (145, 145, "EU", "Europe", 50.0, 10.0),
    (151, 151, "EU", "Europe", 50.0, 10.0),
    (176, 176, "EU", "Europe", 50.0, 10.0),
    (178, 178, "EU", "Europe", 50.0, 10.0),
    (185, 185, "EU", "Europe", 50.0, 10.0),
    (188, 188, "EU", "Europe", 50.0, 10.0),
    (193, 195, "EU", "Europe", 50.0, 10.0),
    (212, 213, "EU", "Europe", 50.0, 10.0),
    (217, 217, "EU", "Europe", 50.0, 10.0),

    # Asia-Pacific
    (1, 1, "AU", "Asia-Pacific", -25.0, 133.0),
    (14, 14, "JP", "Asia-Pacific", 36.0, 138.0),
    (27, 27, "IN", "South Asia", 20.0, 77.0),
    (36, 36, "CN", "East Asia", 35.0, 105.0),
    (39, 39, "CN", "East Asia", 35.0, 105.0),
    (42, 42, "JP", "Asia-Pacific", 36.0, 138.0),
    (43, 43, "JP", "Asia-Pacific", 36.0, 138.0),
    (49, 49, "KR", "East Asia", 36.0, 128.0),
    (58, 58, "CN", "East Asia", 35.0, 105.0),
    (59, 59, "KR", "East Asia", 36.0, 128.0),
    (60, 61, "JP", "Asia-Pacific", 36.0, 138.0),
    (101, 101, "IN", "South Asia", 20.0, 77.0),
    (103, 103, "AP", "Asia-Pacific", 15.0, 105.0),
    (106, 106, "CN", "East Asia", 35.0, 105.0),
    (110, 112, "CN", "East Asia", 35.0, 105.0),
    (113, 113, "JP", "Asia-Pacific", 36.0, 138.0),
    (114, 115, "CN", "East Asia", 35.0, 105.0),
    (116, 120, "CN", "East Asia", 35.0, 105.0),
    (121, 122, "KR", "East Asia", 36.0, 128.0),
    (123, 125, "CN", "East Asia", 35.0, 105.0),
    (126, 126, "JP", "Asia-Pacific", 36.0, 138.0),
    (133, 133, "JP", "Asia-Pacific", 36.0, 138.0),
    (150, 150, "AP", "Asia-Pacific", 15.0, 105.0),
    (163, 163, "CN", "East Asia", 35.0, 105.0),
    (175, 175, "AP", "Asia-Pacific", 15.0, 105.0),
    (180, 180, "CN", "East Asia", 35.0, 105.0),
    (182, 183, "CN", "East Asia", 35.0, 105.0),
    (202, 203, "AP", "Asia-Pacific", 15.0, 105.0),
    (210, 211, "AP", "Asia-Pacific", 15.0, 105.0),
    (218, 219, "KR", "East Asia", 36.0, 128.0),
    (220, 222, "CN", "East Asia", 35.0, 105.0),
    (223, 223, "CN", "East Asia", 35.0, 105.0),

    # Africa
    (41, 41, "ZA", "Africa", -29.0, 24.0),
    (102, 102, "AF", "Africa", 7.0, 21.0),
    (105, 105, "AF", "Africa", 7.0, 21.0),
    (154, 154, "AF", "Africa", 7.0, 21.0),
    (196, 197, "AF", "Africa", 7.0, 21.0),

    # Latin America
    (177, 177, "BR", "South America", -14.0, -51.0),
    (179, 179, "BR", "South America", -14.0, -51.0),
    (181, 181, "LA", "Latin America", -10.0, -55.0),
    (186, 187, "BR", "South America", -14.0, -51.0),
    (189, 190, "LA", "Latin America", -10.0, -55.0),
    (191, 191, "BR", "South America", -14.0, -51.0),
    (200, 201, "LA", "Latin America", -10.0, -55.0),

    # Middle East
    (5, 5, "ME", "Middle East", 29.0, 47.0),
    (37, 37, "ME", "Middle East", 29.0, 47.0),
    (46, 46, "RU", "Russia", 61.0, 105.0),
    (62, 62, "RU", "Russia", 61.0, 105.0),

    # Russia/CIS
    (2, 2, "RU", "Russia", 61.0, 105.0),
    (25, 25, "EU", "Europe", 50.0, 10.0),
    (31, 31, "EU", "Europe", 50.0, 10.0),
    (46, 46, "RU", "Russia", 61.0, 105.0),
    (91, 91, "IN", "South Asia", 20.0, 77.0),
    (92, 92, "RU", "Russia", 61.0, 105.0),

    # Oceania
    (1, 1, "AU", "Oceania", -25.0, 133.0),
    (103, 103, "AU", "Oceania", -25.0, 133.0),
]

# Build fast lookup dict
_FIRST_OCTET_MAP: Dict[int, Tuple[str, str, float, float]] = {}
for start, end, cc, region, lat, lon in _IP_REGIONS:
    for octet in range(start, end + 1):
        if octet not in _FIRST_OCTET_MAP:
            _FIRST_OCTET_MAP[octet] = (cc, region, lat, lon)

# Private IP ranges
_PRIVATE_RANGES = [
    (167772160, 184549375),   # 10.0.0.0/8
    (2886729728, 2887778303),  # 172.16.0.0/12
    (3232235520, 3232301055),  # 192.168.0.0/16
]


def _ip_to_int(ip: str) -> int:
    """Convert dotted IP to int."""
    try:
        parts = ip.split(".")
        return (int(parts[0]) << 24) | (int(parts[1]) << 16) | \
               (int(parts[2]) << 8) | int(parts[3])
    except Exception:
        return 0


def _is_private(ip_int: int) -> bool:
    """Check if IP is in a private range."""
    for start, end in _PRIVATE_RANGES:
        if start <= ip_int <= end:
            return True
    return False


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in km between two points."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def lookup_ip(ip: str) -> GeoInfo:
    """Look up geographic info for an IP address."""
    info = GeoInfo(ip=ip)

    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return info

        first_octet = int(parts[0])
        ip_int = _ip_to_int(ip)

        # Check private IP
        if _is_private(ip_int):
            info.is_local = True
            info.country_code = "LO"
            info.region = "Local Network"
            return info

        # Loopback
        if first_octet == 127:
            info.is_local = True
            info.country_code = "LO"
            info.region = "Loopback"
            return info

        # Look up region
        if first_octet in _FIRST_OCTET_MAP:
            cc, region, lat, lon = _FIRST_OCTET_MAP[first_octet]
            info.country_code = cc
            info.region = region
            info.latitude = lat
            info.longitude = lon

    except Exception:
        pass

    return info


class GeoPeerSelector:
    """
    Geo-aware peer selection engine.

    Scores peers based on geographic proximity and ISP similarity.
    Integrates with the TopologyEngine as an additional scoring factor.
    """

    def __init__(self, geo_weight: float = 0.15):
        self.geo_weight = geo_weight
        self._self_geo: Optional[GeoInfo] = None
        self._peer_geo: Dict[str, GeoInfo] = {}
        self._lock = threading.Lock()
        self._detect_self_location()

    def _detect_self_location(self):
        """Detect our own approximate location."""
        try:
            # Try to get external IP via a socket connection
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            my_ip = s.getsockname()[0]
            s.close()
            self._self_geo = lookup_ip(my_ip)
        except Exception:
            # Default to unknown
            self._self_geo = GeoInfo(ip="0.0.0.0")

    def get_peer_geo(self, ip: str) -> GeoInfo:
        """Get or compute geo info for a peer."""
        with self._lock:
            if ip not in self._peer_geo:
                self._peer_geo[ip] = lookup_ip(ip)
            return self._peer_geo[ip]

    def compute_geo_score(self, ip: str, port: int) -> float:
        """
        Compute a geo-proximity score (0..1) for a peer.

        1.0 = same local network
        0.8+ = same country/region
        0.5 = same continent
        0.1+ = different continent
        """
        peer_geo = self.get_peer_geo(ip)

        # Local network = maximum score
        if peer_geo.is_local:
            return 1.0

        if self._self_geo is None:
            return 0.5  # Unknown, neutral score

        # Same subnet (/16) = likely same ISP
        try:
            self_parts = self._self_geo.ip.split(".")
            peer_parts = ip.split(".")
            if len(self_parts) >= 2 and len(peer_parts) >= 2:
                if self_parts[0] == peer_parts[0] and self_parts[1] == peer_parts[1]:
                    peer_geo.is_same_isp = True
                    return 0.95  # Same ISP = very fast
        except Exception:
            pass

        # Same country
        if (peer_geo.country_code == self._self_geo.country_code
                and peer_geo.country_code != "??"):
            return 0.85

        # Same region
        if (peer_geo.region == self._self_geo.region
                and peer_geo.region != "Unknown"):
            return 0.7

        # Calculate distance-based score
        if (self._self_geo.latitude != 0 and peer_geo.latitude != 0):
            dist = _haversine(
                self._self_geo.latitude, self._self_geo.longitude,
                peer_geo.latitude, peer_geo.longitude,
            )
            # Normalize: 0km=1.0, 20000km=0.1
            score = max(0.1, 1.0 - dist / 20000.0)
            return score

        return 0.3  # Unknown distance, low score

    def rank_peers(self, peer_addresses: List[Tuple[str, int]]) -> List[Tuple[str, int, float]]:
        """
        Rank a list of peer addresses by geo score.

        Returns: List of (ip, port, geo_score) sorted by score desc.
        """
        scored = []
        for ip, port in peer_addresses:
            score = self.compute_geo_score(ip, port)
            scored.append((ip, port, score))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored

    def get_self_location(self) -> Optional[GeoInfo]:
        """Get our own location info."""
        return self._self_geo
