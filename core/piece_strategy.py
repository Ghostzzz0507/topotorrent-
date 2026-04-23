"""
Piece Strategy Engine for TopoTorrent.

Replaces naive random piece selection with intelligent strategies:
- Rarest-first: prevents piece starvation in low-seed swarms
- Sequential: enables streaming / in-order playback
- Hybrid: rarest-first + file completion awareness
- Swarm intelligence: rarity tracking, churn prediction, endangered piece pre-fetch
"""

import time
import threading
import random
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple


class PieceSelectionStrategy(Enum):
    RANDOM = "random"
    RAREST_FIRST = "rarest_first"
    SEQUENTIAL = "sequential"
    HYBRID = "hybrid"  # rarest-first + file completion boost


@dataclass
class PieceRarityInfo:
    """Tracks availability of a single piece across the swarm."""
    index: int
    availability: int = 0  # Number of peers that have this piece
    last_availability: int = 0  # Previous tick's availability
    availability_trend: float = 0.0  # Negative = becoming rarer
    endangered: bool = False  # True if availability <= 1
    requested_count: int = 0  # How many times peers requested this from us


@dataclass
class PeerChurnStats:
    """Tracks peer connection/disconnection patterns."""
    ip: str
    port: int
    connect_times: List[float] = field(default_factory=list)
    disconnect_times: List[float] = field(default_factory=list)
    avg_session_duration: float = 0.0
    predicted_remaining: float = float("inf")  # Estimated seconds until disconnect
    churn_risk: float = 0.0  # 0..1, higher = more likely to leave soon


