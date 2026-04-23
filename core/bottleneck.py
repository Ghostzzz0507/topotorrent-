"""
Bottleneck Detection for TopoTorrent.

Analyzes the current download state and produces human-readable
diagnostic messages explaining WHY the download is slow.

Messages help users understand what's limiting their speed and
what actions they can take to improve it.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Bottleneck:
    """A detected bottleneck with severity and suggested action."""
    severity: str  # "info", "warning", "critical"
    icon: str  # Emoji icon for display
    message: str  # Human-readable description
    suggestion: str  # What the user can do
    metric_name: str = ""  # Which metric triggered this
    metric_value: float = 0.0


class BottleneckDetector:
    """
    Detects and explains performance bottlenecks.

    Produces clear, actionable messages for the GUI dashboard.
    """

    def __init__(self):
        self._last_analysis: List[Bottleneck] = []
        self._analysis_time = 0.0

    def analyze(self, status: dict, peers: list = None,
                swarm_health: dict = None,
                topology_summary: dict = None) -> List[Bottleneck]:
        """
        Analyze current state and detect bottlenecks.

        Args:
            status: Torrent status dict (from get_status())
            peers: List of connected peers
            swarm_health: Swarm health dict from SwarmIntelligence
            topology_summary: TopologyEngine metrics summary

        Returns:
            List of detected bottlenecks, sorted by severity.
        """
        bottlenecks = []
        now = time.time()
        self._analysis_time = now

        dl_speed = status.get("download_speed", 0)
        ul_speed = status.get("upload_speed", 0)
        progress = status.get("progress", 0)
        num_seeds = status.get("num_seeds", 0)
        num_peers = status.get("num_peers", 0)
        state = status.get("state", "")

        # Skip analysis for completed/paused torrents
        if progress >= 1.0 or "Paused" in state or "Seeding" in state:
            self._last_analysis = []
            return []

        # === Seed Availability ===
        if num_seeds == 0:
            bottlenecks.append(Bottleneck(
                severity="critical",
                icon="🔴",
                message="No complete seeds available in swarm",
                suggestion="Wait for seeders to come online, or add more trackers",
                metric_name="seeds",
                metric_value=0,
            ))
        elif num_seeds <= 2:
            bottlenecks.append(Bottleneck(
                severity="warning",
                icon="🟡",
                message=f"Very few seeds available ({num_seeds})",
                suggestion="Add more trackers to discover additional seeds",
                metric_name="seeds",
                metric_value=num_seeds,
            ))

        # === Peer Count ===
        if num_peers == 0 and progress < 1.0:
            bottlenecks.append(Bottleneck(
                severity="critical",
                icon="🔴",
                message="No peers connected",
                suggestion="Check your internet connection and firewall settings",
                metric_name="peers",
                metric_value=0,
            ))
        elif num_peers <= 3 and progress < 1.0:
            bottlenecks.append(Bottleneck(
                severity="warning",
                icon="🟡",
                message=f"Very few peers connected ({num_peers})",
                suggestion="Enable DHT and PEX for better peer discovery",
                metric_name="peers",
                metric_value=num_peers,
            ))

        # === Download Speed ===
        if num_peers > 0 and dl_speed == 0 and progress < 1.0:
            bottlenecks.append(Bottleneck(
                severity="critical",
                icon="🔴",
                message="Connected to peers but download speed is zero",
                suggestion="Peers may be choking you — try force re-announce",
                metric_name="dl_speed",
                metric_value=0,
            ))
        elif num_peers > 0 and dl_speed < 10 * 1024 and progress < 1.0:
            bottlenecks.append(Bottleneck(
                severity="warning",
                icon="🟡",
                message=f"Very slow download speed ({dl_speed / 1024:.1f} KB/s)",
                suggestion="Most peers may be slow — consider adding HTTP mirrors",
                metric_name="dl_speed",
                metric_value=dl_speed,
            ))

        # === Choking Analysis ===
        if peers:
            connected = [p for p in peers if getattr(p, "connected", True)]
            choking = sum(1 for p in connected
                         if "C" in str(getattr(p, "flags", "")))
            if connected and choking / max(len(connected), 1) > 0.8:
                bottlenecks.append(Bottleneck(
                    severity="warning",
                    icon="⚠️",
                    message=f"Most peers are choking you ({choking}/{len(connected)})",
                    suggestion="You may need to upload more to trigger reciprocation",
                    metric_name="choke_ratio",
                    metric_value=choking / max(len(connected), 1),
                ))

            # Slow peer analysis
            slow_peers = sum(1 for p in connected
                             if getattr(p, "download_speed", 0) < 50 * 1024
                             and getattr(p, "download_speed", 0) > 0)
            if connected and slow_peers / max(len(connected), 1) > 0.7:
                bottlenecks.append(Bottleneck(
                    severity="info",
                    icon="📊",
                    message=f"Most peers are slow (<50 KB/s): {slow_peers}/{len(connected)}",
                    suggestion="This torrent's swarm has limited bandwidth",
                    metric_name="slow_peers_ratio",
                    metric_value=slow_peers / max(len(connected), 1),
                ))

        # === Swarm Health ===
        if swarm_health:
            endangered = swarm_health.get("endangered", 0)
            unavailable = swarm_health.get("unavailable", 0)
            total = swarm_health.get("total_pieces", 1)

            if unavailable > 0:
                bottlenecks.append(Bottleneck(
                    severity="critical",
                    icon="🔴",
                    message=f"{unavailable} pieces have NO sources in swarm",
                    suggestion="Some pieces may be permanently lost — try adding trackers",
                    metric_name="unavailable_pieces",
                    metric_value=unavailable,
                ))

            if endangered > 0:
                bottlenecks.append(Bottleneck(
                    severity="warning",
                    icon="⚠️",
                    message=f"{endangered} pieces are endangered (only 1 source)",
                    suggestion="Download may stall if those seeders leave",
                    metric_name="endangered_pieces",
                    metric_value=endangered,
                ))

            avg_avail = swarm_health.get("avg_availability", 0)
            if 0 < avg_avail < 2:
                bottlenecks.append(Bottleneck(
                    severity="info",
                    icon="📉",
                    message=f"Low swarm health — avg piece availability: {avg_avail:.1f}",
                    suggestion="Stay connected to maintain swarm health by seeding",
                    metric_name="avg_availability",
                    metric_value=avg_avail,
                ))

        # === Topology Quality ===
        if topology_summary:
            avg_score = topology_summary.get("avg_score", 0)
            if avg_score < 0.2 and num_peers > 5:
                bottlenecks.append(Bottleneck(
                    severity="info",
                    icon="📡",
                    message=f"Low average peer quality score: {avg_score:.2f}",
                    suggestion="Connected peers have poor latency/throughput",
                    metric_name="topo_score",
                    metric_value=avg_score,
                ))

        # === ISP Throttling Detection ===
        if (num_peers > 10 and dl_speed < 100 * 1024
                and num_seeds > 3 and progress < 1.0):
            bottlenecks.append(Bottleneck(
                severity="warning",
                icon="🛡️",
                message="Possible ISP throttling detected",
                suggestion="Enable protocol encryption in Settings → Privacy",
                metric_name="throttle_suspect",
                metric_value=1.0,
            ))

        # Sort by severity
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        bottlenecks.sort(key=lambda b: severity_order.get(b.severity, 3))

        self._last_analysis = bottlenecks
        return bottlenecks

    def get_primary_message(self) -> str:
        """Get the most important bottleneck message for status bar display."""
        if not self._last_analysis:
            return ""
        b = self._last_analysis[0]
        return f"{b.icon} {b.message}"

    def get_all_messages(self) -> List[str]:
        """Get all bottleneck messages."""
        return [f"{b.icon} {b.message}" for b in self._last_analysis]

    def get_suggestion(self) -> str:
        """Get the top suggestion."""
        if not self._last_analysis:
            return ""
        return self._last_analysis[0].suggestion

    def has_critical(self) -> bool:
        """Check if there are any critical bottlenecks."""
        return any(b.severity == "critical" for b in self._last_analysis)
