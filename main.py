"""
TopoTorrent — A qBittorrent-like Torrent Client with Topology-Aware Peer Selection

Main entry point. Initializes and launches the application.

Usage:
    python main.py

Requirements:
    pip install libtorrent customtkinter matplotlib Pillow
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.app import TopoTorrentApp


def main():
    app = TopoTorrentApp()
    app.run()


if __name__ == "__main__":
    main()