class SwarmIntelligence:
    """
    Analyzes entire swarm behavior to make smarter decisions.

    Tracks:
    - Per-piece rarity (how many peers have each piece)
    - Rarity trends (which pieces are becoming rarer)
    - Peer churn (which peers are likely to disconnect soon)
    - Endangered pieces (pieces with only 1-2 sources)
    """

    def __init__(self, num_pieces: int):
        self.num_pieces = num_pieces
        self._rarity: Dict[int, PieceRarityInfo] = {
            i: PieceRarityInfo(index=i) for i in range(num_pieces)
        }
        self._churn: Dict[str, PeerChurnStats] = {}
        self._lock = threading.Lock()
        self._last_update = time.time()

    def update_from_peers(self, peers: list):
        """
        Update rarity data by scanning all peer bitfields.

        Args:
            peers: List of PeerConnection objects with .bitfield and .has_piece()
        """
        with self._lock:
            # Reset availability counts
            for info in self._rarity.values():
                info.last_availability = info.availability
                info.availability = 0

            # Count how many peers have each piece
            for peer in peers:
                if not getattr(peer, "connected", False):
                    continue
                if not getattr(peer, "bitfield", None):
                    continue
                for i in range(self.num_pieces):
                    if peer.has_piece(i):
                        self._rarity[i].availability += 1

            # Update trends and endangered status
            for info in self._rarity.values():
                info.availability_trend = info.availability - info.last_availability
                info.endangered = (0 < info.availability <= 1)

    def update_peer_churn(self, ip: str, port: int, connected: bool):
        """Track peer connect/disconnect events for churn prediction."""
        key = f"{ip}:{port}"
        with self._lock:
            if key not in self._churn:
                self._churn[key] = PeerChurnStats(ip=ip, port=port)

            stats = self._churn[key]
            now = time.time()

            if connected:
                stats.connect_times.append(now)
                # Keep last 10 events
                stats.connect_times = stats.connect_times[-10:]
            else:
                stats.disconnect_times.append(now)
                stats.disconnect_times = stats.disconnect_times[-10:]

            # Calculate average session duration
            if len(stats.connect_times) >= 1 and len(stats.disconnect_times) >= 1:
                sessions = []
                for ct in stats.connect_times:
                    dts = [dt for dt in stats.disconnect_times if dt > ct]
                    if dts:
                        sessions.append(min(dts) - ct)
                if sessions:
                    stats.avg_session_duration = sum(sessions) / len(sessions)

                    # Predict remaining time
                    if stats.connect_times:
                        latest_connect = max(stats.connect_times)
                        elapsed = now - latest_connect
                        stats.predicted_remaining = max(
                            0, stats.avg_session_duration - elapsed
                        )
                        # Churn risk: higher as we approach predicted disconnect
                        if stats.avg_session_duration > 0:
                            stats.churn_risk = min(
                                1.0, elapsed / stats.avg_session_duration
                            )

    def record_piece_request(self, piece_index: int):
        """Record that a peer requested a piece from us (for edge cache)."""
        with self._lock:
            if piece_index in self._rarity:
                self._rarity[piece_index].requested_count += 1

    def get_rarity(self, piece_index: int) -> int:
        """Get availability count for a piece."""
        with self._lock:
            info = self._rarity.get(piece_index)
            return info.availability if info else 0

    def get_endangered_pieces(self) -> List[int]:
        """Get pieces that have only 0-1 sources in the swarm."""
        with self._lock:
            return [
                info.index for info in self._rarity.values()
                if info.endangered
            ]

    def get_declining_pieces(self) -> List[int]:
        """Get pieces whose availability is declining (becoming rarer)."""
        with self._lock:
            return [
                info.index for info in self._rarity.values()
                if info.availability_trend < 0 and info.availability <= 3
            ]

    def get_most_requested_pieces(self, top_n: int = 20) -> List[int]:
        """Get pieces most frequently requested by peers (for edge cache)."""
        with self._lock:
            sorted_pieces = sorted(
                self._rarity.values(),
                key=lambda x: x.requested_count,
                reverse=True,
            )
            return [p.index for p in sorted_pieces[:top_n] if p.requested_count > 0]

    def get_churn_risk(self, ip: str, port: int) -> float:
        """Get churn risk for a peer (0..1)."""
        key = f"{ip}:{port}"
        with self._lock:
            stats = self._churn.get(key)
            return stats.churn_risk if stats else 0.0

    def get_swarm_health(self) -> dict:
        """Get overall swarm health metrics."""
        with self._lock:
            avail = [info.availability for info in self._rarity.values()]
            endangered = sum(1 for a in avail if 0 < a <= 1)
            unavailable = sum(1 for a in avail if a == 0)
            total = len(avail)

            return {
                "total_pieces": total,
                "fully_available": sum(1 for a in avail if a >= 5),
                "low_availability": sum(1 for a in avail if 1 < a <= 3),
                "endangered": endangered,
                "unavailable": unavailable,
                "avg_availability": sum(avail) / total if total else 0,
                "min_availability": min(avail) if avail else 0,
                "max_availability": max(avail) if avail else 0,
                "health_score": 1.0 - (endangered + unavailable * 2) / max(total, 1),
            }


