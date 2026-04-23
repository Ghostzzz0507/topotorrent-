"""
Peer Reputation & Incentive System for TopoTorrent.

Maintains persistent per-peer reputation scores that survive restarts.
Rewards generous uploaders and penalizes free-riders and bad actors.

Reputation factors:
- Upload generosity: ratio of uploaded vs downloaded
- Reliability: successful connection rate, session duration
- Speed consistency: stable throughput over time
- Protocol compliance: no corrupt data, proper handshake behavior

Integration:
- Unchoke decisions weighted by reputation
- Connection priority based on reputation
- Bad peers blocked after repeated violations
"""

import json
import os
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from pathlib import Path


CONFIG_DIR = str(Path.home() / ".topotorrent")
REPUTATION_FILE = os.path.join(CONFIG_DIR, "peer_reputation.json")


@dataclass
class PeerReputation:
    """Persistent reputation data for a single peer."""
    # Identity
    ip: str
    port: int

    # Upload/download tracking
    total_uploaded_to_us: int = 0  # Bytes they uploaded to us
    total_downloaded_from_us: int = 0  # Bytes they downloaded from us
    generosity_ratio: float = 0.0  # upload / download

    # Reliability
    successful_connections: int = 0
    failed_connections: int = 0
    total_sessions: int = 0
    total_session_time: float = 0.0  # seconds
    avg_session_duration: float = 0.0

    # Bad behavior
    corrupt_pieces: int = 0  # Sent bad data
    protocol_violations: int = 0  # Bad handshakes, invalid messages
    times_banned: int = 0

    # Speed
    peak_upload_speed: float = 0.0
    avg_upload_speed: float = 0.0
    speed_samples: int = 0

    # Computed
    score: float = 0.5  # 0..1
    last_seen: float = 0.0
    first_seen: float = 0.0
    is_banned: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PeerReputation":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ReputationManager:
    """
    Manages peer reputations with disk persistence.
    """

    # Scoring weights
    GENEROSITY_WEIGHT = 0.30
    RELIABILITY_WEIGHT = 0.25
    SPEED_WEIGHT = 0.20
    COMPLIANCE_WEIGHT = 0.25

    # Ban thresholds
    MAX_CORRUPT_PIECES = 5
    MAX_PROTOCOL_VIOLATIONS = 10
    BAN_DURATION = 3600  # 1 hour

    def __init__(self, persistence_path: str = REPUTATION_FILE):
        self._peers: Dict[str, PeerReputation] = {}
        self._lock = threading.Lock()
        self._persistence_path = persistence_path
        self._dirty = False
        self._load()

        # Start periodic save thread
        self._running = True
        self._save_thread = threading.Thread(
            target=self._auto_save_loop, daemon=True, name="ReputationSaver"
        )
        self._save_thread.start()

    def stop(self):
        self._running = False
        self._save()

    def _key(self, ip: str, port: int) -> str:
        return f"{ip}:{port}"

    def _get_or_create(self, ip: str, port: int) -> PeerReputation:
        key = self._key(ip, port)
        if key not in self._peers:
            self._peers[key] = PeerReputation(
                ip=ip, port=port,
                first_seen=time.time(),
                last_seen=time.time(),
            )
        return self._peers[key]

    # ═══ Event Recording ═══════════════════════════════════════════════

    def record_connection(self, ip: str, port: int, success: bool):
        """Record a connection attempt."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.last_seen = time.time()
            if success:
                rep.successful_connections += 1
                rep.total_sessions += 1
            else:
                rep.failed_connections += 1
            self._dirty = True

    def record_disconnection(self, ip: str, port: int, session_duration: float):
        """Record session end."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.total_session_time += session_duration
            if rep.total_sessions > 0:
                rep.avg_session_duration = rep.total_session_time / rep.total_sessions
            self._dirty = True

    def record_upload(self, ip: str, port: int, bytes_uploaded: int):
        """Record bytes they uploaded to us."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.total_uploaded_to_us += bytes_uploaded
            total = rep.total_uploaded_to_us + rep.total_downloaded_from_us
            if total > 0:
                rep.generosity_ratio = rep.total_uploaded_to_us / total
            self._dirty = True

    def record_download(self, ip: str, port: int, bytes_downloaded: int):
        """Record bytes they downloaded from us."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.total_downloaded_from_us += bytes_downloaded
            total = rep.total_uploaded_to_us + rep.total_downloaded_from_us
            if total > 0:
                rep.generosity_ratio = rep.total_uploaded_to_us / total
            self._dirty = True

    def record_speed(self, ip: str, port: int, upload_speed: float):
        """Record upload speed sample."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            if upload_speed > rep.peak_upload_speed:
                rep.peak_upload_speed = upload_speed
            # Running average
            rep.speed_samples += 1
            rep.avg_upload_speed += (upload_speed - rep.avg_upload_speed) / rep.speed_samples
            self._dirty = True

    def record_corrupt_piece(self, ip: str, port: int):
        """Record that peer sent corrupt data."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.corrupt_pieces += 1
            if rep.corrupt_pieces >= self.MAX_CORRUPT_PIECES:
                rep.is_banned = True
                rep.times_banned += 1
            self._dirty = True

    def record_protocol_violation(self, ip: str, port: int):
        """Record a protocol violation."""
        with self._lock:
            rep = self._get_or_create(ip, port)
            rep.protocol_violations += 1
            if rep.protocol_violations >= self.MAX_PROTOCOL_VIOLATIONS:
                rep.is_banned = True
                rep.times_banned += 1
            self._dirty = True

    # ═══ Scoring ═══════════════════════════════════════════════════════

    def compute_score(self, ip: str, port: int) -> float:
        """
        Compute reputation score (0..1) for a peer.

        Score = generosity * 0.30 + reliability * 0.25
              + speed * 0.20 + compliance * 0.25
        """
        with self._lock:
            rep = self._get_or_create(ip, port)

            if rep.is_banned:
                rep.score = 0.0
                return 0.0

            # Generosity: how much they upload vs download
            generosity = min(1.0, rep.generosity_ratio * 2)  # 0.5 ratio = full marks

            # Reliability: successful connections / total
            total_conns = rep.successful_connections + rep.failed_connections
            if total_conns > 0:
                reliability = rep.successful_connections / total_conns
            else:
                reliability = 0.5  # Unknown = neutral

            # Adjust by session duration
            if rep.avg_session_duration > 60:
                reliability = min(1.0, reliability * 1.2)  # Bonus for long sessions

            # Speed: normalized against 1 MB/s
            speed_score = min(1.0, rep.avg_upload_speed / 1_000_000)

            # Compliance: penalize bad behavior
            violations = rep.corrupt_pieces + rep.protocol_violations
            if violations == 0:
                compliance = 1.0
            else:
                compliance = max(0.0, 1.0 - violations * 0.1)

            score = (
                generosity * self.GENEROSITY_WEIGHT
                + reliability * self.RELIABILITY_WEIGHT
                + speed_score * self.SPEED_WEIGHT
                + compliance * self.COMPLIANCE_WEIGHT
            )

            rep.score = round(score, 4)
            self._dirty = True
            return rep.score

    def get_score(self, ip: str, port: int) -> float:
        """Get cached score for a peer."""
        with self._lock:
            key = self._key(ip, port)
            rep = self._peers.get(key)
            return rep.score if rep else 0.5

    def is_banned(self, ip: str, port: int) -> bool:
        """Check if a peer is banned."""
        with self._lock:
            key = self._key(ip, port)
            rep = self._peers.get(key)
            if rep and rep.is_banned:
                # Check if ban has expired
                if time.time() - rep.last_seen > self.BAN_DURATION:
                    rep.is_banned = False
                    return False
                return True
            return False

    def get_all_scores(self) -> Dict[str, float]:
        """Get all peer scores."""
        with self._lock:
            return {k: v.score for k, v in self._peers.items()}

    def get_top_peers(self, n: int = 20) -> List[PeerReputation]:
        """Get top N peers by reputation score."""
        with self._lock:
            sorted_peers = sorted(
                self._peers.values(),
                key=lambda p: p.score,
                reverse=True,
            )
            return sorted_peers[:n]

    def get_reputation(self, ip: str, port: int) -> Optional[PeerReputation]:
        """Get full reputation data for a peer."""
        with self._lock:
            return self._peers.get(self._key(ip, port))

    def get_stats(self) -> dict:
        """Get reputation system statistics."""
        with self._lock:
            if not self._peers:
                return {
                    "total_known_peers": 0,
                    "banned_peers": 0,
                    "avg_score": 0.5,
                    "top_score": 0.0,
                }
            scores = [p.score for p in self._peers.values()]
            return {
                "total_known_peers": len(self._peers),
                "banned_peers": sum(1 for p in self._peers.values() if p.is_banned),
                "avg_score": sum(scores) / len(scores),
                "top_score": max(scores),
            }

    # ═══ Persistence ═══════════════════════════════════════════════════

    def _load(self):
        """Load reputation data from disk."""
        try:
            if os.path.exists(self._persistence_path):
                with open(self._persistence_path, "r") as f:
                    data = json.load(f)
                for key, peer_dict in data.items():
                    try:
                        self._peers[key] = PeerReputation.from_dict(peer_dict)
                    except Exception:
                        pass
                print(f"[Reputation] Loaded {len(self._peers)} peer records")
        except Exception:
            pass

    def _save(self):
        """Save reputation data to disk."""
        try:
            os.makedirs(os.path.dirname(self._persistence_path), exist_ok=True)
            with self._lock:
                data = {k: v.to_dict() for k, v in self._peers.items()}
            with open(self._persistence_path, "w") as f:
                json.dump(data, f, indent=1)
        except Exception:
            pass

    def _auto_save_loop(self):
        """Periodically save reputation data."""
        while self._running:
            time.sleep(60)  # Save every minute
            if self._dirty:
                self._save()
                self._dirty = False

    def cleanup_old_peers(self, max_age_days: int = 30):
        """Remove peers not seen in N days."""
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            old_keys = [
                k for k, v in self._peers.items()
                if v.last_seen < cutoff
            ]
            for k in old_keys:
                del self._peers[k]
            if old_keys:
                self._dirty = True
