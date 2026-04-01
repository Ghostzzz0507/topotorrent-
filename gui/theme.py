"""
Dark theme configuration for TopoTorrent.

Defines a modern, premium dark color palette and font configuration
used consistently across all GUI widgets.
"""


# ─── Color Palette ───────────────────────────────────────────────────
class Colors:
    # Backgrounds
    BG_DARK = "#0d1117"         # Main window background
    BG_MEDIUM = "#161b22"       # Panel backgrounds
    BG_LIGHT = "#21262d"        # Card / widget backgrounds
    BG_HOVER = "#30363d"        # Hover states
    BG_INPUT = "#0d1117"        # Input field backgrounds
    BG_SELECTED = "#1f6feb22"   # Selected row (with transparency)

    # Borders
    BORDER = "#30363d"
    BORDER_LIGHT = "#484f58"
    BORDER_ACCENT = "#1f6feb"

    # Text
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED = "#6e7681"
    TEXT_INVERSE = "#0d1117"

    # Accent colors
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#3fb950"
    ACCENT_ORANGE = "#d29922"
    ACCENT_RED = "#f85149"
    ACCENT_PURPLE = "#bc8cff"
    ACCENT_CYAN = "#39d2c0"
    ACCENT_TEAL = "#2ea043"

    # Gradients (used for progress bars, etc.)
    GRADIENT_START = "#1f6feb"
    GRADIENT_END = "#58a6ff"

    # Topology score colors
    TOPO_EXCELLENT = "#3fb950"     # > 0.7
    TOPO_GOOD = "#58a6ff"         # 0.4 - 0.7
    TOPO_FAIR = "#d29922"         # 0.2 - 0.4
    TOPO_POOR = "#f85149"         # < 0.2

    # State colors
    STATE_DOWNLOADING = "#58a6ff"
    STATE_SEEDING = "#3fb950"
    STATE_PAUSED = "#8b949e"
    STATE_ERROR = "#f85149"
    STATE_CHECKING = "#d29922"
    STATE_QUEUED = "#6e7681"

    # Progress bar
    PROGRESS_BG = "#21262d"
    PROGRESS_DL = "#58a6ff"
    PROGRESS_SEED = "#3fb950"
    PROGRESS_CHECK = "#d29922"

    # Speed graph
    GRAPH_DL_LINE = "#58a6ff"
    GRAPH_DL_FILL = "#58a6ff33"
    GRAPH_UL_LINE = "#3fb950"
    GRAPH_UL_FILL = "#3fb95033"
    GRAPH_GRID = "#21262d"
    GRAPH_AXIS = "#484f58"


# ─── Fonts ───────────────────────────────────────────────────────────
class Fonts:
    FAMILY = "Segoe UI"
    MONO = "Cascadia Code"

    TITLE_SIZE = 16
    HEADING_SIZE = 13
    BODY_SIZE = 11
    SMALL_SIZE = 10
    TINY_SIZE = 9

    TITLE = (FAMILY, TITLE_SIZE, "bold")
    HEADING = (FAMILY, HEADING_SIZE, "bold")
    BODY = (FAMILY, BODY_SIZE)
    BODY_BOLD = (FAMILY, BODY_SIZE, "bold")
    SMALL = (FAMILY, SMALL_SIZE)
    SMALL_BOLD = (FAMILY, SMALL_SIZE, "bold")
    TINY = (FAMILY, TINY_SIZE)
    MONO_BODY = (MONO, BODY_SIZE)
    MONO_SMALL = (MONO, SMALL_SIZE)


# ─── Sizes ───────────────────────────────────────────────────────────
class Sizes:
    TOOLBAR_HEIGHT = 48
    STATUSBAR_HEIGHT = 28
    TAB_HEIGHT = 32
    ROW_HEIGHT = 28
    PADDING = 8
    PADDING_LARGE = 16
    BORDER_RADIUS = 6
    ICON_SIZE = 20
    PROGRESS_BAR_HEIGHT = 16


# ─── Score Color Helper ──────────────────────────────────────────────
def get_score_color(score: float) -> str:
    """Return a color based on topology score value."""
    if score >= 0.7:
        return Colors.TOPO_EXCELLENT
    elif score >= 0.4:
        return Colors.TOPO_GOOD
    elif score >= 0.2:
        return Colors.TOPO_FAIR
    else:
        return Colors.TOPO_POOR


def get_state_color(state: str) -> str:
    """Return a color for the torrent state."""
    state_lower = state.lower()
    if "download" in state_lower:
        return Colors.STATE_DOWNLOADING
    elif "seed" in state_lower:
        return Colors.STATE_SEEDING
    elif "paus" in state_lower:
        return Colors.STATE_PAUSED
    elif "error" in state_lower or "invalid" in state_lower:
        return Colors.STATE_ERROR
    elif "check" in state_lower:
        return Colors.STATE_CHECKING
    elif "queue" in state_lower or "allocat" in state_lower:
        return Colors.STATE_QUEUED
    elif "metadata" in state_lower:
        return Colors.STATE_CHECKING
    elif "finish" in state_lower:
        return Colors.STATE_SEEDING
    else:
        return Colors.TEXT_SECONDARY


# ─── Utility Functions ───────────────────────────────────────────────
def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(size_bytes)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}" if idx > 0 else f"{int(size)} B"


def format_speed(speed_bps: float) -> str:
    """Format bytes/second to human-readable speed."""
    if speed_bps <= 0:
        return "0 B/s"
    return f"{format_size(int(speed_bps))}/s"


def format_eta(seconds: int) -> str:
    """Format seconds to HH:MM:SS or meaningful text."""
    if seconds < 0:
        return "∞"
    if seconds == 0:
        return "0s"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 99:
        return "∞"
    elif h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def format_ratio(ratio: float) -> str:
    """Format upload/download ratio."""
    return f"{ratio:.2f}"
