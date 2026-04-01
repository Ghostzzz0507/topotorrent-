"""
Speed graph widget for TopoTorrent.

Displays a real-time line chart of download and upload speeds
using matplotlib embedded in a tkinter canvas.
"""

import tkinter as tk
from collections import deque
from typing import List, Tuple

from gui.theme import Colors, Fonts, format_speed

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


class SpeedGraphWidget(tk.Frame):
    """
    Real-time speed graph showing download and upload rates
    over the last 5 minutes.
    """

    def __init__(self, parent, max_points: int = 300):
        super().__init__(parent, bg=Colors.BG_MEDIUM)
        self.max_points = max_points
        self._dl_data = deque([0.0] * max_points, maxlen=max_points)
        self._ul_data = deque([0.0] * max_points, maxlen=max_points)
        self._canvas = None
        self._setup_ui()

    def _setup_ui(self):
        if not HAS_MATPLOTLIB:
            label = tk.Label(
                self,
                text="Install matplotlib for speed graphs",
                bg=Colors.BG_MEDIUM,
                fg=Colors.TEXT_MUTED,
                font=Fonts.SMALL,
            )
            label.pack(expand=True)
            return

        self._fig = Figure(figsize=(6, 1.8), dpi=100)
        self._fig.patch.set_facecolor(Colors.BG_MEDIUM)

        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor(Colors.BG_DARK)

        # Style the axes
        self._ax.tick_params(
            colors=Colors.TEXT_MUTED, labelsize=8, length=0
        )
        self._ax.spines["top"].set_visible(False)
        self._ax.spines["right"].set_visible(False)
        self._ax.spines["bottom"].set_color(Colors.GRAPH_AXIS)
        self._ax.spines["left"].set_color(Colors.GRAPH_AXIS)
        self._ax.grid(True, color=Colors.GRAPH_GRID, alpha=0.3, linewidth=0.5)

        # Initial plot
        x = list(range(self.max_points))
        self._dl_line, = self._ax.plot(
            x, list(self._dl_data),
            color=Colors.GRAPH_DL_LINE, linewidth=1.5, label="Download"
        )
        self._ul_line, = self._ax.plot(
            x, list(self._ul_data),
            color=Colors.GRAPH_UL_LINE, linewidth=1.5, label="Upload"
        )

        # Fill under curves
        self._dl_fill = self._ax.fill_between(
            x, list(self._dl_data), alpha=0.15, color=Colors.GRAPH_DL_LINE
        )
        self._ul_fill = self._ax.fill_between(
            x, list(self._ul_data), alpha=0.10, color=Colors.GRAPH_UL_LINE
        )

        self._ax.set_xlim(0, self.max_points)
        self._ax.set_ylim(0, 1024 * 100)  # 100 KB/s initial
        self._ax.set_ylabel("Speed", color=Colors.TEXT_MUTED, fontsize=8)

        # Legend
        legend = self._ax.legend(
            loc="upper right", fontsize=7,
            facecolor=Colors.BG_LIGHT, edgecolor=Colors.BORDER,
            labelcolor=Colors.TEXT_SECONDARY,
            framealpha=0.8,
        )

        self._fig.tight_layout(pad=0.5)

        self._canvas = FigureCanvasTkAgg(self._fig, self)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        self._canvas.draw()

    def update_speeds(self, dl_speed: float, ul_speed: float):
        """Add new speed data point and redraw."""
        if not HAS_MATPLOTLIB or not self._canvas:
            return

        self._dl_data.append(dl_speed)
        self._ul_data.append(ul_speed)

        x = list(range(self.max_points))
        dl = list(self._dl_data)
        ul = list(self._ul_data)

        # Update lines
        self._dl_line.set_ydata(dl)
        self._ul_line.set_ydata(ul)

        # Update fills
        self._dl_fill.remove()
        self._ul_fill.remove()
        self._dl_fill = self._ax.fill_between(
            x, dl, alpha=0.15, color=Colors.GRAPH_DL_LINE
        )
        self._ul_fill = self._ax.fill_between(
            x, ul, alpha=0.10, color=Colors.GRAPH_UL_LINE
        )

        # Auto-scale Y axis
        max_val = max(max(dl), max(ul), 1024)
        self._ax.set_ylim(0, max_val * 1.2)

        # Better Y labels
        self._ax.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda v, _: format_speed(v))
        )

        try:
            self._canvas.draw_idle()
        except Exception:
            pass

    def clear(self):
        """Reset graph data."""
        self._dl_data = deque([0.0] * self.max_points, maxlen=self.max_points)
        self._ul_data = deque([0.0] * self.max_points, maxlen=self.max_points)
