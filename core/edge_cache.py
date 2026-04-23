"""
Edge Cache System for TopoTorrent.

Implements a local LRU piece cache for faster serving:
- Frequently requested pieces cached separately from download files
- Faster block serving to peers (read from cache vs scattered files)
- Popular piece detection for pre-loading
- Cache statistics for dashboard
"""

import os
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from pathlib import Path


CONFIG_DIR = str(Path.home() / ".topotorrent")
CACHE_DIR = os.path.join(CONFIG_DIR, "edge_cache")


@dataclass
class CacheStats:
    """Statistics for the edge cache."""
    hits: int = 0
    misses: int = 0
    total_served_bytes: int = 0
    cached_pieces: int = 0
    cache_size_bytes: int = 0
    max_cache_bytes: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


@dataclass
class CachedPiece:
    """A piece stored in the edge cache."""
    info_hash: str
    piece_index: int
    data: bytes
    size: int
    access_count: int = 0
    last_access: float = 0.0
    created: float = 0.0


class EdgeCache:
    """
    LRU piece cache for fast serving.

    Stores frequently-requested pieces in memory and optionally on disk
    for rapid serving to peers. This reduces disk seeks on the actual
    download files and speeds up upload to other peers.
    """

    def __init__(self, max_memory_mb: int = 256, max_disk_mb: int = 1024,
                 cache_dir: str = CACHE_DIR):
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.max_disk_bytes = max_disk_mb * 1024 * 1024
        self.cache_dir = cache_dir
        self._memory_cache: OrderedDict[str, CachedPiece] = OrderedDict()
        self._memory_size = 0
        self._lock = threading.Lock()
        self._stats = CacheStats(max_cache_bytes=self.max_memory_bytes)
        self._request_counts: Dict[str, int] = {}  # Track popularity
        self._running = True

        os.makedirs(cache_dir, exist_ok=True)

        # Start cache maintenance thread
        self._maint_thread = threading.Thread(
            target=self._maintenance_loop, daemon=True,
            name="EdgeCache"
        )
        self._maint_thread.start()

    def stop(self):
        self._running = False

    def _cache_key(self, info_hash: str, piece_index: int) -> str:
        return f"{info_hash}:{piece_index}"

    def get(self, info_hash: str, piece_index: int) -> Optional[bytes]:
        """
        Get a piece from cache.

        Returns piece data if cached, None if not.
        """
        key = self._cache_key(info_hash, piece_index)

        with self._lock:
            if key in self._memory_cache:
                piece = self._memory_cache[key]
                piece.access_count += 1
                piece.last_access = time.time()
                # Move to end (most recently used)
                self._memory_cache.move_to_end(key)
                self._stats.hits += 1
                self._stats.total_served_bytes += piece.size
                return piece.data
            else:
                self._stats.misses += 1

        # Try disk cache
        disk_data = self._read_from_disk(info_hash, piece_index)
        if disk_data is not None:
            # Promote to memory cache
            self.put(info_hash, piece_index, disk_data)
            with self._lock:
                self._stats.hits += 1
                self._stats.misses -= 1  # Correct the miss we counted
            return disk_data

        return None

    def put(self, info_hash: str, piece_index: int, data: bytes):
        """
        Store a piece in cache.

        Automatically evicts least-recently-used pieces if cache is full.
        """
        key = self._cache_key(info_hash, piece_index)
        size = len(data)

        with self._lock:
            # Evict if necessary
            while self._memory_size + size > self.max_memory_bytes and self._memory_cache:
                self._evict_one()

            # Store
            self._memory_cache[key] = CachedPiece(
                info_hash=info_hash,
                piece_index=piece_index,
                data=data,
                size=size,
                access_count=1,
                last_access=time.time(),
                created=time.time(),
            )
            self._memory_size += size
            self._stats.cached_pieces = len(self._memory_cache)
            self._stats.cache_size_bytes = self._memory_size

    def record_request(self, info_hash: str, piece_index: int):
        """Record that a piece was requested (for popularity tracking)."""
        key = self._cache_key(info_hash, piece_index)
        with self._lock:
            self._request_counts[key] = self._request_counts.get(key, 0) + 1

    def get_popular_pieces(self, info_hash: str, top_n: int = 20) -> List[int]:
        """Get most frequently requested pieces for a torrent."""
        prefix = f"{info_hash}:"
        with self._lock:
            relevant = {
                k: v for k, v in self._request_counts.items()
                if k.startswith(prefix)
            }
        sorted_pieces = sorted(relevant.items(), key=lambda x: x[1], reverse=True)
        return [
            int(k.split(":")[1])
            for k, _ in sorted_pieces[:top_n]
        ]

    def preload_pieces(self, info_hash: str, piece_indices: List[int],
                       read_func):
        """
        Pre-load popular pieces into cache.

        Args:
            info_hash: Torrent info hash
            piece_indices: List of piece indices to preload
            read_func: Function(piece_index) -> bytes
        """
        for idx in piece_indices:
            key = self._cache_key(info_hash, idx)
            with self._lock:
                if key in self._memory_cache:
                    continue  # Already cached

            try:
                data = read_func(idx)
                if data:
                    self.put(info_hash, idx, data)
            except Exception:
                pass

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        with self._lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                total_served_bytes=self._stats.total_served_bytes,
                cached_pieces=len(self._memory_cache),
                cache_size_bytes=self._memory_size,
                max_cache_bytes=self.max_memory_bytes,
            )

    def clear(self):
        """Clear all cached data."""
        with self._lock:
            self._memory_cache.clear()
            self._memory_size = 0
            self._stats = CacheStats(max_cache_bytes=self.max_memory_bytes)

    # ═══ Internal ═════════════════════════════════════════════════════

    def _evict_one(self):
        """Evict the least recently used piece from memory cache."""
        if not self._memory_cache:
            return

        # Pop the first item (LRU)
        key, piece = self._memory_cache.popitem(last=False)
        self._memory_size -= piece.size

        # Optionally write to disk if frequently accessed
        if piece.access_count >= 3:
            self._write_to_disk(piece)

    def _write_to_disk(self, piece: CachedPiece):
        """Write a piece to disk cache for later retrieval."""
        try:
            piece_dir = os.path.join(self.cache_dir, piece.info_hash[:16])
            os.makedirs(piece_dir, exist_ok=True)
            path = os.path.join(piece_dir, f"piece_{piece.piece_index}.cache")
            with open(path, "wb") as f:
                f.write(piece.data)
        except Exception:
            pass

    def _read_from_disk(self, info_hash: str, piece_index: int) -> Optional[bytes]:
        """Read a piece from disk cache."""
        try:
            path = os.path.join(
                self.cache_dir, info_hash[:16],
                f"piece_{piece_index}.cache"
            )
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return f.read()
        except Exception:
            pass
        return None

    def _maintenance_loop(self):
        """Periodic cache maintenance."""
        while self._running:
            time.sleep(60)

            # Clean up old disk cache files
            try:
                total_disk = 0
                cache_files = []
                for root, dirs, files in os.walk(self.cache_dir):
                    for f in files:
                        path = os.path.join(root, f)
                        stat = os.stat(path)
                        total_disk += stat.st_size
                        cache_files.append((path, stat.st_mtime, stat.st_size))

                # If disk cache exceeds limit, remove oldest
                if total_disk > self.max_disk_bytes:
                    cache_files.sort(key=lambda x: x[1])  # Oldest first
                    while total_disk > self.max_disk_bytes * 0.8 and cache_files:
                        path, _, size = cache_files.pop(0)
                        try:
                            os.remove(path)
                            total_disk -= size
                        except Exception:
                            pass
            except Exception:
                pass
