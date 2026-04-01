"""
Core engine for TopoTorrent.

Automatically selects between:
1. libtorrent-based engine (production-grade, if available)
2. Pure-Python engine (fallback, no C++ dependencies needed)
"""

import os
import time
import threading
from typing import Callable, Dict, List, Optional, Any

from core.settings import AppSettings, RESUME_DIR
from core.topology import TopologyEngine, TopologyConfig
from core.pure_engine import (
    parse_torrent_file, parse_magnet,
    generate_peer_id, PurePythonTorrentHandle,
)

# Try libtorrent, but don't fail
lt = None
_lt_import_error = None
try:
    import libtorrent as lt
except Exception as e:
    _lt_import_error = str(e)

HAS_LIBTORRENT = lt is not None


class TorrentEngine:
    """
    Main torrent engine.

    Uses libtorrent when available, falls back to pure-Python engine.
    Both backends expose the same interface to the GUI.
    """

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._torrents: Dict[str, Any] = {}  # info_hash_hex -> handle
        self._lock = threading.Lock()
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._status_callbacks: List[Callable] = []
        self._peer_id = generate_peer_id()

        # Backend
        self._use_libtorrent = HAS_LIBTORRENT
        self._session = None  # libtorrent session (if available)

        # Topology
        topo_config = TopologyConfig(
            enabled=settings.topology.enabled,
            score_update_interval=settings.topology.score_update_interval_seconds,
            latency_weight=settings.topology.latency_weight,
            throughput_weight=settings.topology.throughput_weight,
            uptime_weight=settings.topology.uptime_weight,
            stability_weight=settings.topology.stability_weight,
            max_latency_ms=settings.topology.max_latency_ms,
            max_throughput_bps=settings.topology.max_throughput_bps,
            ewma_alpha=settings.topology.rtt_ewma_alpha,
            min_score_threshold=settings.topology.min_score_threshold,
        )
        self.topology = TopologyEngine(config=topo_config)

    @property
    def backend_name(self) -> str:
        return "libtorrent" if self._use_libtorrent else "Pure Python"

    def start(self):
        """Initialize and start the engine."""
        if self._use_libtorrent:
            self._start_libtorrent()
        else:
            print(f"[TopoTorrent] libtorrent unavailable ({_lt_import_error})")
            print("[TopoTorrent] Using pure-Python engine (fully functional)")

        # Start topology engine
        self.topology.set_peer_data_callback(self._get_all_peer_data)
        self.topology.start()

        # Start polling thread
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="EnginePoller"
        )
        self._poll_thread.start()

    def stop(self):
        """Stop the engine and cleanup."""
        self._running = False
        self.topology.stop()

        if self._poll_thread:
            self._poll_thread.join(timeout=5)

        # Stop all pure-python handles
        with self._lock:
            for th in self._torrents.values():
                if isinstance(th, PurePythonTorrentHandle):
                    th.stop()

        if self._use_libtorrent and self._session:
            try:
                self._session.pause()
                del self._session
            except Exception:
                pass
            self._session = None

    def add_torrent_file(self, torrent_path: str, save_path: Optional[str] = None) -> Optional[str]:
        """Add a torrent from a .torrent file."""
        save = save_path or self.settings.download_dir
        os.makedirs(save, exist_ok=True)

        if self._use_libtorrent and self._session:
            return self._lt_add_file(torrent_path, save)
        else:
            return self._py_add_file(torrent_path, save)

    def add_magnet(self, magnet_uri: str, save_path: Optional[str] = None) -> Optional[str]:
        """Add a torrent from a magnet link."""
        save = save_path or self.settings.download_dir
        os.makedirs(save, exist_ok=True)

        if self._use_libtorrent and self._session:
            return self._lt_add_magnet(magnet_uri, save)
        else:
            return self._py_add_magnet(magnet_uri, save)

    def remove_torrent(self, info_hash: str, delete_files: bool = False):
        """Remove a torrent."""
        with self._lock:
            th = self._torrents.pop(info_hash, None)

        if th:
            if isinstance(th, PurePythonTorrentHandle):
                th.stop()
                if delete_files:
                    # Delete downloaded files
                    for fpath, _ in th.meta.files:
                        full = os.path.join(th.save_path, fpath)
                        if os.path.exists(full):
                            try:
                                os.remove(full)
                            except Exception:
                                pass
            elif self._use_libtorrent and self._session:
                try:
                    from core.torrent_handle import TorrentHandle
                    if isinstance(th, TorrentHandle):
                        flags = lt.options_t.delete_files if delete_files else 0
                        self._session.remove_torrent(th.lt_handle, flags)
                except Exception:
                    pass

    def pause_torrent(self, info_hash: str):
        with self._lock:
            th = self._torrents.get(info_hash)
        if th:
            th.pause()

    def resume_torrent(self, info_hash: str):
        with self._lock:
            th = self._torrents.get(info_hash)
        if th:
            th.resume()

    def pause_all(self):
        with self._lock:
            for th in self._torrents.values():
                th.pause()

    def resume_all(self):
        with self._lock:
            for th in self._torrents.values():
                th.resume()

    def get_torrent(self, info_hash: str):
        with self._lock:
            return self._torrents.get(info_hash)

    def get_all_torrents(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._torrents)

    def get_all_status(self) -> Dict[str, dict]:
        """Get status of all torrents."""
        result = {}
        with self._lock:
            torrents = dict(self._torrents)

        for ih, th in torrents.items():
            try:
                status = th.get_status()
                status["topology_score"] = th.topology_avg_score
                result[ih] = status
            except Exception:
                pass

        return result

    def get_global_stats(self) -> dict:
        """Get global download/upload speeds."""
        total_dl = 0.0
        total_ul = 0.0

        with self._lock:
            torrents = list(self._torrents.values())

        for th in torrents:
            try:
                status = th.get_status()
                total_dl += status.get("download_speed", 0)
                total_ul += status.get("upload_speed", 0)
            except Exception:
                pass

        return {
            "download_speed": total_dl,
            "upload_speed": total_ul,
            "dht_nodes": self._get_dht_nodes(),
            "num_torrents": len(self._torrents),
            "backend": self.backend_name,
        }

    def set_download_limit(self, limit: int):
        if self._use_libtorrent and self._session:
            try:
                s = self._session.get_settings()
                s["download_rate_limit"] = limit
                self._session.apply_settings(s)
            except Exception:
                pass

    def set_upload_limit(self, limit: int):
        if self._use_libtorrent and self._session:
            try:
                s = self._session.get_settings()
                s["upload_rate_limit"] = limit
                self._session.apply_settings(s)
            except Exception:
                pass

    def on_status_update(self, callback: Callable):
        self._status_callbacks.append(callback)

    # ═══════════════════════════════════════════════════════════════════
    # Pure-Python backend
    # ═══════════════════════════════════════════════════════════════════

    def _py_add_file(self, torrent_path: str, save_path: str) -> Optional[str]:
        """Add torrent using pure-Python engine."""
        try:
            meta = parse_torrent_file(torrent_path)
            info_hash_hex = meta.info_hash.hex()

            handle = PurePythonTorrentHandle(meta, save_path, self._peer_id)
            handle.start()

            with self._lock:
                self._torrents[info_hash_hex] = handle

            return info_hash_hex
        except Exception as e:
            print(f"Error adding torrent: {e}")
            return None

    def _py_add_magnet(self, magnet_uri: str, save_path: str) -> Optional[str]:
        """Add magnet link using pure-Python engine."""
        try:
            info_hash, name, trackers = parse_magnet(magnet_uri)
            info_hash_hex = info_hash.hex()

            # Create a minimal TorrentMeta for magnet
            from core.pure_engine import TorrentMeta
            meta = TorrentMeta(
                info_hash=info_hash,
                announce=trackers[0] if trackers else "",
                announce_list=trackers,
                name=name,
                total_length=0,  # Unknown until metadata received
                piece_length=262144,
                pieces_hashes=[],
                files=[(name, 0)],
            )

            handle = PurePythonTorrentHandle(meta, save_path, self._peer_id)
            handle._state = "Downloading Metadata"
            handle.start()

            with self._lock:
                self._torrents[info_hash_hex] = handle

            return info_hash_hex
        except Exception as e:
            print(f"Error adding magnet: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # libtorrent backend
    # ═══════════════════════════════════════════════════════════════════

    def _start_libtorrent(self):
        """Initialize libtorrent session."""
        settings_pack = {
            "user_agent": "TopoTorrent/1.0",
            "listen_interfaces": f"0.0.0.0:{self.settings.connection.listen_port}",
            "enable_dht": self.settings.connection.enable_dht,
            "enable_lsd": self.settings.connection.enable_lsd,
            "enable_upnp": self.settings.connection.enable_upnp,
            "enable_natpmp": self.settings.connection.enable_natpmp,
            "max_connections": self.settings.connection.max_connections,
            "max_connections_per_torrent": self.settings.connection.max_connections_per_torrent,
        }

        if self.settings.speed.download_rate_limit > 0:
            settings_pack["download_rate_limit"] = self.settings.speed.download_rate_limit
        if self.settings.speed.upload_rate_limit > 0:
            settings_pack["upload_rate_limit"] = self.settings.speed.upload_rate_limit

        self._session = lt.session(settings_pack)

        if self.settings.connection.enable_pex:
            self._session.add_extension("ut_pex")
            self._session.add_extension("ut_metadata")

        if self.settings.connection.enable_dht:
            self._session.add_dht_router("router.bittorrent.com", 6881)
            self._session.add_dht_router("router.utorrent.com", 6881)
            self._session.add_dht_router("dht.transmissionbt.com", 6881)
            self._session.start_dht()

    def _lt_add_file(self, torrent_path: str, save_path: str) -> Optional[str]:
        """Add torrent using libtorrent."""
        from core.torrent_handle import TorrentHandle
        try:
            info = lt.torrent_info(torrent_path)
            params = {
                "ti": info,
                "save_path": save_path,
                "storage_mode": lt.storage_mode_t.storage_mode_sparse,
            }
            handle = self._session.add_torrent(params)
            info_hash = str(handle.info_hash())

            with self._lock:
                self._torrents[info_hash] = TorrentHandle(handle, save_path)
            return info_hash
        except Exception as e:
            print(f"Error: {e}")
            return None

    def _lt_add_magnet(self, magnet_uri: str, save_path: str) -> Optional[str]:
        """Add magnet using libtorrent."""
        from core.torrent_handle import TorrentHandle
        try:
            params = lt.parse_magnet_uri(magnet_uri)
            params.save_path = save_path
            handle = self._session.add_torrent(params)
            info_hash = str(handle.info_hash())

            with self._lock:
                self._torrents[info_hash] = TorrentHandle(handle, save_path)
            return info_hash
        except Exception as e:
            print(f"Error: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # Common
    # ═══════════════════════════════════════════════════════════════════

    def _poll_loop(self):
        """Background polling loop."""
        while self._running:
            try:
                time.sleep(1.0)
                if not self._running:
                    break

                # Process libtorrent alerts if available
                if self._use_libtorrent and self._session:
                    try:
                        self._session.pop_alerts()
                    except Exception:
                        pass

                # Update topology scores
                with self._lock:
                    for th in self._torrents.values():
                        th.topology_avg_score = self.topology.get_average_score()

                # Notify callbacks
                for cb in self._status_callbacks:
                    try:
                        cb()
                    except Exception:
                        pass

            except Exception:
                pass

    def _get_all_peer_data(self) -> list:
        """Get peer data from all torrents for topology engine."""
        all_peers = []
        with self._lock:
            torrents = list(self._torrents.values())

        for th in torrents:
            try:
                peers = th.get_peers()
                for p in peers:
                    ip = getattr(p, "ip", "")
                    port = getattr(p, "port", 0)
                    all_peers.append({
                        "ip": str(ip),
                        "port": int(port),
                        "down_speed": getattr(p, "download_speed", 0),
                        "up_speed": getattr(p, "upload_speed", 0),
                        "total_download": getattr(p, "total_downloaded", 0),
                        "total_upload": getattr(p, "total_uploaded", 0),
                        "progress": getattr(p, "progress", 0),
                    })
            except Exception:
                pass

        return all_peers

    def _get_dht_nodes(self) -> int:
        if self._use_libtorrent and self._session:
            try:
                return self._session.status().dht_nodes
            except Exception:
                pass
        return 0
