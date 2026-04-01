"""
Settings dialog for TopoTorrent.

Multi-tab settings dialog for Connection, Speed, Downloads,
Topology, and UI preferences.
"""

import tkinter as tk
from tkinter import filedialog
from typing import Optional

from gui.theme import Colors, Fonts
from core.settings import AppSettings


class SettingsDialog(tk.Toplevel):
    """Settings configuration dialog with tabs."""

    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=Colors.BG_DARK)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.settings = settings
        self.result: Optional[AppSettings] = None
        self._vars = {}

        self._setup_ui()

        # Center
        self.update_idletasks()
        w, h = 520, 480
        self.geometry(f"{w}x{h}")
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda e: self._on_cancel())

    def _setup_ui(self):
        # Title
        tk.Label(
            self, text="⚙  Settings",
            bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY,
            font=Fonts.HEADING,
        ).pack(pady=(12, 8))

        # Notebook for tabs
        from tkinter import ttk
        style = ttk.Style()
        style.configure("Settings.TNotebook",
            background=Colors.BG_DARK, borderwidth=0)
        style.configure("Settings.TNotebook.Tab",
            background=Colors.BG_LIGHT, foreground=Colors.TEXT_SECONDARY,
            padding=[14, 4], font=Fonts.SMALL_BOLD, borderwidth=0)
        style.map("Settings.TNotebook.Tab",
            background=[("selected", Colors.BG_MEDIUM)],
            foreground=[("selected", Colors.ACCENT_BLUE)])

        nb = ttk.Notebook(self, style="Settings.TNotebook")
        nb.pack(fill="both", expand=True, padx=12, pady=4)

        # ─── Connection Tab ──────────────────────────
        conn = tk.Frame(nb, bg=Colors.BG_MEDIUM)
        nb.add(conn, text="  Connection  ")

        self._add_field(conn, "Listen Port:", "listen_port",
                        self.settings.connection.listen_port, 0, "int")
        self._add_field(conn, "Max Connections:", "max_connections",
                        self.settings.connection.max_connections, 1, "int")
        self._add_field(conn, "Max Per Torrent:", "max_conn_torrent",
                        self.settings.connection.max_connections_per_torrent, 2, "int")
        self._add_check(conn, "Enable DHT", "enable_dht",
                        self.settings.connection.enable_dht, 3)
        self._add_check(conn, "Enable PEX", "enable_pex",
                        self.settings.connection.enable_pex, 4)
        self._add_check(conn, "Enable LSD", "enable_lsd",
                        self.settings.connection.enable_lsd, 5)
        self._add_check(conn, "Enable UPnP", "enable_upnp",
                        self.settings.connection.enable_upnp, 6)

        # ─── Speed Tab ───────────────────────────────
        speed = tk.Frame(nb, bg=Colors.BG_MEDIUM)
        nb.add(speed, text="  Speed  ")

        self._add_field(speed, "DL Limit (KB/s, 0=∞):", "dl_limit",
                        self.settings.speed.download_rate_limit // 1024, 0, "int")
        self._add_field(speed, "UL Limit (KB/s, 0=∞):", "ul_limit",
                        self.settings.speed.upload_rate_limit // 1024, 1, "int")
        self._add_field(speed, "Active Downloads:", "active_dl",
                        self.settings.speed.max_active_downloads, 2, "int")
        self._add_field(speed, "Active Seeds:", "active_seeds",
                        self.settings.speed.max_active_seeds, 3, "int")
        self._add_field(speed, "Active Torrents:", "active_torrents",
                        self.settings.speed.max_active_torrents, 4, "int")

        # ─── Downloads Tab ───────────────────────────
        dl = tk.Frame(nb, bg=Colors.BG_MEDIUM)
        nb.add(dl, text="  Downloads  ")

        row_frame = tk.Frame(dl, bg=Colors.BG_MEDIUM)
        row_frame.pack(fill="x", padx=12, pady=(12, 0))
        tk.Label(row_frame, text="Default Save Directory:",
                 bg=Colors.BG_MEDIUM, fg=Colors.TEXT_SECONDARY,
                 font=Fonts.SMALL).pack(anchor="w")

        path_row = tk.Frame(dl, bg=Colors.BG_MEDIUM)
        path_row.pack(fill="x", padx=12, pady=(4, 0))

        self._vars["download_dir"] = tk.StringVar(value=self.settings.download_dir)
        tk.Entry(
            path_row, textvariable=self._vars["download_dir"],
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            highlightthickness=1, highlightbackground=Colors.BORDER,
        ).pack(side="left", fill="x", expand=True, ipady=4)

        tk.Button(
            path_row, text="Browse",
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            font=Fonts.SMALL, relief="flat", cursor="hand2",
            command=lambda: self._browse_dir("download_dir"),
        ).pack(side="right", padx=(8, 0), ipady=2)

        # ─── Topology Tab ───────────────────────────
        topo = tk.Frame(nb, bg=Colors.BG_MEDIUM)
        nb.add(topo, text="  Topology  ")

        self._add_check(topo, "Enable Topology Engine", "topo_enabled",
                        self.settings.topology.enabled, 0)
        self._add_field(topo, "Latency Weight:", "lat_weight",
                        self.settings.topology.latency_weight, 1, "float")
        self._add_field(topo, "Throughput Weight:", "thr_weight",
                        self.settings.topology.throughput_weight, 2, "float")
        self._add_field(topo, "Uptime Weight:", "upt_weight",
                        self.settings.topology.uptime_weight, 3, "float")
        self._add_field(topo, "Stability Weight:", "stb_weight",
                        self.settings.topology.stability_weight, 4, "float")
        self._add_field(topo, "Score Update Interval (s):", "score_interval",
                        self.settings.topology.score_update_interval_seconds, 5, "float")

        # ─── UI Tab ──────────────────────────────────
        ui = tk.Frame(nb, bg=Colors.BG_MEDIUM)
        nb.add(ui, text="  UI  ")

        self._add_check(ui, "Minimize to System Tray", "minimize_tray",
                        self.settings.ui.minimize_to_tray, 0)
        self._add_check(ui, "Show Speed in Title Bar", "speed_title",
                        self.settings.ui.show_speed_in_title, 1)
        self._add_check(ui, "Confirm on Delete", "confirm_delete",
                        self.settings.ui.confirm_on_delete, 2)

        # ─── Buttons ─────────────────────────────────
        btn_frame = tk.Frame(self, bg=Colors.BG_DARK)
        btn_frame.pack(fill="x", padx=12, pady=(8, 12))

        tk.Button(
            btn_frame, text="Cancel", width=10,
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat", cursor="hand2",
            command=self._on_cancel,
        ).pack(side="right", ipady=3)

        tk.Button(
            btn_frame, text="Save", width=10,
            bg=Colors.ACCENT_BLUE, fg=Colors.TEXT_INVERSE,
            font=Fonts.BODY_BOLD, relief="flat", cursor="hand2",
            command=self._on_save,
        ).pack(side="right", padx=(0, 8), ipady=3)

    def _add_field(self, parent, label, key, default, row, vtype="str"):
        frame = tk.Frame(parent, bg=Colors.BG_MEDIUM)
        frame.pack(fill="x", padx=12, pady=(8 if row == 0 else 4, 0))

        tk.Label(
            frame, text=label, width=22, anchor="w",
            bg=Colors.BG_MEDIUM, fg=Colors.TEXT_SECONDARY, font=Fonts.SMALL,
        ).pack(side="left")

        if vtype == "int":
            var = tk.IntVar(value=int(default))
        elif vtype == "float":
            var = tk.DoubleVar(value=float(default))
        else:
            var = tk.StringVar(value=str(default))

        self._vars[key] = var

        tk.Entry(
            frame, textvariable=var, width=12,
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            highlightthickness=1, highlightbackground=Colors.BORDER,
        ).pack(side="left", ipady=3)

    def _add_check(self, parent, label, key, default, row):
        frame = tk.Frame(parent, bg=Colors.BG_MEDIUM)
        frame.pack(fill="x", padx=12, pady=(8 if row == 0 else 4, 0))

        var = tk.BooleanVar(value=default)
        self._vars[key] = var

        tk.Checkbutton(
            frame, text=label, variable=var,
            bg=Colors.BG_MEDIUM, fg=Colors.TEXT_PRIMARY,
            selectcolor=Colors.BG_LIGHT,
            activebackground=Colors.BG_MEDIUM,
            activeforeground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY,
        ).pack(anchor="w")

    def _browse_dir(self, key):
        path = filedialog.askdirectory(parent=self, title="Select Directory")
        if path:
            self._vars[key].set(path)

    def _on_save(self):
        try:
            s = self.settings

            # Connection
            s.connection.listen_port = self._vars["listen_port"].get()
            s.connection.max_connections = self._vars["max_connections"].get()
            s.connection.max_connections_per_torrent = self._vars["max_conn_torrent"].get()
            s.connection.enable_dht = self._vars["enable_dht"].get()
            s.connection.enable_pex = self._vars["enable_pex"].get()
            s.connection.enable_lsd = self._vars["enable_lsd"].get()
            s.connection.enable_upnp = self._vars["enable_upnp"].get()

            # Speed
            s.speed.download_rate_limit = self._vars["dl_limit"].get() * 1024
            s.speed.upload_rate_limit = self._vars["ul_limit"].get() * 1024
            s.speed.max_active_downloads = self._vars["active_dl"].get()
            s.speed.max_active_seeds = self._vars["active_seeds"].get()
            s.speed.max_active_torrents = self._vars["active_torrents"].get()

            # Downloads
            s.download_dir = self._vars["download_dir"].get()

            # Topology
            s.topology.enabled = self._vars["topo_enabled"].get()
            s.topology.latency_weight = self._vars["lat_weight"].get()
            s.topology.throughput_weight = self._vars["thr_weight"].get()
            s.topology.uptime_weight = self._vars["upt_weight"].get()
            s.topology.stability_weight = self._vars["stb_weight"].get()
            s.topology.score_update_interval_seconds = self._vars["score_interval"].get()

            # UI
            s.ui.minimize_to_tray = self._vars["minimize_tray"].get()
            s.ui.show_speed_in_title = self._vars["speed_title"].get()
            s.ui.confirm_on_delete = self._vars["confirm_delete"].get()

            s.save()
            self.result = s
            self.destroy()

        except Exception as e:
            tk.messagebox.showerror("Error", f"Invalid settings: {e}", parent=self)

    def _on_cancel(self):
        self.result = None
        self.destroy()
