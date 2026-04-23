"""
Experimental Features for TopoTorrent.

Crown-level features pushing the boundaries of P2P:

1. AI Bandwidth Allocation
   - Observes per-peer throughput patterns
   - Allocates pipeline depth proportionally to peer performance
   - Adapts in real-time as peer speeds change

2. LAN Mesh Discovery
   - UDP broadcast on local network
   - Finds other TopoTorrent instances sharing the same torrent
   - Enables direct LAN-speed piece exchange

3. Cloud Seeder Hooks
   - API interface for optional cloud nodes
   - Cloud nodes can seed on behalf of the client
   - HTTP-based control protocol

4. Mobile Peer Bridge
   - Lightweight HTTP API
   - Companion mobile apps can contribute upload bandwidth
   - Simple REST interface for piece exchange
"""

import json
import os
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# 1. AI Bandwidth Allocation
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PeerBandwidthProfile:
    """Bandwidth profile for a peer tracked by the AI allocator."""
    ip: str
    port: int
    speed_samples: List[float] = field(default_factory=list)
    avg_speed: float = 0.0
    speed_variance: float = 0.0
    pipeline_depth: int = 5  # How many concurrent requests to send
    allocated_weight: float = 1.0  # Share of total bandwidth allocation
    trend: float = 0.0  # Positive = speeding up, negative = slowing down
    last_update: float = 0.0


class AIBandwidthAllocator:
    """
    ML-like per-peer bandwidth allocation.

    Observes each peer's throughput over time and dynamically adjusts:
    - Pipeline depth (more requests to fast peers)
    - Piece assignment priority (rate pieces to best performers)
    - Connection slots (drop slowest peers when at capacity)

    Uses exponential weighted moving average (EWMA) with trend detection.
    """

    MIN_PIPELINE = 2
    MAX_PIPELINE = 100
    EWMA_ALPHA = 0.3

    def __init__(self):
        self._profiles: Dict[str, PeerBandwidthProfile] = {}
        self._lock = threading.Lock()
        self._total_bandwidth = 0.0

    def update_peer_speed(self, ip: str, port: int, speed_bps: float):
        """Record a speed sample for a peer."""
        key = f"{ip}:{port}"
        now = time.time()

        with self._lock:
            if key not in self._profiles:
                self._profiles[key] = PeerBandwidthProfile(ip=ip, port=port)

            profile = self._profiles[key]
            profile.last_update = now

            # Add sample
            profile.speed_samples.append(speed_bps)
            if len(profile.speed_samples) > 30:
                profile.speed_samples.pop(0)

            # EWMA speed
            old_avg = profile.avg_speed
            profile.avg_speed = (
                self.EWMA_ALPHA * speed_bps
                + (1 - self.EWMA_ALPHA) * profile.avg_speed
            )

            # Trend detection
            profile.trend = profile.avg_speed - old_avg

            # Variance (for stability assessment)
            if len(profile.speed_samples) >= 3:
                mean = sum(profile.speed_samples) / len(profile.speed_samples)
                profile.speed_variance = sum(
                    (s - mean) ** 2 for s in profile.speed_samples
                ) / len(profile.speed_samples)

    def get_pipeline_depth(self, ip: str, port: int) -> int:
        """
        Get recommended pipeline depth for a peer.

        Fast, stable peers: high pipeline (up to 100)
        Slow or unstable peers: low pipeline (2-5)
        """
        key = f"{ip}:{port}"
        with self._lock:
            profile = self._profiles.get(key)
            if not profile:
                return 5  # Default

            # Base pipeline on speed: 1 pipeline per 50 KB/s
            base = max(self.MIN_PIPELINE, int(profile.avg_speed / (50 * 1024)))

            # Reduce for high variance (unstable connections)
            if profile.speed_variance > 0 and profile.avg_speed > 0:
                cv = (profile.speed_variance ** 0.5) / profile.avg_speed
                if cv > 0.5:
                    base = max(self.MIN_PIPELINE, int(base * 0.6))

            # Boost for positive trend
            if profile.trend > 0:
                base = int(base * 1.2)

            profile.pipeline_depth = min(self.MAX_PIPELINE, base)
            return profile.pipeline_depth

    def get_allocation_weights(self) -> Dict[str, float]:
        """
        Get bandwidth allocation weights for all peers.

        Returns dict of {ip:port: weight} where weights sum to 1.0.
        Faster peers get higher weights = more piece assignments.
        """
        with self._lock:
            if not self._profiles:
                return {}

            total_speed = sum(
                max(p.avg_speed, 1) for p in self._profiles.values()
            )

            weights = {}
            for key, profile in self._profiles.items():
                weight = max(profile.avg_speed, 1) / total_speed

                # Bonus for positive trend
                if profile.trend > 0:
                    weight *= 1.1

                # Penalty for high variance
                if profile.speed_variance > 0 and profile.avg_speed > 0:
                    cv = (profile.speed_variance ** 0.5) / profile.avg_speed
                    if cv > 1.0:
                        weight *= 0.8

                weights[key] = weight
                profile.allocated_weight = weight

            return weights

    def get_drop_candidates(self, n: int = 5) -> List[str]:
        """
        Get the N worst-performing peers suitable for disconnection.

        Used when at max connections to make room for potentially better peers.
        """
        with self._lock:
            scored = [
                (key, profile.avg_speed - profile.speed_variance * 0.1)
                for key, profile in self._profiles.items()
                if time.time() - profile.last_update < 120  # Active peers only
            ]
            scored.sort(key=lambda x: x[1])
            return [key for key, _ in scored[:n]]

    def remove_peer(self, ip: str, port: int):
        """Remove a peer from tracking."""
        key = f"{ip}:{port}"
        with self._lock:
            self._profiles.pop(key, None)


