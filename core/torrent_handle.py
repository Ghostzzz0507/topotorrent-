"""
Torrent handle wrapper for TopoTorrent.

Provides a higher-level interface around libtorrent's torrent_handle
with computed properties for the GUI (ETA, formatted speeds, etc.).
"""

import time
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class TorrentFileInfo:
    """Info about a single file within a torrent."""
    index: int
    path: str
    size: int
    progress: float = 0.0
    priority: int = 4  # 0=skip, 1=low, 4=normal, 7=high


@dataclass
class TorrentPeerInfo:
    """Info about a connected peer."""
    ip: str = ""
    port: int = 0
    client: str = ""
    flags: str = ""
    progress: float = 0.0
    download_speed: float = 0.0
    upload_speed: float = 0.0
    total_downloaded: int = 0
    total_uploaded: int = 0
    country: str = ""
    # Topology fields
    topology_score: float = 0.0
    latency_ms: float = 0.0
    throughput_bps: float = 0.0
    stability: float = 1.0
    uptime_seconds: float = 0.0


@dataclass
class TorrentTrackerInfo:
    """Info about a tracker."""
    url: str = ""
    status: str = "Not contacted"
    peers: int = 0
    message: str = ""
    next_announce: int = 0
    tier: int = 0


# Torrent states matching libtorrent
TORRENT_STATES = {
    0: "Queued",
    1: "Checking",
    2: "Downloading Metadata",
    3: "Downloading",
    4: "Finished",
    5: "Seeding",
    6: "Allocating",
    7: "Checking Resume",
}


