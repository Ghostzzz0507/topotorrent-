"""
Core engine for TopoTorrent.

Automatically selects between:
1. libtorrent-based engine (production-grade, if available)
2. Pure-Python engine (fallback, no C++ dependencies needed)

Integrates all subsystems:
- Topology scoring with geo + reputation
- Privacy / encryption
- Edge cache
- Auto-heal
- LAN mesh discovery
- Reputation persistence
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

# Optional subsystems (graceful fallback)
try:
    from core.geo_peer import GeoPeerSelector
except ImportError:
    GeoPeerSelector = None

try:
    from core.reputation import ReputationManager
except ImportError:
    ReputationManager = None

try:
    from core.edge_cache import EdgeCache
except ImportError:
    EdgeCache = None

try:
    from core.auto_heal import AutoHealEngine
except ImportError:
    AutoHealEngine = None

try:
    from core.privacy import PrivacyManager
except ImportError:
    PrivacyManager = None

try:
    from core.experimental import LANMeshDiscovery
except ImportError:
    LANMeshDiscovery = None

try:
    from core.bottleneck import BottleneckDetector
except ImportError:
    BottleneckDetector = None

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
    Main torrent engine with all subsystems integrated.

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

        # === Topology Engine ===
        topo_config = TopologyConfig(
            enabled=settings.topology.enabled,
            score_update_interval=settings.topology.score_update_interval_seconds,
            latency_weight=settings.topology.latency_weight,
            throughput_weight=settings.topology.throughput_weight,
            uptime_weight=settings.topology.uptime_weight,
            stability_weight=settings.topology.stability_weight,
            geo_weight=getattr(settings.topology, 'geo_weight', 0.10),
            reputation_weight=getattr(settings.topology, 'reputation_weight', 0.10),
            max_latency_ms=settings.topology.max_latency_ms,
            max_throughput_bps=settings.topology.max_throughput_bps,
            ewma_alpha=settings.topology.rtt_ewma_alpha,
            min_score_threshold=settings.topology.min_score_threshold,
        )
        self.topology = TopologyEngine(config=topo_config)

        # === Geo Peer Selector ===
        self.geo_selector = None
        if GeoPeerSelector and getattr(settings, 'geo', None) and settings.geo.enabled:
            try:
                self.geo_selector = GeoPeerSelector()
                self.topology.set_geo_selector(self.geo_selector)
                print("[TopoTorrent] Geo-aware peer selection: ON")
            except Exception as e:
                print(f"[TopoTorrent] Geo selector failed: {e}")

        # === Reputation Manager ===
        self.reputation = None
        if ReputationManager and getattr(settings, 'reputation', None) and settings.reputation.enabled:
            try:
                self.reputation = ReputationManager()
                self.topology.set_reputation_manager(self.reputation)
                print("[TopoTorrent] Peer reputation system: ON")
            except Exception as e:
                print(f"[TopoTorrent] Reputation failed: {e}")

        # === Edge Cache ===
        self.edge_cache = None
        if EdgeCache and getattr(settings, 'edge_cache', None) and settings.edge_cache.enabled:
            try:
                self.edge_cache = EdgeCache(
                    max_memory_mb=settings.edge_cache.max_memory_mb,
                    max_disk_mb=settings.edge_cache.max_disk_mb,
                )
                print("[TopoTorrent] Edge cache: ON")
            except Exception as e:
                print(f"[TopoTorrent] Edge cache failed: {e}")

        # === Auto-Heal Engine ===
        self.auto_heal = None
        if AutoHealEngine and getattr(settings, 'auto_heal', None) and settings.auto_heal.enabled:
            try:
                self.auto_heal = AutoHealEngine()
                self.auto_heal.on("on_reannounce", self._on_auto_reannounce)
                self.auto_heal.on("on_reset_pieces", self._on_auto_reset_pieces)
                print("[TopoTorrent] Auto-heal: ON")
            except Exception as e:
                print(f"[TopoTorrent] Auto-heal failed: {e}")

        # === Privacy Manager ===
        self.privacy = None
        if PrivacyManager:
            try:
                enc_mode = getattr(settings, 'privacy', None)
                enc_val = enc_mode.encryption_mode if enc_mode else settings.connection.encryption_mode
                traffic_shaping = enc_mode.traffic_shaping if enc_mode else True
                self.privacy = PrivacyManager(
                    encryption_mode=enc_val,
                    traffic_shaping=traffic_shaping,
                )
                print(f"[TopoTorrent] Privacy (encryption={enc_val}): ON")
            except Exception as e:
                print(f"[TopoTorrent] Privacy failed: {e}")

        # === LAN Mesh Discovery ===
        self.lan_mesh = None
        if (LANMeshDiscovery and getattr(settings, 'experimental', None)
                and settings.experimental.lan_mesh_discovery):
            try:
                self.lan_mesh = LANMeshDiscovery(
                    bt_listen_port=settings.connection.listen_port
                )
                self.lan_mesh.set_on_lan_peer(self._on_lan_peer_found)
                print("[TopoTorrent] LAN mesh discovery: ON")
            except Exception as e:
                print(f"[TopoTorrent] LAN mesh failed: {e}")

        # === Bottleneck Detector ===
        self.bottleneck_detector = None
        if BottleneckDetector:
            try:
                self.bottleneck_detector = BottleneckDetector()
            except Exception:
                pass

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

        # Start auto-heal
        if self.auto_heal:
            self.auto_heal.start()

        # Start LAN mesh discovery
        if self.lan_mesh:
            self.lan_mesh.start()

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

        if self.auto_heal:
            self.auto_heal.stop()

        if self.lan_mesh:
            self.lan_mesh.stop()

        if self.reputation:
            self.reputation.stop()

        if self.edge_cache:
            self.edge_cache.stop()

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

        if self.auto_heal:
            self.auto_heal.unregister_torrent(info_hash)

        if th:
            if isinstance(th, PurePythonTorrentHandle):
                th.stop()
                if delete_files:
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

        # Edge cache stats
        cache_stats = {}
        if self.edge_cache:
            try:
                cs = self.edge_cache.get_stats()
                cache_stats = {
                    "cache_hits": cs.hits,
                    "cache_misses": cs.misses,
                    "cache_hit_rate": f"{cs.hit_rate * 100:.1f}%",
                    "cache_size_mb": f"{cs.cache_size_bytes / 1024 / 1024:.1f}",
                }
            except Exception:
                pass

        # Reputation stats
        rep_stats = {}
        if self.reputation:
            try:
                rep_stats = self.reputation.get_stats()
            except Exception:
                pass

        return {
            "download_speed": total_dl,
            "upload_speed": total_ul,
            "dht_nodes": self._get_dht_nodes(),
            "num_torrents": len(self._torrents),
            "backend": self.backend_name,
            "cache": cache_stats,
            "reputation": rep_stats,
            "lan_peers": len(self.lan_mesh.get_lan_peers()) if self.lan_mesh else 0,
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

            # Inject shared subsystems
            handle._reputation_mgr = self.reputation
            handle._edge_cache = self.edge_cache
            handle._geo_selector = self.geo_selector

            handle.start()

            with self._lock:
                self._torrents[info_hash_hex] = handle

            # Register with auto-heal
            if self.auto_heal:
                self.auto_heal.register_torrent(info_hash_hex)

            # Update LAN mesh with our torrents
            if self.lan_mesh:
                self.lan_mesh.update_info_hashes(list(self._torrents.keys()))

            return info_hash_hex
        except Exception as e:
            print(f"Error adding torrent: {e}")
            return None

    def _py_add_magnet(self, magnet_uri: str, save_path: str) -> Optional[str]:
        """Add magnet link using pure-Python engine."""
        try:
            info_hash, name, trackers = parse_magnet(magnet_uri)
            info_hash_hex = info_hash.hex()

            from core.pure_engine import TorrentMeta
            meta = TorrentMeta(
                info_hash=info_hash,
                announce=trackers[0] if trackers else "",
                announce_list=trackers,
                name=name,
                total_length=0,
                piece_length=262144,
                pieces_hashes=[],
                files=[(name, 0)],
            )

            handle = PurePythonTorrentHandle(meta, save_path, self._peer_id)
            handle._state = "Downloading Metadata"

            # Inject shared subsystems
            handle._reputation_mgr = self.reputation
            handle._edge_cache = self.edge_cache
            handle._geo_selector = self.geo_selector

            handle.start()

            with self._lock:
                self._torrents[info_hash_hex] = handle

            if self.auto_heal:
                self.auto_heal.register_torrent(info_hash_hex)

            if self.lan_mesh:
                self.lan_mesh.update_info_hashes(list(self._torrents.keys()))

            return info_hash_hex
        except Exception as e:
            print(f"Error adding magnet: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # libtorrent backend
    # ═══════════════════════════════════════════════════════════════════

    def _start_libtorrent(self):
        """Initialize libtorrent session with encryption support."""
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

        # === Apply encryption settings (was dead code before!) ===
        if self.privacy:
            lt_enc = self.privacy.get_libtorrent_settings()
            settings_pack.update(lt_enc)
            print(f"[TopoTorrent] Encryption applied to libtorrent session")

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

            if self.auto_heal:
                self.auto_heal.register_torrent(info_hash)

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

            if self.auto_heal:
                self.auto_heal.register_torrent(info_hash)

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

                # Auto-heal: check torrent health
                if self.auto_heal:
                    with self._lock:
                        torrents_snap = dict(self._torrents)
                    for ih, th in torrents_snap.items():
                        try:
                            status = th.get_status()
                            if not status.get("is_paused") and not status.get("is_seeding"):
                                # Get swarm health if available
                                endangered = 0
                                missing = 0
                                if (isinstance(th, PurePythonTorrentHandle) and
                                        th.piece_manager.strategy_engine and
                                        th.piece_manager.strategy_engine.swarm):
                                    swarm = th.piece_manager.strategy_engine.swarm
                                    health = swarm.get_swarm_health()
                                    endangered = health.get("endangered", 0)
                                    missing = health.get("unavailable", 0)

                                self.auto_heal.check_health(
                                    ih,
                                    progress=status.get("progress", 0),
                                    num_seeds=status.get("num_seeds", 0),
                                    num_peers=status.get("num_peers", 0),
                                    download_speed=status.get("download_speed", 0),
                                    endangered_pieces=endangered,
                                    missing_pieces=missing,
                                )
                        except Exception:
                            pass

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

    # ═══════════════════════════════════════════════════════════════════
    # Auto-Heal Callbacks
    # ═══════════════════════════════════════════════════════════════════

    def _on_auto_reannounce(self, info_hash: str):
        """Called by auto-heal to force re-announce."""
        with self._lock:
            th = self._torrents.get(info_hash)
        if th:
            try:
                th.force_reannounce()
            except Exception:
                pass

    def _on_auto_reset_pieces(self, info_hash: str):
        """Called by auto-heal to reset stalled pieces."""
        with self._lock:
            th = self._torrents.get(info_hash)
        if th and isinstance(th, PurePythonTorrentHandle):
            try:
                # Clear stalled in-progress pieces
                now = time.time()
                stale = [
                    idx for idx, t in th.piece_manager.in_progress.items()
                    if now - t > 60
                ]
                for idx in stale:
                    th.piece_manager.in_progress.pop(idx, None)
                if stale:
                    print(f"[AutoHeal] Reset {len(stale)} stalled pieces for {info_hash[:8]}")
            except Exception:
                pass

    def _on_lan_peer_found(self, ip: str, port: int, matching_hashes: List[str]):
        """Called when a LAN peer with matching torrents is found."""
        for ih in matching_hashes:
            with self._lock:
                th = self._torrents.get(ih)
            if th and isinstance(th, PurePythonTorrentHandle):
                # Add LAN peer with highest priority
                if (ip, port) not in [(p.ip, p.port) for p in th.all_peers]:
                    th.discovered_peers.insert(0, (ip, port))
                    print(f"[LANMesh] Added LAN peer {ip}:{port} for {ih[:8]}")