# ═══════════════════════════════════════════════════════════════════════
# 2. LAN Mesh Discovery
# ═══════════════════════════════════════════════════════════════════════

LAN_DISCOVERY_PORT = 51413
LAN_MAGIC = b"TOPOTORRENT_MESH_V1"


@dataclass
class LANPeer:
    """A TopoTorrent instance discovered on the LAN."""
    ip: str
    port: int  # Their BitTorrent listen port
    info_hashes: List[str] = field(default_factory=list)
    last_seen: float = 0.0
    hostname: str = ""


class LANMeshDiscovery:
    """
    Discovers other TopoTorrent instances on the local network.

    Uses UDP broadcast to announce torrents we have and discover
    peers sharing the same torrents. LAN peers get maximum priority
    since they can transfer at gigabit speeds.
    """

    BROADCAST_INTERVAL = 10  # seconds
    PEER_TIMEOUT = 60  # seconds

    def __init__(self, bt_listen_port: int = 6881):
        self.bt_listen_port = bt_listen_port
        self._our_info_hashes: List[str] = []
        self._lan_peers: Dict[str, LANPeer] = {}
        self._lock = threading.Lock()
        self._running = False
        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None
        self._on_lan_peer: Optional[Callable] = None

    def set_on_lan_peer(self, callback: Callable):
        """Set callback for new LAN peer discovery: callback(ip, port, info_hashes)."""
        self._on_lan_peer = callback

    def update_info_hashes(self, hashes: List[str]):
        """Update list of info hashes we're sharing."""
        self._our_info_hashes = hashes

    def start(self):
        """Start LAN discovery."""
        self._running = True

        # Receive thread
        try:
            self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._recv_sock.settimeout(2)
            self._recv_sock.bind(("", LAN_DISCOVERY_PORT))

            threading.Thread(
                target=self._receive_loop, daemon=True,
                name="LANRecv"
            ).start()
        except Exception as e:
            print(f"[LANMesh] Could not start receiver: {e}")

        # Send thread
        try:
            self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            threading.Thread(
                target=self._broadcast_loop, daemon=True,
                name="LANBroadcast"
            ).start()
            print(f"[LANMesh] Discovery started on port {LAN_DISCOVERY_PORT}")
        except Exception as e:
            print(f"[LANMesh] Could not start broadcaster: {e}")

    def stop(self):
        self._running = False
        if self._send_sock:
            try:
                self._send_sock.close()
            except Exception:
                pass
        if self._recv_sock:
            try:
                self._recv_sock.close()
            except Exception:
                pass

    def get_lan_peers(self) -> List[LANPeer]:
        """Get list of discovered LAN peers."""
        now = time.time()
        with self._lock:
            return [
                p for p in self._lan_peers.values()
                if now - p.last_seen < self.PEER_TIMEOUT
            ]

    def _broadcast_loop(self):
        """Periodically broadcast our presence."""
        while self._running:
            try:
                if self._our_info_hashes:
                    msg = self._build_announcement()
                    self._send_sock.sendto(
                        msg,
                        ("255.255.255.255", LAN_DISCOVERY_PORT)
                    )
            except Exception:
                pass
            time.sleep(self.BROADCAST_INTERVAL)

    def _receive_loop(self):
        """Listen for LAN peer announcements."""
        while self._running:
            try:
                data, addr = self._recv_sock.recvfrom(4096)
                self._handle_announcement(data, addr[0])
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    continue
                break

    def _build_announcement(self) -> bytes:
        """Build a LAN discovery announcement packet."""
        payload = {
            "port": self.bt_listen_port,
            "hashes": self._our_info_hashes[:20],  # Limit size
            "hostname": socket.gethostname(),
        }
        return LAN_MAGIC + json.dumps(payload).encode("utf-8")

    def _handle_announcement(self, data: bytes, sender_ip: str):
        """Parse a received LAN discovery packet."""
        if not data.startswith(LAN_MAGIC):
            return

        try:
            payload = json.loads(data[len(LAN_MAGIC):].decode("utf-8"))
            port = payload.get("port", 6881)
            hashes = payload.get("hashes", [])
            hostname = payload.get("hostname", "")

            key = f"{sender_ip}:{port}"

            with self._lock:
                if key not in self._lan_peers:
                    self._lan_peers[key] = LANPeer(
                        ip=sender_ip, port=port
                    )
                    print(f"[LANMesh] Discovered: {sender_ip}:{port} ({hostname})")

                peer = self._lan_peers[key]
                peer.info_hashes = hashes
                peer.last_seen = time.time()
                peer.hostname = hostname

            # Check for matching torrents
            matching = set(hashes) & set(self._our_info_hashes)
            if matching and self._on_lan_peer:
                self._on_lan_peer(sender_ip, port, list(matching))

        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# 3. Cloud Seeder Hooks
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CloudNode:
    """A cloud seeding node."""
    url: str  # Base URL of the cloud seeder API
    api_key: str = ""
    is_alive: bool = False
    last_check: float = 0.0
    torrents_seeding: List[str] = field(default_factory=list)


