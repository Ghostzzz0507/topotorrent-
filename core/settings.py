"""
Settings manager for TopoTorrent.

Handles loading, saving, and accessing application configuration
stored as JSON on disk. Includes all feature settings.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List


DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "TopoTorrent")
CONFIG_DIR = str(Path.home() / ".topotorrent")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
SESSION_FILE = os.path.join(CONFIG_DIR, "session.state")
RESUME_DIR = os.path.join(CONFIG_DIR, "resume")


@dataclass
class ConnectionSettings:
    listen_port: int = 6881
    enable_dht: bool = True
    enable_pex: bool = True
    enable_lsd: bool = True
    enable_upnp: bool = True
    enable_natpmp: bool = True
    encryption_mode: int = 1  # 0=disabled, 1=enabled, 2=forced
    max_connections: int = 200
    max_connections_per_torrent: int = 50


@dataclass
class SpeedSettings:
    download_rate_limit: int = 0  # 0 = unlimited, in bytes/s
    upload_rate_limit: int = 0
    max_active_downloads: int = 5
    max_active_seeds: int = 5
    max_active_torrents: int = 10


@dataclass
class TopologySettings:
    enabled: bool = True
    probe_interval_seconds: float = 5.0
    score_update_interval_seconds: float = 2.0
    latency_weight: float = 0.30
    throughput_weight: float = 0.30
    uptime_weight: float = 0.10
    stability_weight: float = 0.10
    geo_weight: float = 0.10
    reputation_weight: float = 0.10
    max_latency_ms: float = 1000.0
    max_throughput_bps: float = 10_000_000.0
    rtt_ewma_alpha: float = 0.3
    prefer_high_score_peers: bool = True
    min_score_threshold: float = 0.1


@dataclass
class PrivacySettings:
    encryption_mode: int = 1  # 0=disabled, 1=enabled, 2=forced
    traffic_shaping: bool = True
    protocol_obfuscation: bool = True


@dataclass
class GeoSettings:
    enabled: bool = True
    prefer_local_peers: bool = True
    prefer_same_region: bool = True


@dataclass
class ReputationSettings:
    enabled: bool = True
    persist_to_disk: bool = True
    ban_corrupt_peers: bool = True
    max_corrupt_pieces: int = 5
    cleanup_days: int = 30


@dataclass
class MultiSourceSettings:
    enabled: bool = True
    http_mirrors_enabled: bool = True
    ipfs_enabled: bool = True
    bt_slow_threshold: int = 50 * 1024  # 50 KB/s
    max_http_connections: int = 4


@dataclass
class AutoHealSettings:
    enabled: bool = True
    stall_threshold_seconds: int = 120
    dead_threshold_seconds: int = 600
    auto_reannounce: bool = True
    auto_reset_stalled: bool = True


@dataclass
class EdgeCacheSettings:
    enabled: bool = True
    max_memory_mb: int = 256
    max_disk_mb: int = 1024


@dataclass
class PieceStrategySettings:
    strategy: str = "hybrid"  # random, rarest_first, sequential, hybrid
    enable_swarm_intelligence: bool = True
    enable_churn_prediction: bool = True


@dataclass
class ExperimentalSettings:
    ai_bandwidth: bool = True
    lan_mesh_discovery: bool = True
    cloud_seeder: bool = False  # Off by default (needs config)
    mobile_bridge: bool = False  # Off by default
    mobile_bridge_port: int = 8099
    cloud_nodes: List[str] = field(default_factory=list)


@dataclass
class UISettings:
    theme: str = "dark"
    minimize_to_tray: bool = True
    show_speed_in_title: bool = True
    confirm_on_delete: bool = True
    show_bottleneck_messages: bool = True
    window_width: int = 1280
    window_height: int = 800
    window_x: int = -1
    window_y: int = -1


@dataclass
class AppSettings:
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    speed: SpeedSettings = field(default_factory=SpeedSettings)
    topology: TopologySettings = field(default_factory=TopologySettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    geo: GeoSettings = field(default_factory=GeoSettings)
    reputation: ReputationSettings = field(default_factory=ReputationSettings)
    multi_source: MultiSourceSettings = field(default_factory=MultiSourceSettings)
    auto_heal: AutoHealSettings = field(default_factory=AutoHealSettings)
    edge_cache: EdgeCacheSettings = field(default_factory=EdgeCacheSettings)
    piece_strategy: PieceStrategySettings = field(default_factory=PieceStrategySettings)
    experimental: ExperimentalSettings = field(default_factory=ExperimentalSettings)
    ui: UISettings = field(default_factory=UISettings)

    def save(self):
        """Save settings to disk."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        data = asdict(self)
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls) -> "AppSettings":
        """Load settings from disk, or return defaults."""
        if not os.path.exists(CONFIG_FILE):
            settings = cls()
            settings.save()
            return settings

        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)

            # Build settings with safe fallbacks for new fields
            def safe_init(klass, d):
                known = {k for k in klass.__dataclass_fields__}
                return klass(**{k: v for k, v in d.items() if k in known})

            settings = cls(
                download_dir=data.get("download_dir", DEFAULT_DOWNLOAD_DIR),
                connection=safe_init(ConnectionSettings, data.get("connection", {})),
                speed=safe_init(SpeedSettings, data.get("speed", {})),
                topology=safe_init(TopologySettings, data.get("topology", {})),
                privacy=safe_init(PrivacySettings, data.get("privacy", {})),
                geo=safe_init(GeoSettings, data.get("geo", {})),
                reputation=safe_init(ReputationSettings, data.get("reputation", {})),
                multi_source=safe_init(MultiSourceSettings, data.get("multi_source", {})),
                auto_heal=safe_init(AutoHealSettings, data.get("auto_heal", {})),
                edge_cache=safe_init(EdgeCacheSettings, data.get("edge_cache", {})),
                piece_strategy=safe_init(PieceStrategySettings, data.get("piece_strategy", {})),
                experimental=safe_init(ExperimentalSettings, data.get("experimental", {})),
                ui=safe_init(UISettings, data.get("ui", {})),
            )
            return settings
        except Exception:
            return cls()

    def ensure_dirs(self):
        """Ensure all required directories exist."""
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(RESUME_DIR, exist_ok=True)
