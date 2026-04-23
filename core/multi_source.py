"""
Multi-Source Download Engine for TopoTorrent.

Downloads from multiple sources simultaneously:
1. BitTorrent (primary) — standard peer wire protocol
2. HTTP/HTTPS mirrors — web seeds (BEP 19), direct HTTP mirrors
3. IPFS gateways — fetch via public IPFS HTTP gateways

When BitTorrent is slow, automatically engages HTTP mirrors
for parallel downloading of the same pieces.
"""

import hashlib
import os
import struct
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class HTTPMirror:
    """An HTTP/HTTPS source for a torrent's files."""
    url: str
    is_web_seed: bool = False  # BEP 19 web seed
    is_ipfs: bool = False
    speed_bps: float = 0.0
    bytes_downloaded: int = 0
    errors: int = 0
    last_error: str = ""
    active: bool = True
    last_used: float = 0.0


@dataclass
class DownloadChunk:
    """A chunk to be downloaded from an HTTP source."""
    file_path: str
    file_offset: int
    length: int
    piece_index: int
    piece_offset: int
    url: str = ""


class MultiSourceEngine:
    """
    Manages multi-source downloading alongside BitTorrent.

    Automatically detects when BT is slow and engages HTTP sources.
    Supports BEP 19 web seeds and manual HTTP mirrors.
    """

    # Speed threshold: if BT is below this, engage HTTP mirrors (50 KB/s)
    BT_SLOW_THRESHOLD = 50 * 1024
    # Minimum time between HTTP download attempts
    RETRY_INTERVAL = 30

    def __init__(self, piece_length: int, total_length: int,
                 files: List[Tuple[str, int]]):
        self.piece_length = piece_length
        self.total_length = total_length
        self.files = files
        self._mirrors: List[HTTPMirror] = []
        self._ipfs_gateways: List[str] = [
            "https://ipfs.io/ipfs/",
            "https://dweb.link/ipfs/",
            "https://cloudflare-ipfs.com/ipfs/",
            "https://gateway.pinata.cloud/ipfs/",
        ]
        self._ipfs_cid: Optional[str] = None
        self._lock = threading.Lock()
        self._running = False
        self._download_threads: List[threading.Thread] = []
        self._bt_speed = 0.0
        self._on_piece_data: Optional[Callable] = None

    def add_web_seeds(self, urls: List[str]):
        """Add web seed URLs from torrent metadata (BEP 19 url-list)."""
        with self._lock:
            for url in urls:
                url = url.strip()
                if url and not any(m.url == url for m in self._mirrors):
                    self._mirrors.append(HTTPMirror(
                        url=url, is_web_seed=True
                    ))
                    print(f"[MultiSource] Added web seed: {url}")

    def add_http_mirror(self, url: str):
        """Manually add an HTTP mirror URL."""
        with self._lock:
            if not any(m.url == url for m in self._mirrors):
                self._mirrors.append(HTTPMirror(url=url))
                print(f"[MultiSource] Added HTTP mirror: {url}")

    def set_ipfs_cid(self, cid: str):
        """Set IPFS CID for this torrent's content."""
        self._ipfs_cid = cid

    def set_piece_callback(self, callback: Callable):
        """Set callback: callback(piece_index, offset, data)."""
        self._on_piece_data = callback

    def update_bt_speed(self, speed_bps: float):
        """Update current BitTorrent download speed."""
        self._bt_speed = speed_bps

    def start(self):
        """Start the multi-source download manager."""
        self._running = True
        t = threading.Thread(
            target=self._manager_loop, daemon=True,
            name="MultiSourceManager"
        )
        t.start()
        self._download_threads.append(t)

    def stop(self):
        """Stop all multi-source downloads."""
        self._running = False
        for t in self._download_threads:
            t.join(timeout=5)

    def get_mirrors(self) -> List[HTTPMirror]:
        """Get list of configured mirrors."""
        with self._lock:
            return list(self._mirrors)

    def get_stats(self) -> dict:
        """Get multi-source statistics."""
        with self._lock:
            total_dl = sum(m.bytes_downloaded for m in self._mirrors)
            active = sum(1 for m in self._mirrors if m.active)
            return {
                "total_mirrors": len(self._mirrors),
                "active_mirrors": active,
                "http_bytes_downloaded": total_dl,
                "ipfs_available": self._ipfs_cid is not None,
                "bt_speed": self._bt_speed,
                "http_active": self._bt_speed < self.BT_SLOW_THRESHOLD and len(self._mirrors) > 0,
            }

    # ═══ Internal ═════════════════════════════════════════════════════

    def _manager_loop(self):
        """Monitor BT speed and engage HTTP sources when needed."""
        while self._running:
            try:
                time.sleep(5)

                if not self._mirrors and not self._ipfs_cid:
                    continue

                # Check if BT is slow enough to warrant HTTP downloads
                if self._bt_speed < self.BT_SLOW_THRESHOLD:
                    self._try_http_downloads()

            except Exception:
                pass

    def _try_http_downloads(self):
        """Attempt to download pieces from HTTP mirrors."""
        with self._lock:
            active_mirrors = [m for m in self._mirrors
                              if m.active and m.errors < 5]

        for mirror in active_mirrors:
            now = time.time()
            if now - mirror.last_used < self.RETRY_INTERVAL:
                continue

            mirror.last_used = now

            t = threading.Thread(
                target=self._download_from_mirror,
                args=(mirror,),
                daemon=True,
            )
            t.start()

    def _download_from_mirror(self, mirror: HTTPMirror):
        """Download file data from an HTTP mirror using range requests."""
        try:
            if mirror.is_web_seed:
                self._download_web_seed(mirror)
            else:
                self._download_http_mirror(mirror)
        except Exception as e:
            mirror.errors += 1
            mirror.last_error = str(e)
            if mirror.errors >= 5:
                mirror.active = False
                print(f"[MultiSource] Mirror disabled after errors: {mirror.url}")

    def _download_web_seed(self, mirror: HTTPMirror):
        """
        Download from a BEP 19 web seed.

        Web seeds serve the torrent's files directly via HTTP.
        We use Range requests to fetch specific pieces.
        """
        if len(self.files) == 1:
            # Single-file torrent: URL points directly to the file
            file_name, file_size = self.files[0]
            url = mirror.url
            if not url.endswith("/"):
                url += "/"
            url += urllib.parse.quote(file_name)
        else:
            # Multi-file: URL is the base directory
            url = mirror.url

        # Try to download a chunk via HTTP range request
        try:
            # Request first piece worth of data as a test
            chunk_size = min(self.piece_length, self.total_length)
            req = urllib.request.Request(url)
            req.add_header("Range", f"bytes=0-{chunk_size - 1}")
            req.add_header("User-Agent", "TopoTorrent/1.0")

            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                if data and self._on_piece_data:
                    # Calculate which piece this belongs to
                    self._on_piece_data(0, 0, data[:self.piece_length])
                    mirror.bytes_downloaded += len(data)
                    mirror.speed_bps = len(data) / 15  # rough estimate
                    print(f"[WebSeed] Downloaded {len(data)} bytes from {mirror.url}")

        except urllib.error.HTTPError as e:
            if e.code == 416:  # Range not satisfiable
                mirror.active = False
            mirror.errors += 1
            mirror.last_error = f"HTTP {e.code}"
        except Exception as e:
            mirror.errors += 1
            mirror.last_error = str(e)

    def _download_http_mirror(self, mirror: HTTPMirror):
        """Download from a standard HTTP mirror."""
        try:
            req = urllib.request.Request(mirror.url)
            req.add_header("User-Agent", "TopoTorrent/1.0")

            # Try a small range request first
            req.add_header("Range", f"bytes=0-{self.piece_length - 1}")

            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                if data and self._on_piece_data:
                    self._on_piece_data(0, 0, data[:self.piece_length])
                    mirror.bytes_downloaded += len(data)
                    print(f"[HTTP] Downloaded {len(data)} bytes from {mirror.url}")

        except Exception as e:
            mirror.errors += 1
            mirror.last_error = str(e)

    def download_piece_from_http(self, piece_index: int,
                                  piece_size: int) -> Optional[bytes]:
        """
        Try to download a specific piece from any available HTTP source.

        Returns piece data if successful, None otherwise.
        """
        offset = piece_index * self.piece_length

        # Calculate which file(s) this piece spans
        file_offset = 0
        for file_path, file_size in self.files:
            file_end = file_offset + file_size
            if offset < file_end:
                # This piece starts in this file
                in_file_offset = offset - file_offset
                break
            file_offset = file_end
        else:
            return None

        # Try each active mirror
        with self._lock:
            mirrors = [m for m in self._mirrors if m.active]

        for mirror in mirrors:
            try:
                if mirror.is_web_seed and len(self.files) == 1:
                    url = mirror.url
                    if not url.endswith("/"):
                        url += "/"
                    url += urllib.parse.quote(self.files[0][0])
                else:
                    url = mirror.url

                req = urllib.request.Request(url)
                req.add_header("Range", f"bytes={offset}-{offset + piece_size - 1}")
                req.add_header("User-Agent", "TopoTorrent/1.0")

                start = time.time()
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                elapsed = time.time() - start

                if len(data) == piece_size:
                    mirror.bytes_downloaded += len(data)
                    if elapsed > 0:
                        mirror.speed_bps = len(data) / elapsed
                    return data

            except Exception as e:
                mirror.errors += 1
                mirror.last_error = str(e)

        # Try IPFS gateways
        if self._ipfs_cid:
            return self._try_ipfs_download(piece_index, piece_size, offset)

        return None

    def _try_ipfs_download(self, piece_index: int, piece_size: int,
                           offset: int) -> Optional[bytes]:
        """Try to download piece data from IPFS gateways."""
        for gateway in self._ipfs_gateways:
            try:
                url = f"{gateway}{self._ipfs_cid}"
                req = urllib.request.Request(url)
                req.add_header("Range", f"bytes={offset}-{offset + piece_size - 1}")
                req.add_header("User-Agent", "TopoTorrent/1.0")

                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = resp.read()
                    if len(data) == piece_size:
                        print(f"[IPFS] Downloaded piece {piece_index} from {gateway}")
                        return data

            except Exception:
                continue

        return None
