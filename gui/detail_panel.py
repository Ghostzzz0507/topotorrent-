"""
Detail panel for TopoTorrent.

Tabbed panel at the bottom of the main window showing detailed info
about the selected torrent: General, Peers, Files, Trackers, Speed.
"""

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from gui.theme import (
    Colors, Fonts, Sizes, format_size, format_speed, format_eta,
    get_score_color,
)
from gui.speed_graph import SpeedGraphWidget


class DetailPanel(tk.Frame):
    """
    Tabbed detail panel showing info for the selected torrent.
    """

    def __init__(self, parent):
        super().__init__(parent, bg=Colors.BG_DARK, height=280)

        self._current_hash: Optional[str] = None
        self._setup_style()
        self._setup_ui()

    def _setup_style(self):
        style = ttk.Style()
        style.configure("Detail.TNotebook",
            background=Colors.BG_DARK,
            borderwidth=0,
            tabmargins=[0, 0, 0, 0],
        )
        style.configure("Detail.TNotebook.Tab",
            background=Colors.BG_LIGHT,
            foreground=Colors.TEXT_SECONDARY,
            padding=[12, 4],
            font=Fonts.SMALL_BOLD,
            borderwidth=0,
        )
        style.map("Detail.TNotebook.Tab",
            background=[("selected", Colors.BG_MEDIUM)],
            foreground=[("selected", Colors.ACCENT_BLUE)],
        )

    def _setup_ui(self):
        self.notebook = ttk.Notebook(self, style="Detail.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=1, pady=(1, 0))

        # ─── General Tab ─────────────────────────────────
        self.general_frame = tk.Frame(self.notebook, bg=Colors.BG_MEDIUM)
        self.notebook.add(self.general_frame, text="  General  ")
        self._setup_general_tab()

        # ─── Peers Tab ───────────────────────────────────
        self.peers_frame = tk.Frame(self.notebook, bg=Colors.BG_MEDIUM)
        self.notebook.add(self.peers_frame, text="  Peers  ")
        self._setup_peers_tab()

        # ─── Files Tab ───────────────────────────────────
        self.files_frame = tk.Frame(self.notebook, bg=Colors.BG_MEDIUM)
        self.notebook.add(self.files_frame, text="  Files  ")
        self._setup_files_tab()

        # ─── Trackers Tab ────────────────────────────────
        self.trackers_frame = tk.Frame(self.notebook, bg=Colors.BG_MEDIUM)
        self.notebook.add(self.trackers_frame, text="  Trackers  ")
        self._setup_trackers_tab()

        # ─── Speed Tab ───────────────────────────────────
        self.speed_frame = tk.Frame(self.notebook, bg=Colors.BG_MEDIUM)
        self.notebook.add(self.speed_frame, text="  Speed  ")
        self._setup_speed_tab()

    # ═══ General Tab ═════════════════════════════════════════════════
    def _setup_general_tab(self):
        self._gen_labels = {}

        fields = [
            ("Name:", "name"),
            ("Save Path:", "save_path"),
            ("Total Size:", "total_size"),
            ("Progress:", "progress"),
            ("Status:", "state"),
            ("Downloaded:", "downloaded"),
            ("Uploaded:", "uploaded"),
            ("Ratio:", "ratio"),
            ("Info Hash:", "info_hash"),
            ("Topology Score:", "topology_score"),
            ("Health:", "bottleneck"),
        ]

        for i, (label_text, key) in enumerate(fields):
            row = i // 2
            col = (i % 2) * 2

            label = tk.Label(
                self.general_frame, text=label_text,
                bg=Colors.BG_MEDIUM, fg=Colors.TEXT_SECONDARY,
                font=Fonts.SMALL, anchor="e",
            )
            label.grid(row=row, column=col, sticky="e", padx=(10, 4), pady=2)

            value = tk.Label(
                self.general_frame, text="—",
                bg=Colors.BG_MEDIUM, fg=Colors.TEXT_PRIMARY,
                font=Fonts.SMALL, anchor="w",
            )
            value.grid(row=row, column=col + 1, sticky="w", padx=(0, 20), pady=2)
            self._gen_labels[key] = value

        self.general_frame.grid_columnconfigure(1, weight=1)
        self.general_frame.grid_columnconfigure(3, weight=1)

    # ═══ Peers Tab ═══════════════════════════════════════════════════
    def _setup_peers_tab(self):
        style = ttk.Style()
        style.configure("Peers.Treeview",
            background=Colors.BG_MEDIUM,
            foreground=Colors.TEXT_PRIMARY,
            fieldbackground=Colors.BG_MEDIUM,
            borderwidth=0,
            rowheight=22,
            font=Fonts.TINY,
        )
        style.configure("Peers.Treeview.Heading",
            background=Colors.BG_LIGHT,
            foreground=Colors.TEXT_SECONDARY,
            font=(Fonts.FAMILY, 8, "bold"),
            borderwidth=0,
            relief="flat",
        )
        style.map("Peers.Treeview",
            background=[("selected", Colors.BG_HOVER)],
        )

        cols = ("ip", "client", "flags", "progress", "dl_speed", "ul_speed",
                "downloaded", "uploaded", "topo_score", "reputation")
        self.peers_tree = ttk.Treeview(
            self.peers_frame, columns=cols, show="headings",
            style="Peers.Treeview",
        )

        headings = {
            "ip": ("IP Address", 130),
            "client": ("Client", 110),
            "flags": ("Flags", 60),
            "progress": ("Progress", 65),
            "dl_speed": ("↓ Speed", 75),
            "ul_speed": ("↑ Speed", 75),
            "downloaded": ("Downloaded", 80),
            "uploaded": ("Uploaded", 80),
            "topo_score": ("⚡ Score", 60),
            "reputation": ("🏆 Rep", 55),
        }

        for col, (text, width) in headings.items():
            self.peers_tree.heading(col, text=text)
            anchor = "e" if col in ("dl_speed", "ul_speed", "downloaded", "uploaded") else "center"
            if col == "ip":
                anchor = "w"
            self.peers_tree.column(col, width=width, anchor=anchor, minwidth=40)

        peer_scroll = ttk.Scrollbar(self.peers_frame, orient="vertical",
                                    command=self.peers_tree.yview)
        self.peers_tree.configure(yscrollcommand=peer_scroll.set)
        self.peers_tree.pack(side="left", fill="both", expand=True)
        peer_scroll.pack(side="right", fill="y")

    # ═══ Files Tab ═══════════════════════════════════════════════════
    def _setup_files_tab(self):
        style = ttk.Style()
        style.configure("Files.Treeview",
            background=Colors.BG_MEDIUM,
            foreground=Colors.TEXT_PRIMARY,
            fieldbackground=Colors.BG_MEDIUM,
            borderwidth=0,
            rowheight=22,
            font=Fonts.TINY,
        )
        style.configure("Files.Treeview.Heading",
            background=Colors.BG_LIGHT,
            foreground=Colors.TEXT_SECONDARY,
            font=(Fonts.FAMILY, 8, "bold"),
            borderwidth=0,
            relief="flat",
        )

        cols = ("name", "size", "progress", "priority")
        self.files_tree = ttk.Treeview(
            self.files_frame, columns=cols, show="headings",
            style="Files.Treeview",
        )

        headings = {
            "name": ("File", 350, "w"),
            "size": ("Size", 90, "e"),
            "progress": ("Progress", 80, "center"),
            "priority": ("Priority", 80, "center"),
        }

        for col, (text, width, anchor) in headings.items():
            self.files_tree.heading(col, text=text)
            self.files_tree.column(col, width=width, anchor=anchor, minwidth=40)

        files_scroll = ttk.Scrollbar(self.files_frame, orient="vertical",
                                     command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=files_scroll.set)
        self.files_tree.pack(side="left", fill="both", expand=True)
        files_scroll.pack(side="right", fill="y")

    # ═══ Trackers Tab ════════════════════════════════════════════════
    def _setup_trackers_tab(self):
        style = ttk.Style()
        style.configure("Trackers.Treeview",
            background=Colors.BG_MEDIUM,
            foreground=Colors.TEXT_PRIMARY,
            fieldbackground=Colors.BG_MEDIUM,
            borderwidth=0,
            rowheight=22,
            font=Fonts.TINY,
        )
        style.configure("Trackers.Treeview.Heading",
            background=Colors.BG_LIGHT,
            foreground=Colors.TEXT_SECONDARY,
            font=(Fonts.FAMILY, 8, "bold"),
            borderwidth=0,
            relief="flat",
        )

        cols = ("url", "status", "peers", "message", "tier")
        self.trackers_tree = ttk.Treeview(
            self.trackers_frame, columns=cols, show="headings",
            style="Trackers.Treeview",
        )

        headings = {
            "url": ("URL", 300, "w"),
            "status": ("Status", 100, "center"),
            "peers": ("Peers", 60, "center"),
            "message": ("Message", 200, "w"),
            "tier": ("Tier", 40, "center"),
        }

        for col, (text, width, anchor) in headings.items():
            self.trackers_tree.heading(col, text=text)
            self.trackers_tree.column(col, width=width, anchor=anchor, minwidth=40)

        tracker_scroll = ttk.Scrollbar(self.trackers_frame, orient="vertical",
                                       command=self.trackers_tree.yview)
        self.trackers_tree.configure(yscrollcommand=tracker_scroll.set)
        self.trackers_tree.pack(side="left", fill="both", expand=True)
        tracker_scroll.pack(side="right", fill="y")

    # ═══ Speed Tab ═══════════════════════════════════════════════════
    def _setup_speed_tab(self):
        self.speed_graph = SpeedGraphWidget(self.speed_frame)
        self.speed_graph.pack(fill="both", expand=True, padx=4, pady=4)

    # ═══ Update Methods ══════════════════════════════════════════════
    def set_torrent(self, info_hash: str):
        """Set which torrent to display details for."""
        self._current_hash = info_hash

    def update_general(self, status: dict):
        """Update the General tab."""
        if not status:
            return

        updates = {
            "name": status.get("name", "—"),
            "save_path": status.get("save_path", "—"),
            "total_size": format_size(status.get("total_size", 0)),
            "progress": f"{status.get('progress', 0) * 100:.1f}%",
            "state": status.get("state", "—"),
            "downloaded": format_size(status.get("total_downloaded", 0)),
            "uploaded": format_size(status.get("total_uploaded", 0)),
            "ratio": f"{status.get('ratio', 0):.3f}",
            "info_hash": status.get("info_hash", "—"),
            "topology_score": f"{status.get('topology_score', 0):.3f}",
            "bottleneck": status.get("bottleneck", "") or "✅ Healthy",
        }

        for key, value in updates.items():
            if key in self._gen_labels:
                self._gen_labels[key].configure(text=str(value))

                # Color topology score
                if key == "topology_score":
                    score = status.get("topology_score", 0)
                    self._gen_labels[key].configure(
                        fg=get_score_color(score)
                    )

                # Color health/bottleneck
                if key == "bottleneck":
                    msg = status.get("bottleneck", "")
                    if msg and "🔴" in msg:
                        self._gen_labels[key].configure(fg="#FF4444")
                    elif msg and ("🟡" in msg or "⚠" in msg):
                        self._gen_labels[key].configure(fg="#FFAA00")
                    else:
                        self._gen_labels[key].configure(fg=Colors.ACCENT_GREEN)

    def update_peers(self, peers: list, topology_scores: dict = None,
                     reputation_scores: dict = None):
        """Update the Peers tab."""
        self.peers_tree.delete(*self.peers_tree.get_children())

        for peer in peers:
            ip = getattr(peer, "ip", peer.get("ip", "")) if isinstance(peer, dict) else peer.ip
            port = getattr(peer, "port", peer.get("port", 0)) if isinstance(peer, dict) else peer.port
            client = getattr(peer, "client", "") if not isinstance(peer, dict) else peer.get("client", "")
            flags = getattr(peer, "flags", "") if not isinstance(peer, dict) else peer.get("flags", "")
            progress = getattr(peer, "progress", 0) if not isinstance(peer, dict) else peer.get("progress", 0)
            dl_speed = getattr(peer, "download_speed", 0) if not isinstance(peer, dict) else peer.get("download_speed", 0)
            ul_speed = getattr(peer, "upload_speed", 0) if not isinstance(peer, dict) else peer.get("upload_speed", 0)
            total_dl = getattr(peer, "total_downloaded", 0) if not isinstance(peer, dict) else peer.get("total_downloaded", 0)
            total_ul = getattr(peer, "total_uploaded", 0) if not isinstance(peer, dict) else peer.get("total_uploaded", 0)

            # Get topology score
            topo_score = 0.0
            if topology_scores:
                key = f"{ip}:{port}"
                topo_score = topology_scores.get(key, 0.0)

            # Get reputation score
            rep_score = 0.5
            if reputation_scores:
                key = f"{ip}:{port}"
                rep_score = reputation_scores.get(key, 0.5)

            self.peers_tree.insert("", "end", values=(
                f"{ip}:{port}",
                client,
                flags,
                f"{progress * 100:.0f}%",
                format_speed(dl_speed),
                format_speed(ul_speed),
                format_size(total_dl),
                format_size(total_ul),
                f"{topo_score:.2f}",
                f"{rep_score:.2f}",
            ))

    def update_files(self, files: list):
        """Update the Files tab."""
        self.files_tree.delete(*self.files_tree.get_children())

        priority_names = {0: "Skip", 1: "Low", 4: "Normal", 7: "High"}

        for f in files:
            path = getattr(f, "path", "") if not isinstance(f, dict) else f.get("path", "")
            size = getattr(f, "size", 0) if not isinstance(f, dict) else f.get("size", 0)
            progress = getattr(f, "progress", 0) if not isinstance(f, dict) else f.get("progress", 0)
            priority = getattr(f, "priority", 4) if not isinstance(f, dict) else f.get("priority", 4)

            self.files_tree.insert("", "end", values=(
                path,
                format_size(size),
                f"{progress * 100:.1f}%",
                priority_names.get(priority, str(priority)),
            ))

    def update_trackers(self, trackers: list):
        """Update the Trackers tab."""
        self.trackers_tree.delete(*self.trackers_tree.get_children())

        for t in trackers:
            url = getattr(t, "url", "") if not isinstance(t, dict) else t.get("url", "")
            status = getattr(t, "status", "") if not isinstance(t, dict) else t.get("status", "")
            peers = getattr(t, "peers", 0) if not isinstance(t, dict) else t.get("peers", 0)
            message = getattr(t, "message", "") if not isinstance(t, dict) else t.get("message", "")
            tier = getattr(t, "tier", 0) if not isinstance(t, dict) else t.get("tier", 0)

            self.trackers_tree.insert("", "end", values=(
                url, status, peers, message, tier,
            ))

    def update_speed(self, dl_speed: float, ul_speed: float):
        """Update the Speed tab graph."""
        self.speed_graph.update_speeds(dl_speed, ul_speed)
