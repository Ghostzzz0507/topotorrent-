"""
Main application window for TopoTorrent.

Assembles the toolbar, torrent list, detail panel, and status bar
into the main window. Manages the update loop and all user actions.
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from gui.theme import Colors, Fonts, Sizes, format_speed, format_size
from gui.torrent_list import TorrentListWidget
from gui.detail_panel import DetailPanel
from gui.add_dialog import AddTorrentDialog
from gui.settings_dialog import SettingsDialog
from core.engine import TorrentEngine
from core.settings import AppSettings


class TopoTorrentApp:
    """
    Main application class.

    Manages the tkinter root window, engine lifecycle,
    and the periodic UI update loop.
    """

    def __init__(self):
        self.settings = AppSettings.load()
        self.settings.ensure_dirs()
        self.engine: Optional[TorrentEngine] = None
        self._update_id = None

        self._setup_root()
        self._setup_toolbar()
        self._setup_main_area()
        self._setup_statusbar()

        self._start_engine()
        self._start_update_loop()

    def _setup_root(self):
        self.root = tk.Tk()
        self.root.title("TopoTorrent — Topology-Aware Torrent Client")
        self.root.configure(bg=Colors.BG_DARK)

        w = self.settings.ui.window_width
        h = self.settings.ui.window_height
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(900, 600)

        # Position
        if self.settings.ui.window_x >= 0:
            self.root.geometry(
                f"+{self.settings.ui.window_x}+{self.settings.ui.window_y}"
            )

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Style treeview scrollbar
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar",
            background=Colors.BG_LIGHT,
            troughcolor=Colors.BG_DARK,
            borderwidth=0,
            arrowsize=12,
        )

    def _setup_toolbar(self):
        toolbar = tk.Frame(
            self.root, bg=Colors.BG_MEDIUM, height=Sizes.TOOLBAR_HEIGHT,
        )
        toolbar.pack(fill="x", side="top")
        toolbar.pack_propagate(False)

        # Buttons
        btn_config = {
            "bg": Colors.BG_MEDIUM,
            "fg": Colors.TEXT_PRIMARY,
            "activebackground": Colors.BG_HOVER,
            "activeforeground": Colors.ACCENT_BLUE,
            "relief": "flat",
            "cursor": "hand2",
            "font": Fonts.BODY,
            "padx": 12,
            "pady": 4,
        }

        buttons = [
            ("➕  Add", self._on_add),
            ("▶  Resume", self._on_resume),
            ("⏸  Pause", self._on_pause),
            ("🗑  Remove", self._on_remove),
            None,  # separator
            ("⏸  Pause All", self._on_pause_all),
            ("▶  Resume All", self._on_resume_all),
            None,  # separator
            ("⚙  Settings", self._on_settings),
        ]

        for item in buttons:
            if item is None:
                sep = tk.Frame(toolbar, bg=Colors.BORDER, width=1)
                sep.pack(side="left", fill="y", padx=4, pady=8)
            else:
                text, cmd = item
                btn = tk.Button(toolbar, text=text, command=cmd, **btn_config)
                btn.pack(side="left", padx=2, pady=4)

        # ─── Topology indicator on the right ─────────
        self._topo_frame = tk.Frame(toolbar, bg=Colors.BG_MEDIUM)
        self._topo_frame.pack(side="right", padx=12)

        self._topo_dot = tk.Label(
            self._topo_frame, text="●",
            bg=Colors.BG_MEDIUM, fg=Colors.ACCENT_GREEN,
            font=(Fonts.FAMILY, 12),
        )
        self._topo_dot.pack(side="left")

        self._topo_label = tk.Label(
            self._topo_frame, text="Topology: ON",
            bg=Colors.BG_MEDIUM, fg=Colors.TEXT_SECONDARY,
            font=Fonts.SMALL,
        )
        self._topo_label.pack(side="left", padx=(4, 0))

    def _setup_main_area(self):
        # Paned window for torrent list + detail panel
        self._paned = tk.PanedWindow(
            self.root, orient="vertical",
            bg=Colors.BG_DARK, sashwidth=3,
            sashrelief="flat",
        )
        self._paned.pack(fill="both", expand=True)

        # Torrent list
        self.torrent_list = TorrentListWidget(
            self._paned, on_select=self._on_torrent_select
        )
        self.torrent_list.set_context_callback(self._on_context_action)
        self._paned.add(self.torrent_list, minsize=150)

        # Detail panel
        self.detail_panel = DetailPanel(self._paned)
        self._paned.add(self.detail_panel, minsize=180)

    def _setup_statusbar(self):
        statusbar = tk.Frame(
            self.root, bg=Colors.BG_MEDIUM,
            height=Sizes.STATUSBAR_HEIGHT,
        )
        statusbar.pack(fill="x", side="bottom")
        statusbar.pack_propagate(False)

        # DHT status
        self._dht_label = tk.Label(
            statusbar, text="DHT: 0 nodes",
            bg=Colors.BG_MEDIUM, fg=Colors.TEXT_MUTED,
            font=Fonts.TINY,
        )
        self._dht_label.pack(side="left", padx=(10, 20))

        # Torrent count
        self._count_label = tk.Label(
            statusbar, text="0 torrents",
            bg=Colors.BG_MEDIUM, fg=Colors.TEXT_MUTED,
            font=Fonts.TINY,
        )
        self._count_label.pack(side="left", padx=(0, 20))

        # Topology avg score
        self._topo_status = tk.Label(
            statusbar, text="⚡ Avg Score: —",
            bg=Colors.BG_MEDIUM, fg=Colors.ACCENT_CYAN,
            font=Fonts.TINY,
        )
        self._topo_status.pack(side="left")

        # Global speeds on right
        self._ul_speed_label = tk.Label(
            statusbar, text="↑ 0 B/s",
            bg=Colors.BG_MEDIUM, fg=Colors.ACCENT_GREEN,
            font=Fonts.SMALL,
        )
        self._ul_speed_label.pack(side="right", padx=(0, 12))

        self._dl_speed_label = tk.Label(
            statusbar, text="↓ 0 B/s",
            bg=Colors.BG_MEDIUM, fg=Colors.ACCENT_BLUE,
            font=Fonts.SMALL,
        )
        self._dl_speed_label.pack(side="right", padx=(0, 12))

    # ═══════════════════════════════════════════════════════════════════
    # Engine Management
    # ═══════════════════════════════════════════════════════════════════

    def _start_engine(self):
        try:
            self.engine = TorrentEngine(self.settings)
            self.engine.start()

            # Show backend and topology status
            backend = self.engine.backend_name
            if self.settings.topology.enabled:
                self._topo_dot.configure(fg=Colors.ACCENT_GREEN)
                self._topo_label.configure(text=f"Topology: ON  |  {backend}")
            else:
                self._topo_dot.configure(fg=Colors.TEXT_MUTED)
                self._topo_label.configure(text=f"Topology: OFF  |  {backend}")

        except Exception as e:
            messagebox.showerror(
                "Engine Error",
                f"Failed to start torrent engine:\n\n{e}",
                parent=self.root,
            )

    def _start_update_loop(self):
        """Start the periodic UI update loop (every 1 second)."""
        self._update_ui()

    def _update_ui(self):
        """Update all UI elements from engine state."""
        if self.engine:
            try:
                # Update torrent list
                all_status = self.engine.get_all_status()
                self.torrent_list.update_torrents(all_status)

                # Update global stats
                stats = self.engine.get_global_stats()
                dl = stats["download_speed"]
                ul = stats["upload_speed"]

                self._dl_speed_label.configure(
                    text=f"↓ {format_speed(dl)}"
                )
                self._ul_speed_label.configure(
                    text=f"↑ {format_speed(ul)}"
                )
                self._dht_label.configure(
                    text=f"DHT: {stats.get('dht_nodes', 0)} nodes"
                )
                self._count_label.configure(
                    text=f"{stats.get('num_torrents', 0)} torrents"
                )

                # Update title with speed
                if self.settings.ui.show_speed_in_title:
                    self.root.title(
                        f"TopoTorrent — ↓ {format_speed(dl)}  ↑ {format_speed(ul)}"
                    )

                # Update topology status
                topo_summary = self.engine.topology.get_metrics_summary()
                avg = topo_summary.get("avg_score", 0)
                self._topo_status.configure(
                    text=f"⚡ Avg Score: {avg:.3f}  |  "
                         f"Peers: {topo_summary.get('total_peers', 0)}"
                )

                # Update detail panel for selected torrent
                selected = self.torrent_list.get_selected_hash()
                if selected and selected in all_status:
                    status = all_status[selected]
                    self.detail_panel.update_general(status)

                    # Update peers with topology scores
                    th = self.engine.get_torrent(selected)
                    if th:
                        peers = th.get_peers()
                        scores = self.engine.topology.get_scores()
                        self.detail_panel.update_peers(peers, scores)
                        self.detail_panel.update_files(th.get_files())
                        self.detail_panel.update_trackers(th.get_trackers())
                        self.detail_panel.update_speed(
                            status["download_speed"],
                            status["upload_speed"],
                        )

            except Exception:
                pass

        self._update_id = self.root.after(1000, self._update_ui)

    # ═══════════════════════════════════════════════════════════════════
    # User Actions
    # ═══════════════════════════════════════════════════════════════════

    def _on_add(self):
        dialog = AddTorrentDialog(self.root, self.settings.download_dir)
        self.root.wait_window(dialog)

        if dialog.result and self.engine:
            mode, value, save_path, start_paused = dialog.result

            if mode == "file":
                info_hash = self.engine.add_torrent_file(value, save_path)
            else:
                info_hash = self.engine.add_magnet(value, save_path)

            if info_hash and start_paused:
                self.engine.pause_torrent(info_hash)

    def _on_resume(self):
        selected = self.torrent_list.get_selected_hash()
        if selected and self.engine:
            self.engine.resume_torrent(selected)

    def _on_pause(self):
        selected = self.torrent_list.get_selected_hash()
        if selected and self.engine:
            self.engine.pause_torrent(selected)

    def _on_remove(self):
        selected = self.torrent_list.get_selected_hash()
        if not selected or not self.engine:
            return

        if self.settings.ui.confirm_on_delete:
            result = messagebox.askyesnocancel(
                "Remove Torrent",
                "Delete downloaded files too?",
                parent=self.root,
            )
            if result is None:
                return
            self.engine.remove_torrent(selected, delete_files=result)
        else:
            self.engine.remove_torrent(selected)

    def _on_pause_all(self):
        if self.engine:
            self.engine.pause_all()

    def _on_resume_all(self):
        if self.engine:
            self.engine.resume_all()

    def _on_settings(self):
        dialog = SettingsDialog(self.root, self.settings)
        self.root.wait_window(dialog)

        if dialog.result and self.engine:
            self.settings = dialog.result
            # Apply speed limits immediately
            self.engine.set_download_limit(
                self.settings.speed.download_rate_limit
            )
            self.engine.set_upload_limit(
                self.settings.speed.upload_rate_limit
            )

    def _on_torrent_select(self, info_hash: str):
        self.detail_panel.set_torrent(info_hash)

    def _on_context_action(self, info_hash: str, action: str):
        if not self.engine:
            return

        if action == "resume":
            self.engine.resume_torrent(info_hash)
        elif action == "pause":
            self.engine.pause_torrent(info_hash)
        elif action == "remove":
            self.engine.remove_torrent(info_hash)
        elif action == "remove_files":
            self.engine.remove_torrent(info_hash, delete_files=True)
        elif action == "open_folder":
            th = self.engine.get_torrent(info_hash)
            if th:
                path = th.save_path
                if os.path.exists(path):
                    subprocess.Popen(f'explorer "{path}"')
        elif action == "reannounce":
            th = self.engine.get_torrent(info_hash)
            if th:
                th.force_reannounce()

    def _on_close(self):
        # Save window geometry
        try:
            geo = self.root.geometry()
            # Parse WxH+X+Y
            size, pos = geo.split("+", 1)
            w, h = size.split("x")
            x, y = pos.split("+")
            self.settings.ui.window_width = int(w)
            self.settings.ui.window_height = int(h)
            self.settings.ui.window_x = int(x)
            self.settings.ui.window_y = int(y)
            self.settings.save()
        except Exception:
            pass

        # Stop update loop
        if self._update_id:
            self.root.after_cancel(self._update_id)

        # Stop engine
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass

        self.root.destroy()

    def run(self):
        """Start the application main loop."""
        self.root.mainloop()
