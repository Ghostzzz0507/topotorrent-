"""
Topology-aware peer scoring engine for TopoTorrent.

Monitors connected peers via libtorrent's peer info API and computes
weighted scores based on latency, throughput, uptime, connection
stability, geographic proximity, and reputation.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# Lazy imports to avoid circular dependencies
_geo_selector = None
_reputation_mgr = None


@dataclass
class PeerMetrics:
    """Real-time network metrics for a single peer."""
    ip: str
    port: int

    # Connection
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # Latency (estimated from download response time)
    latency_ms: float = float("inf")
    latency_samples: List[float] = field(default_factory=list)
    latency_ewma: float = float("inf")

    # Throughput
    throughput_bps: float = 0.0
    last_throughput_bytes: int = 0
    last_throughput_time: float = field(default_factory=time.time)

    # Stability
    connection_count: int = 1
    disconnection_count: int = 0
    consecutive_failures: int = 0

    # Upload/download from libtorrent
    total_downloaded: int = 0
    total_uploaded: int = 0
    download_speed: float = 0.0
    upload_speed: float = 0.0
    progress: float = 0.0

    # Computed
    score: float = 0.0
    rank: int = 0

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.connected_at

    @property
    def stability_ratio(self) -> float:
        total = self.connection_count + self.disconnection_count
        if total == 0:
            return 1.0
        return self.connection_count / total

    @property
    def is_healthy(self) -> bool:
        return (
            self.consecutive_failures < 5
            and self.latency_ms < 5000
            and time.time() - self.last_seen < 120
        )

    @property
    def peer_key(self) -> str:
        return f"{self.ip}:{self.port}"


@dataclass
class TopologyConfig:
    """Configuration for topology scoring."""
    enabled: bool = True
    score_update_interval: float = 2.0

    # Weights (must sum to 1.0)
    latency_weight: float = 0.30
    throughput_weight: float = 0.30
    uptime_weight: float = 0.10
    stability_weight: float = 0.10
    geo_weight: float = 0.10
    reputation_weight: float = 0.10

    # Normalization
    max_latency_ms: float = 1000.0
    max_throughput_bps: float = 10_000_000.0  # 10 MB/s
    max_uptime_seconds: float = 3600.0

    # EWMA
    ewma_alpha: float = 0.3
    max_latency_samples: int = 20

    # Peer scoring thresholds
    min_score_threshold: float = 0.1


class TopologyEngine:
    """
    Topology-aware peer ranking engine.

    Reads peer data from libtorrent handles, maintains per-peer metrics,
    and computes weighted scores. The engine runs in a background thread
    and updates scores periodically.

    Scoring formula:
        score = latency_score * 0.35 + throughput_score * 0.35
              + uptime_score * 0.15 + stability_score * 0.15

    All components normalized to [0, 1].
    """

    def __init__(self, config: Optional[TopologyConfig] = None):
        self.config = config or TopologyConfig()
        self._peers: Dict[str, PeerMetrics] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._score_callbacks: List[Callable] = []
        self._get_peer_data_callback: Optional[Callable] = None
        self._geo_selector = None  # Set externally
        self._reputation_mgr = None  # Set externally

    def set_geo_selector(self, geo_selector):
        """Set the GeoPeerSelector for geo-aware scoring."""
        self._geo_selector = geo_selector

    def set_reputation_manager(self, reputation_mgr):
        """Set the ReputationManager for reputation-aware scoring."""
        self._reputation_mgr = reputation_mgr

    def start(self):
        """Start the background scoring thread."""
        if not self.config.enabled:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._score_loop, daemon=True, name="TopologyEngine"
        )
        self._thread.start()

    def stop(self):
        """Stop the background scoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def set_peer_data_callback(self, callback: Callable):
        """Register callback that returns list of (torrent_id, peer_info_list)."""
        self._get_peer_data_callback = callback

    def on_score_update(self, callback: Callable):
        """Register callback for score updates: callback(peer_key, score)."""
        self._score_callbacks.append(callback)

    def update_from_libtorrent(self, peer_infos: list):
        """
        Update internal metrics from libtorrent peer info objects.

        Expected peer_infos: list of dicts with keys:
            ip, port, down_speed, up_speed, total_download, total_upload, progress
        """
        now = time.time()
        seen_keys = set()

        with self._lock:
            for pi in peer_infos:
                ip = str(pi.get("ip", ""))
                port = int(pi.get("port", 0))
                key = f"{ip}:{port}"
                seen_keys.add(key)

                if key not in self._peers:
                    self._peers[key] = PeerMetrics(
                        ip=ip, port=port, connected_at=now
                    )

                peer = self._peers[key]
                peer.last_seen = now

                # Update speeds and totals
                new_dl = pi.get("total_download", 0)
                peer.download_speed = pi.get("down_speed", 0)
                peer.upload_speed = pi.get("up_speed", 0)
                peer.progress = pi.get("progress", 0)

                # Estimate throughput from download speed
                if peer.download_speed > 0:
                    peer.throughput_bps = peer.download_speed
                    # Estimate latency from responsiveness
                    if peer.latency_ms == float("inf"):
                        # Initial estimate from speed — fast peer = low latency assumption
                        speed_mbps = peer.download_speed / 1_000_000
                        estimated_latency = max(10, 200 - speed_mbps * 50)
                        peer.latency_ms = estimated_latency
                        peer.latency_ewma = estimated_latency
                    else:
                        # Update EWMA
                        speed_mbps = peer.download_speed / 1_000_000
                        estimated_latency = max(10, 200 - speed_mbps * 50)
                        alpha = self.config.ewma_alpha
                        peer.latency_ewma = (
                            alpha * estimated_latency
                            + (1 - alpha) * peer.latency_ewma
                        )
                        peer.latency_ms = peer.latency_ewma
                        peer.latency_samples.append(estimated_latency)
                        if len(peer.latency_samples) > self.config.max_latency_samples:
                            peer.latency_samples.pop(0)

                peer.consecutive_failures = 0

                # Update totals
                prev_dl = peer.total_downloaded
                peer.total_downloaded = new_dl
                peer.total_uploaded = pi.get("total_upload", 0)

            # Mark disconnected peers
            stale_keys = set(self._peers.keys()) - seen_keys
            for key in stale_keys:
                peer = self._peers[key]
                if now - peer.last_seen > 60:
                    peer.consecutive_failures += 1
                    peer.disconnection_count += 1

    def get_scores(self) -> Dict[str, float]:
        """Get all peer scores as {ip:port: score}."""
        with self._lock:
            return {k: p.score for k, p in self._peers.items()}

    def get_ranked_peers(self, min_score: float = 0.0) -> List[PeerMetrics]:
        """Get peers ranked by score (highest first)."""
        with self._lock:
            peers = [p for p in self._peers.values() if p.score >= min_score]
            return sorted(peers, key=lambda p: p.score, reverse=True)

    def get_peer_score(self, ip: str, port: int) -> float:
        """Get score for a specific peer."""
        key = f"{ip}:{port}"
        with self._lock:
            peer = self._peers.get(key)
            return peer.score if peer else 0.0

    def get_average_score(self) -> float:
        """Get average topology score across all peers."""
        with self._lock:
            if not self._peers:
                return 0.0
            scores = [p.score for p in self._peers.values()]
            return sum(scores) / len(scores)

    def get_peer_details(self, ip: str, port: int) -> Optional[PeerMetrics]:
        """Get detailed metrics for a peer."""
        key = f"{ip}:{port}"
        with self._lock:
            return self._peers.get(key)

    def get_metrics_summary(self) -> dict:
        """Get summary stats for display."""
        with self._lock:
            if not self._peers:
                return {
                    "total_peers": 0,
                    "avg_score": 0.0,
                    "best_score": 0.0,
                    "worst_score": 0.0,
                    "avg_latency_ms": 0.0,
                    "avg_throughput_mbps": 0.0,
                }

            scores = [p.score for p in self._peers.values()]
            latencies = [
                p.latency_ms
                for p in self._peers.values()
                if p.latency_ms != float("inf")
            ]
            throughputs = [
                p.throughput_bps / 1_000_000
                for p in self._peers.values()
            ]

            return {
                "total_peers": len(self._peers),
                "avg_score": sum(scores) / len(scores),
                "best_score": max(scores),
                "worst_score": min(scores),
                "avg_latency_ms": (
                    sum(latencies) / len(latencies) if latencies else 0
                ),
                "avg_throughput_mbps": (
                    sum(throughputs) / len(throughputs) if throughputs else 0
                ),
            }

    def clear(self):
        """Clear all peer data."""
        with self._lock:
            self._peers.clear()

    def _score_loop(self):
        """Background loop to periodically update scores."""
        while self._running:
            try:
                time.sleep(self.config.score_update_interval)
                if not self._running:
                    break

                # Fetch peer data if callback registered
                if self._get_peer_data_callback:
                    try:
                        peer_data = self._get_peer_data_callback()
                        if peer_data:
                            self.update_from_libtorrent(peer_data)
                    except Exception:
                        pass

                self._update_all_scores()

            except Exception:
                pass

    def _update_all_scores(self):
        """Recalculate scores for all peers."""
        with self._lock:
            ranked = []
            for peer_key, peer in self._peers.items():
                old_score = peer.score
                peer.score = self._calculate_score(peer)
                ranked.append((peer_key, peer.score))

                if abs(peer.score - old_score) > 0.01:
                    for cb in self._score_callbacks:
                        try:
                            cb(peer_key, peer.score)
                        except Exception:
                            pass

            # Assign ranks
            ranked.sort(key=lambda x: x[1], reverse=True)
            for rank, (key, _) in enumerate(ranked, 1):
                if key in self._peers:
                    self._peers[key].rank = rank

    def _calculate_score(self, peer: PeerMetrics) -> float:
        """
        Calculate topology score for a peer.

        Score = latency * w1 + throughput * w2 + uptime * w3
              + stability * w4 + geo * w5 + reputation * w6
        """
        cfg = self.config

        # Latency score (inverse — lower latency = higher score)
        if peer.latency_ms == float("inf") or peer.latency_ms <= 0:
            latency_score = 0.0
        else:
            latency_score = 1.0 - min(peer.latency_ms / cfg.max_latency_ms, 1.0)

        # Throughput score
        throughput_score = min(
            peer.throughput_bps / cfg.max_throughput_bps, 1.0
        )

        # Uptime score
        uptime_score = min(
            peer.uptime_seconds / cfg.max_uptime_seconds, 1.0
        )

        # Stability score
        stability_score = peer.stability_ratio
        if peer.consecutive_failures > 0:
            stability_score *= 1.0 / (1 + peer.consecutive_failures)

        # Geo score (from GeoPeerSelector)
        geo_score = 0.5  # Default neutral
        if self._geo_selector:
            try:
                geo_score = self._geo_selector.compute_geo_score(peer.ip, peer.port)
            except Exception:
                pass

        # Reputation score (from ReputationManager)
        reputation_score = 0.5  # Default neutral
        if self._reputation_mgr:
            try:
                reputation_score = self._reputation_mgr.get_score(peer.ip, peer.port)
            except Exception:
                pass

        # Weighted combination
        score = (
            latency_score * cfg.latency_weight
            + throughput_score * cfg.throughput_weight
            + uptime_score * cfg.uptime_weight
            + stability_score * cfg.stability_weight
            + geo_score * cfg.geo_weight
            + reputation_score * cfg.reputation_weight
        )

        return round(score, 4)