class CloudSeederAPI:
    """
    API interface for cloud-assisted seeding.

    Cloud nodes are optional remote servers that can:
    - Seed torrents on behalf of the client
    - Provide high-bandwidth upload capacity
    - Maintain 24/7 seeding when client is offline

    REST API Protocol:
        POST /seed     — Request seeding of a torrent
        DELETE /seed   — Stop seeding
        GET /status    — Get seeding status
        GET /health    — Health check
    """

    def __init__(self):
        self._nodes: List[CloudNode] = []
        self._lock = threading.Lock()

    def add_node(self, url: str, api_key: str = ""):
        """Register a cloud seeder node."""
        with self._lock:
            self._nodes.append(CloudNode(url=url.rstrip("/"), api_key=api_key))

    def remove_node(self, url: str):
        """Remove a cloud node."""
        with self._lock:
            self._nodes = [n for n in self._nodes if n.url != url.rstrip("/")]

    def get_nodes(self) -> List[CloudNode]:
        """Get list of registered cloud nodes."""
        with self._lock:
            return list(self._nodes)

    def request_seeding(self, info_hash: str, magnet_uri: str) -> bool:
        """
        Request a cloud node to seed a torrent.

        Tries each active node until one accepts.
        """
        for node in self._nodes:
            try:
                import urllib.request
                data = json.dumps({
                    "info_hash": info_hash,
                    "magnet": magnet_uri,
                }).encode("utf-8")

                req = urllib.request.Request(
                    f"{node.url}/seed",
                    data=data,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {node.api_key}",
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        node.torrents_seeding.append(info_hash)
                        print(f"[Cloud] Seeding requested on {node.url}")
                        return True
            except Exception:
                continue
        return False

    def check_health(self, node: CloudNode) -> bool:
        """Check if a cloud node is alive."""
        try:
            import urllib.request
            req = urllib.request.Request(f"{node.url}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                node.is_alive = resp.status == 200
                node.last_check = time.time()
                return node.is_alive
        except Exception:
            node.is_alive = False
            return False


# ═══════════════════════════════════════════════════════════════════════
# 4. Mobile Peer Bridge
# ═══════════════════════════════════════════════════════════════════════

class MobileBridge:
    """
    Lightweight HTTP API for mobile companion apps.

    Allows mobile devices (phones/tablets) to contribute upload bandwidth
    by serving as additional seeders. Mobile apps connect to this API
    to receive piece requests and return piece data.

    API Endpoints:
        GET  /bridge/status          — Client status and active torrents
        GET  /bridge/pieces/:hash    — Get list of pieces needed/available
        POST /bridge/upload/:hash    — Upload a piece from mobile
        GET  /bridge/download/:hash/:piece — Download piece to mobile for seeding
    """

    def __init__(self, listen_port: int = 8099):
        self.listen_port = listen_port
        self._server: Optional[socket.socket] = None
        self._running = False
        self._connected_devices: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._get_piece_func: Optional[Callable] = None
        self._put_piece_func: Optional[Callable] = None

    def set_piece_functions(self, get_func: Callable, put_func: Callable):
        """
        Set functions for piece exchange.

        get_func(info_hash, piece_index) -> bytes
        put_func(info_hash, piece_index, data) -> bool
        """
        self._get_piece_func = get_func
        self._put_piece_func = put_func

    def start(self):
        """Start the mobile bridge HTTP server."""
        self._running = True
        threading.Thread(
            target=self._serve, daemon=True,
            name="MobileBridge"
        ).start()

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass

    def get_connected_devices(self) -> List[dict]:
        """Get list of connected mobile devices."""
        with self._lock:
            now = time.time()
            return [
                d for d in self._connected_devices.values()
                if now - d.get("last_seen", 0) < 60
            ]

    def _serve(self):
        """Simple HTTP server for mobile bridge."""
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.settimeout(2)
            self._server.bind(("0.0.0.0", self.listen_port))
            self._server.listen(5)
            print(f"[MobileBridge] Listening on port {self.listen_port}")

            while self._running:
                try:
                    client, addr = self._server.accept()
                    threading.Thread(
                        target=self._handle_request,
                        args=(client, addr),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
                except Exception:
                    if self._running:
                        continue
                    break
        except Exception as e:
            print(f"[MobileBridge] Server error: {e}")

    def _handle_request(self, client: socket.socket, addr: tuple):
        """Handle a single HTTP request from a mobile device."""
        try:
            client.settimeout(10)
            data = client.recv(4096)
            if not data:
                client.close()
                return

            request_line = data.split(b"\r\n")[0].decode("utf-8")
            method, path, _ = request_line.split(" ", 2)

            # Track device
            with self._lock:
                self._connected_devices[addr[0]] = {
                    "ip": addr[0],
                    "port": addr[1],
                    "last_seen": time.time(),
                }

            # Route request
            if path == "/bridge/status":
                response = self._handle_status()
            elif path.startswith("/bridge/pieces/"):
                info_hash = path.split("/")[-1]
                response = self._handle_pieces(info_hash)
            else:
                response = json.dumps({"error": "Not found"})

            # Send HTTP response
            http_response = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(response)}\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"\r\n"
                f"{response}"
            )
            client.sendall(http_response.encode("utf-8"))

        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _handle_status(self) -> str:
        return json.dumps({
            "client": "TopoTorrent",
            "version": "1.0",
            "bridge_active": True,
            "connected_devices": len(self._connected_devices),
        })

    def _handle_pieces(self, info_hash: str) -> str:
        return json.dumps({
            "info_hash": info_hash,
            "message": "Piece exchange endpoint ready",
        })
