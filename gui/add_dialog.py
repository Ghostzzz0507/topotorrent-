"""
Add torrent dialog for TopoTorrent.

Dialog for adding new torrents via .torrent file or magnet link,
with options for save path and starting paused.
"""

import tkinter as tk
from tkinter import filedialog
from typing import Optional, Tuple

from gui.theme import Colors, Fonts, Sizes


class AddTorrentDialog(tk.Toplevel):
    """
    Modal dialog for adding a torrent.

    Returns:
        (type, value, save_path, start_paused) or None if cancelled
        type: "file" or "magnet"
    """

    def __init__(self, parent, default_save_path: str):
        super().__init__(parent)
        self.title("Add Torrent")
        self.configure(bg=Colors.BG_DARK)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result: Optional[Tuple] = None
        self._default_save_path = default_save_path
        self._mode = tk.StringVar(value="file")
        self._file_path = tk.StringVar()
        self._magnet_link = tk.StringVar()
        self._save_path = tk.StringVar(value=default_save_path)
        self._start_paused = tk.BooleanVar(value=False)

        self._setup_ui()

        # Center on parent
        self.update_idletasks()
        w, h = 560, 380
        self.geometry(f"{w}x{h}")
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda e: self._on_cancel())
        self.bind("<Return>", lambda e: self._on_ok())

    def _setup_ui(self):
        # Title
        title = tk.Label(
            self, text="Add Torrent",
            bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY,
            font=Fonts.HEADING,
        )
        title.pack(pady=(16, 12))

        # Mode selection frame
        mode_frame = tk.Frame(self, bg=Colors.BG_DARK)
        mode_frame.pack(fill="x", padx=20)

        file_radio = tk.Radiobutton(
            mode_frame, text="Torrent File", variable=self._mode,
            value="file", bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY,
            selectcolor=Colors.BG_LIGHT, activebackground=Colors.BG_DARK,
            activeforeground=Colors.ACCENT_BLUE, font=Fonts.BODY,
            command=self._on_mode_change,
        )
        file_radio.pack(side="left", padx=(0, 20))

        magnet_radio = tk.Radiobutton(
            mode_frame, text="Magnet Link", variable=self._mode,
            value="magnet", bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY,
            selectcolor=Colors.BG_LIGHT, activebackground=Colors.BG_DARK,
            activeforeground=Colors.ACCENT_BLUE, font=Fonts.BODY,
            command=self._on_mode_change,
        )
        magnet_radio.pack(side="left")

        # ─── File path ───────────────────────────────
        self._file_frame = tk.Frame(self, bg=Colors.BG_DARK)
        self._file_frame.pack(fill="x", padx=20, pady=(12, 0))

        tk.Label(
            self._file_frame, text="Torrent File:",
            bg=Colors.BG_DARK, fg=Colors.TEXT_SECONDARY,
            font=Fonts.SMALL, anchor="w",
        ).pack(fill="x")

        file_row = tk.Frame(self._file_frame, bg=Colors.BG_DARK)
        file_row.pack(fill="x", pady=(4, 0))

        self._file_entry = tk.Entry(
            file_row, textvariable=self._file_path,
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            highlightthickness=1, highlightcolor=Colors.ACCENT_BLUE,
            highlightbackground=Colors.BORDER,
        )
        self._file_entry.pack(side="left", fill="x", expand=True, ipady=4)

        browse_btn = tk.Button(
            file_row, text="Browse",
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER,
            activeforeground=Colors.ACCENT_BLUE,
            font=Fonts.SMALL, relief="flat",
            cursor="hand2", command=self._browse_file,
        )
        browse_btn.pack(side="right", padx=(8, 0), ipady=2)

        # ─── Magnet link ─────────────────────────────
        self._magnet_frame = tk.Frame(self, bg=Colors.BG_DARK)

        tk.Label(
            self._magnet_frame, text="Magnet Link:",
            bg=Colors.BG_DARK, fg=Colors.TEXT_SECONDARY,
            font=Fonts.SMALL, anchor="w",
        ).pack(fill="x")

        self._magnet_entry = tk.Entry(
            self._magnet_frame, textvariable=self._magnet_link,
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            highlightthickness=1, highlightcolor=Colors.ACCENT_BLUE,
            highlightbackground=Colors.BORDER,
        )
        self._magnet_entry.pack(fill="x", pady=(4, 0), ipady=4)

        # ─── Save path ──────────────────────────────
        save_frame = tk.Frame(self, bg=Colors.BG_DARK)
        save_frame.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(
            save_frame, text="Save To:",
            bg=Colors.BG_DARK, fg=Colors.TEXT_SECONDARY,
            font=Fonts.SMALL, anchor="w",
        ).pack(fill="x")

        save_row = tk.Frame(save_frame, bg=Colors.BG_DARK)
        save_row.pack(fill="x", pady=(4, 0))

        self._save_entry = tk.Entry(
            save_row, textvariable=self._save_path,
            bg=Colors.BG_INPUT, fg=Colors.TEXT_PRIMARY,
            insertbackground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            highlightthickness=1, highlightcolor=Colors.ACCENT_BLUE,
            highlightbackground=Colors.BORDER,
        )
        self._save_entry.pack(side="left", fill="x", expand=True, ipady=4)

        save_browse = tk.Button(
            save_row, text="Browse",
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER,
            activeforeground=Colors.ACCENT_BLUE,
            font=Fonts.SMALL, relief="flat",
            cursor="hand2", command=self._browse_save,
        )
        save_browse.pack(side="right", padx=(8, 0), ipady=2)

        # ─── Start paused ────────────────────────────
        paused_check = tk.Checkbutton(
            self, text="Start paused", variable=self._start_paused,
            bg=Colors.BG_DARK, fg=Colors.TEXT_PRIMARY,
            selectcolor=Colors.BG_LIGHT, activebackground=Colors.BG_DARK,
            activeforeground=Colors.TEXT_PRIMARY, font=Fonts.BODY,
        )
        paused_check.pack(anchor="w", padx=20, pady=(16, 0))

        # ─── Buttons ─────────────────────────────────
        btn_frame = tk.Frame(self, bg=Colors.BG_DARK)
        btn_frame.pack(fill="x", padx=20, pady=(20, 16))

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", width=12,
            bg=Colors.BG_LIGHT, fg=Colors.TEXT_PRIMARY,
            activebackground=Colors.BG_HOVER,
            activeforeground=Colors.TEXT_PRIMARY,
            font=Fonts.BODY, relief="flat",
            cursor="hand2", command=self._on_cancel,
        )
        cancel_btn.pack(side="right", ipady=4)

        ok_btn = tk.Button(
            btn_frame, text="Add Torrent", width=14,
            bg=Colors.ACCENT_BLUE, fg=Colors.TEXT_INVERSE,
            activebackground="#4a9eff",
            activeforeground=Colors.TEXT_INVERSE,
            font=Fonts.BODY_BOLD, relief="flat",
            cursor="hand2", command=self._on_ok,
        )
        ok_btn.pack(side="right", padx=(0, 8), ipady=4)

    def _on_mode_change(self):
        if self._mode.get() == "file":
            self._magnet_frame.pack_forget()
            self._file_frame.pack(fill="x", padx=20, pady=(12, 0),
                                  after=self.children.get("!frame", list(self.children.values())[1]))
        else:
            self._file_frame.pack_forget()
            self._magnet_frame.pack(fill="x", padx=20, pady=(12, 0),
                                    after=list(self.children.values())[1])

    def _browse_file(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select Torrent File",
            filetypes=[("Torrent Files", "*.torrent"), ("All Files", "*.*")],
        )
        if path:
            self._file_path.set(path)

    def _browse_save(self):
        path = filedialog.askdirectory(
            parent=self,
            title="Select Save Directory",
            initialdir=self._save_path.get(),
        )
        if path:
            self._save_path.set(path)

    def _on_ok(self):
        mode = self._mode.get()
        if mode == "file":
            value = self._file_path.get().strip()
            if not value:
                return
        else:
            value = self._magnet_link.get().strip()
            if not value or not value.startswith("magnet:"):
                return

        self.result = (
            mode,
            value,
            self._save_path.get(),
            self._start_paused.get(),
        )
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()