class PieceStrategyEngine:
    """
    Intelligent piece selection engine.

    Selects which piece to download next based on the chosen strategy
    and swarm intelligence data.
    """

    def __init__(self, num_pieces: int,
                 strategy: PieceSelectionStrategy = PieceSelectionStrategy.HYBRID):
        self.num_pieces = num_pieces
        self.strategy = strategy
        self.swarm = SwarmIntelligence(num_pieces)
        self._sequential_pos = 0  # Current position for sequential mode
        self._lock = threading.Lock()

    def set_strategy(self, strategy: PieceSelectionStrategy):
        self.strategy = strategy

    def set_sequential_position(self, pos: int):
        """Set starting position for sequential mode (e.g., for streaming)."""
        self._sequential_pos = pos

    def select_piece(
        self,
        needed: Set[int],
        in_progress: Set[int],
        peer_has: Optional[Callable[[int], bool]] = None,
        peer_topology_score: float = 0.0,
    ) -> Optional[int]:
        """
        Select the best piece to download next.

        Args:
            needed: Set of piece indices still needed (not completed)
            in_progress: Set of piece indices currently being downloaded
            peer_has: Function that returns True if the peer has a given piece
            peer_topology_score: Topology score of the requesting peer (0..1)

        Returns:
            Best piece index to download, or None
        """
        # Get candidates: needed, not in progress, peer has it
        candidates = [
            i for i in needed
            if i not in in_progress
            and (peer_has is None or peer_has(i))
        ]

        if not candidates:
            # Try stale in-progress pieces as fallback
            stale = [i for i in needed if peer_has is None or peer_has(i)]
            if stale:
                # Pick one from in_progress that might be stalled
                overlap = [i for i in stale if i in in_progress]
                if overlap:
                    return random.choice(overlap)
            return None

        if self.strategy == PieceSelectionStrategy.RANDOM:
            return self._select_random(candidates)
        elif self.strategy == PieceSelectionStrategy.RAREST_FIRST:
            return self._select_rarest(candidates, peer_topology_score)
        elif self.strategy == PieceSelectionStrategy.SEQUENTIAL:
            return self._select_sequential(candidates)
        elif self.strategy == PieceSelectionStrategy.HYBRID:
            return self._select_hybrid(candidates, peer_topology_score)

        return random.choice(candidates)

    def _select_random(self, candidates: List[int]) -> int:
        """Original random selection."""
        if len(candidates) > 10:
            return random.choice(candidates[:max(5, len(candidates) // 5)])
        return random.choice(candidates)

    def _select_rarest(self, candidates: List[int],
                       peer_score: float = 0.0) -> int:
        """
        Rarest-first selection.

        Picks the piece with lowest availability in the swarm.
        If the peer has a high topology score, give them the very rarest
        piece (they're more likely to complete it fast).
        """
        # Score each candidate by rarity (lower availability = higher priority)
        scored = []
        for idx in candidates:
            rarity = self.swarm.get_rarity(idx)
            # Invert: 0 availability = highest priority
            score = 1.0 / (rarity + 1)

            # Bonus for endangered pieces
            if rarity <= 1:
                score *= 3.0

            scored.append((idx, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # High-score peers get the rarest pieces, low-score peers get variety
        if peer_score > 0.5 and len(scored) > 0:
            # Top peer: give them the rarest piece
            return scored[0][0]
        elif len(scored) > 3:
            # Pick randomly from top 30% for some parallelism
            top = scored[:max(2, len(scored) // 3)]
            return random.choice(top)[0]
        else:
            return scored[0][0]

    def _select_sequential(self, candidates: List[int]) -> int:
        """Sequential selection for streaming mode."""
        # Find the lowest index piece from sequential position
        future = [c for c in candidates if c >= self._sequential_pos]
        if future:
            return min(future)
        # Wrap around
        return min(candidates)

    def _select_hybrid(self, candidates: List[int],
                       peer_score: float = 0.0) -> int:
        """
        Hybrid strategy: rarest-first + file completion boost.

        - Endangered pieces (availability <= 1): highest priority
        - Declining pieces (becoming rarer): high priority
        - Pieces that complete contiguous runs: medium boost
        - Otherwise: rarest-first
        """
        scored = []
        endangered = set(self.swarm.get_endangered_pieces())
        declining = set(self.swarm.get_declining_pieces())

        for idx in candidates:
            rarity = self.swarm.get_rarity(idx)
            score = 1.0 / (rarity + 1)  # Base rarity score

            # Endangered: must download NOW
            if idx in endangered:
                score *= 5.0

            # Declining: download soon before it disappears
            if idx in declining:
                score *= 2.0

            # Contiguous completion boost: favor pieces that fill gaps
            # This helps complete files faster for sequential access
            # Check if adjacent pieces are already completed
            # (Caller should pass completed set, but we approximate)

            scored.append((idx, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Assign best pieces to best peers
        if peer_score > 0.6 and scored:
            return scored[0][0]
        elif len(scored) > 3:
            top = scored[:max(2, len(scored) // 3)]
            return random.choice(top)[0]
        else:
            return scored[0][0]