class TorrentHandle:
    """
    Wrapper around libtorrent.torrent_handle providing a clean
    interface for the GUI layer.
    """

    def __init__(self, lt_handle, save_path: str):
        self._handle = lt_handle
        self.save_path = save_path
        self.added_time = time.time()
        self.topology_avg_score = 0.0
        self._files_cache: List[TorrentFileInfo] = []
        self._speed_history: List[tuple] = []  # (timestamp, dl_speed, ul_speed)
        self._max_history = 300  # 5 minutes at 1s intervals

    @property
    def lt_handle(self):
        return self._handle

    @property
    def is_valid(self) -> bool:
        try:
            return self._handle.is_valid()
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status dict for GUI display."""
        try:
            s = self._handle.status()
        except Exception:
            return self._empty_status()

        info = self._handle.torrent_file()
        name = info.name() if info else "Loading metadata..."
        total_size = info.total_size() if info else 0

        dl_speed = s.download_rate
        ul_speed = s.upload_rate
        progress = s.progress

        # Track speed history
        now = time.time()
        self._speed_history.append((now, dl_speed, ul_speed))
        if len(self._speed_history) > self._max_history:
            self._speed_history.pop(0)

        # Calculate ETA
        eta = -1
        if dl_speed > 0 and total_size > 0:
            remaining = total_size * (1.0 - progress)
            eta = int(remaining / dl_speed)

        state_idx = int(s.state)
        state_str = TORRENT_STATES.get(state_idx, "Unknown")

        if s.paused and not s.auto_managed:
            state_str = "Paused"

        return {
            "name": name,
            "total_size": total_size,
            "progress": progress,
            "state": state_str,
            "state_idx": state_idx,
            "download_speed": dl_speed,
            "upload_speed": ul_speed,
            "eta": eta,
            "num_seeds": s.num_seeds,
            "num_peers": s.num_peers,
            "num_complete": s.num_complete if s.num_complete >= 0 else 0,
            "num_incomplete": s.num_incomplete if s.num_incomplete >= 0 else 0,
            "total_downloaded": s.total_done,
            "total_uploaded": s.all_time_upload,
            "ratio": (s.all_time_upload / s.total_done) if s.total_done > 0 else 0.0,
            "save_path": self.save_path,
            "info_hash": str(s.info_hashes.v1) if hasattr(s.info_hashes, 'v1') else str(s.info_hash),
            "added_time": self.added_time,
            "is_paused": s.paused,
            "is_seeding": state_idx in (4, 5),
            "topology_score": self.topology_avg_score,
        }

    def get_peers(self) -> List[TorrentPeerInfo]:
        """Get list of connected peers."""
        peers = []
        try:
            for p in self._handle.get_peer_info():
                flags = []
                if p.flags & 0x01:  # interesting
                    flags.append("I")
                if p.flags & 0x02:  # choked
                    flags.append("C")
                if p.flags & 0x04:  # remote interested
                    flags.append("i")
                if p.flags & 0x08:  # remote choked
                    flags.append("c")
                if p.flags & 0x10:  # supports encryption
                    flags.append("E")
                if p.flags & 0x100:  # seed
                    flags.append("S")
                if p.flags & 0x4000:  # uTP
                    flags.append("u")

                ip_tuple = p.ip
                peer = TorrentPeerInfo(
                    ip=str(ip_tuple[0]) if isinstance(ip_tuple, tuple) else str(ip_tuple),
                    port=int(ip_tuple[1]) if isinstance(ip_tuple, tuple) else 0,
                    client=str(p.client) if hasattr(p, 'client') else "",
                    flags=" ".join(flags),
                    progress=p.progress,
                    download_speed=p.down_speed,
                    upload_speed=p.up_speed,
                    total_downloaded=p.total_download,
                    total_uploaded=p.total_upload,
                )
                peers.append(peer)
        except Exception:
            pass
        return peers

    def get_files(self) -> List[TorrentFileInfo]:
        """Get file list with progress."""
        files = []
        try:
            info = self._handle.torrent_file()
            if not info:
                return files

            file_storage = info.files()
            file_progress = self._handle.file_progress()
            priorities = self._handle.get_file_priorities()

            for i in range(file_storage.num_files()):
                size = file_storage.file_size(i)
                prog = file_progress[i] / size if size > 0 else 0.0
                pri = priorities[i] if i < len(priorities) else 4

                files.append(TorrentFileInfo(
                    index=i,
                    path=file_storage.file_path(i),
                    size=size,
                    progress=prog,
                    priority=pri,
                ))
        except Exception:
            pass
        return files

    def get_trackers(self) -> List[TorrentTrackerInfo]:
        """Get tracker list with status."""
        trackers = []
        try:
            for t in self._handle.trackers():
                status = "Working"
                message = ""
                peers = 0

                if t.endpoints:
                    ep = t.endpoints[0]
                    if hasattr(ep, 'info_hashes') and ep.info_hashes:
                        ih = list(ep.info_hashes)
                        if ih:
                            a = ih[0]
                            if a.fails > 0:
                                status = f"Error (fails: {a.fails})"
                                message = str(a.message) if hasattr(a, 'message') else ""
                            elif a.verified:
                                status = "Working"
                            peers = a.scrape_complete + a.scrape_incomplete
                    elif hasattr(ep, 'fails'):
                        if ep.fails > 0:
                            status = f"Error (fails: {ep.fails})"
                        peers = getattr(ep, 'scrape_complete', 0) + getattr(ep, 'scrape_incomplete', 0)

                trackers.append(TorrentTrackerInfo(
                    url=t.url,
                    status=status,
                    peers=peers,
                    message=message,
                    tier=t.tier,
                ))
        except Exception:
            pass
        return trackers

    def get_speed_history(self) -> List[tuple]:
        """Return (timestamp, dl_speed, ul_speed) history."""
        return list(self._speed_history)

    def pause(self):
        try:
            self._handle.pause()
            self._handle.unset_flags(1)  # auto_managed
        except Exception:
            pass

    def resume(self):
        try:
            self._handle.resume()
        except Exception:
            pass

    def set_file_priorities(self, priorities: List[int]):
        try:
            self._handle.prioritize_files(priorities)
        except Exception:
            pass

    def force_reannounce(self):
        try:
            self._handle.force_reannounce()
        except Exception:
            pass

    def _empty_status(self) -> Dict[str, Any]:
        return {
            "name": "Invalid",
            "total_size": 0,
            "progress": 0.0,
            "state": "Error",
            "state_idx": -1,
            "download_speed": 0,
            "upload_speed": 0,
            "eta": -1,
            "num_seeds": 0,
            "num_peers": 0,
            "num_complete": 0,
            "num_incomplete": 0,
            "total_downloaded": 0,
            "total_uploaded": 0,
            "ratio": 0.0,
            "save_path": self.save_path,
            "info_hash": "",
            "added_time": self.added_time,
            "is_paused": True,
            "is_seeding": False,
            "topology_score": 0.0,
        }
