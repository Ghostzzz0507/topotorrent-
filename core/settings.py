"""
Settings manager for TopoTorrent.

Handles loading, saving, and accessing application configuration
stored as JSON on disk.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


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
    latency_weight: float = 0.35
    throughput_weight: float = 0.35
    uptime_weight: float = 0.15
    stability_weight: float = 0.15
    max_latency_ms: float = 1000.0
    max_throughput_bps: float = 10_000_000.0
    rtt_ewma_alpha: float = 0.3
    prefer_high_score_peers: bool = True
    min_score_threshold: float = 0.1


@dataclass
class UISettings:
    theme: str = "dark"
    minimize_to_tray: bool = True
    show_speed_in_title: bool = True
    confirm_on_delete: bool = True
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

            settings = cls(
                download_dir=data.get("download_dir", DEFAULT_DOWNLOAD_DIR),
                connection=ConnectionSettings(**data.get("connection", {})),
                speed=SpeedSettings(**data.get("speed", {})),
                topology=TopologySettings(**data.get("topology", {})),
                ui=UISettings(**data.get("ui", {})),
            )
            return settings
        except Exception:
            return cls()

    def ensure_dirs(self):
        """Ensure all required directories exist."""
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(RESUME_DIR, exist_ok=True)
