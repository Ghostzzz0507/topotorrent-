"""
Torrent list widget for TopoTorrent.

Displays all torrents in a treeview with columns for name, size,
progress, state, speeds, ETA, peers, and topology score.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from gui.theme import (
    Colors, Fonts, Sizes, format_size, format_speed, format_eta,
    get_state_color, get_score_color,
)


class TorrentListWidget(tk.Frame):
    """
    Treeview-based torrent list with custom styled rows.
    Shows: Name, Size, Progress, Status, DL Speed, UL Speed, ETA,
    Seeds, Peers, Topology Score
    """

    COLUMNS = {
        "name": {"text": "Name", "width": 300, "anchor": "w"},
        "size": {"text": "Size", "width": 85, "anchor": "e"},
        "progress": {"text": "Progress", "width": 90, "anchor": "center"},
        "state": {"text": "Status", "width": 100, "anchor": "center"},
        "dl_speed": {"text": "↓ Speed", "width": 90, "anchor": "e"},
        "ul_speed": {"text": "↑ Speed", "width": 90, "anchor": "e"},
        "eta": {"text": "ETA", "width": 75, "anchor": "center"},
        "seeds": {"text": "Seeds", "width": 70, "anchor": "center"},
        "peers": {"text": "Peers", "width": 70, "anchor": "center"},
        "ratio": {"text": "Ratio", "width": 60, "anchor": "center"},
        "topo_score": {"text": "⚡ Topo", "width": 70, "anchor": "center"},
    }

    def __init__(self, parent, on_select: Optional[Callable] = None):
        super().__init__(parent, bg=Colors.BG_DARK)
        self.on_select = on_select
        self._items: Dict[str, str] = {}  # info_hash -> treeview item id
        self._selected_hash: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self):
        # Style configuration
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Torrent.Treeview",
            background=Colors.BG_MEDIUM,
            foreground=Colors.TEXT_PRIMARY,
            fieldbackground=Colors.BG_MEDIUM,
            borderwidth=0,
            rowheight=Sizes.ROW_HEIGHT,
            font=Fonts.SMALL,
        )
        style.configure("Torrent.Treeview.Heading",
            background=Colors.BG_LIGHT,
            foreground=Colors.TEXT_SECONDARY,
            borderwidth=0,
            font=Fonts.SMALL_BOLD,
            relief="flat",
        )
        style.map("Torrent.Treeview",
            background=[("selected", Colors.BG_HOVER)],
            foreground=[("selected", Colors.ACCENT_BLUE)],
        )
        style.map("Torrent.Treeview.Heading",
            background=[("active", Colors.BG_HOVER)],
        )

        # Treeview
        cols = list(self.COLUMNS.keys())
        self.tree = ttk.Treeview(
            self,
            columns=cols,
            show="headings",
            style="Torrent.Treeview",
            selectmode="browse",
        )

        for col_id, col_config in self.COLUMNS.items():
            self.tree.heading(col_id, text=col_config["text"],
                             anchor=col_config["anchor"])
            self.tree.column(col_id, width=col_config["width"],
                           anchor=col_config["anchor"], minwidth=50)

        # Scrollbar
        scrollbar = ttk.Scrollbar(self, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        # Layout
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Events
        self.tree.bind("<<TreeviewSelect>>", self._on_selection)
        self.tree.bind("<Button-3>", self._on_right_click)

        # Context menu
        self._context_menu = tk.Menu(self, tearoff=0,
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER,
            activeforeground=Colors.ACCENT_BLUE,
            font=Fonts.SMALL,
            borderwidth=1,
            relief="flat",
        )
        self._context_menu.add_command(label="▶  Resume", command=lambda: self._context_action("resume"))
        self._context_menu.add_command(label="⏸  Pause", command=lambda: self._context_action("pause"))
        self._context_menu.add_separator()
        self._context_menu.add_command(label="🗑  Remove", command=lambda: self._context_action("remove"))
        self._context_menu.add_command(label="🗑  Remove + Delete Files",
                                       command=lambda: self._context_action("remove_files"))
        self._context_menu.add_separator()
        self._context_menu.add_command(label="📂  Open Folder",
                                       command=lambda: self._context_action("open_folder"))
        self._context_menu.add_command(label="🔄  Force Reannounce",
                                       command=lambda: self._context_action("reannounce"))

        self._context_callback: Optional[Callable] = None

    def set_context_callback(self, callback: Callable):
        """Set callback for context menu actions: callback(info_hash, action)."""
        self._context_callback = callback

    def update_torrents(self, all_status: Dict[str, dict]):
        """Update the entire torrent list from status dict."""
        existing_hashes = set(self._items.keys())
        current_hashes = set(all_status.keys())

        # Remove gone torrents
        for ih in existing_hashes - current_hashes:
            item_id = self._items.pop(ih, None)
            if item_id:
                try:
                    self.tree.delete(item_id)
                except Exception:
                    pass

        # Update or add torrents
        for ih, status in all_status.items():
            values = self._format_values(status)
            if ih in self._items:
                try:
                    self.tree.item(self._items[ih], values=values)
                except Exception:
                    pass
            else:
                try:
                    item_id = self.tree.insert("", "end", values=values)
                    self._items[ih] = item_id
                except Exception:
                    pass

    def get_selected_hash(self) -> Optional[str]:
        """Get the info_hash of the currently selected torrent."""
        return self._selected_hash

    def _format_values(self, status: dict) -> tuple:
        """Format status dict into treeview column values."""
        progress_pct = f"{status['progress'] * 100:.1f}%"
        topo = f"{status.get('topology_score', 0):.2f}"

        return (
            status["name"],
            format_size(status["total_size"]),
            progress_pct,
            status["state"],
            format_speed(status["download_speed"]),
            format_speed(status["upload_speed"]),
            format_eta(status["eta"]),
            str(status["num_seeds"]),
            str(status["num_peers"]),
            f"{status['ratio']:.2f}",
            topo,
        )

    def _on_selection(self, event):
        selection = self.tree.selection()
        if not selection:
            self._selected_hash = None
            return

        item_id = selection[0]
        # Find info_hash by item_id
        for ih, iid in self._items.items():
            if iid == item_id:
                self._selected_hash = ih
                if self.on_select:
                    self.on_select(ih)
                break

    def _on_right_click(self, event):
        # Select item under cursor
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._on_selection(None)
            self._context_menu.post(event.x_root, event.y_root)

    def _context_action(self, action: str):
        if self._selected_hash and self._context_callback:
            self._context_callback(self._selected_hash, action)
