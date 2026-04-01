"""
Diagnostic script for TopoTorrent peer connections.
Tests each step independently to find the exact failure point.

Usage: python debug_peers.py path/to/file.torrent
"""

import hashlib
import os
import socket
import struct
import random
import sys
import time

# Add project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.pure_engine import (
    parse_torrent_file, _extract_raw_info_value,
    tracker_announce, udp_tracker_announce, http_tracker_announce,
    bencode, bdecode, generate_peer_id,
)


def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_peers.py path/to/file.torrent")
        return

    torrent_path = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"TopoTorrent Connection Diagnostics")
    print(f"{'='*60}\n")

    # ─── Step 1: Parse torrent ──────────────────────────────────
    print("[1] Parsing torrent file...")
    try:
        meta = parse_torrent_file(torrent_path)
        print(f"    Name: {meta.name}")
        print(f"    Size: {meta.total_length / (1024**3):.2f} GB")
        print(f"    Pieces: {meta.num_pieces}")
        print(f"    Piece size: {meta.piece_length / 1024:.0f} KB")
        print(f"    Files: {len(meta.files)}")
        print(f"    Trackers: {len(meta.announce_list)}")
    except Exception as e:
        print(f"    FAILED: {e}")
        return

    # ─── Step 2: Verify info_hash ───────────────────────────────
    print(f"\n[2] Verifying info_hash computation...")
    with open(torrent_path, "rb") as f:
        raw_data = f.read()

    # Method A: From raw bytes
    try:
        raw_info = _extract_raw_info_value(raw_data)
        hash_raw = hashlib.sha1(raw_info).digest()
        print(f"    Raw extraction: {hash_raw.hex()}")
    except Exception as e:
        print(f"    Raw extraction FAILED: {e}")
        hash_raw = None

    # Method B: From re-encoded dict
    root = bdecode(raw_data)
    info_dict = root[b"info"]
    re_encoded = bencode(info_dict)
    hash_reencoded = hashlib.sha1(re_encoded).digest()
    print(f"    Re-encoded:     {hash_reencoded.hex()}")

    if hash_raw and hash_raw == hash_reencoded:
        print(f"    ✅ Both methods produce SAME hash")
    elif hash_raw:
        print(f"    ⚠️  MISMATCH! Raw and re-encoded produce DIFFERENT hashes!")
        print(f"    This means re-encoding changes something. Using RAW hash.")
    
    info_hash = hash_raw or hash_reencoded
    print(f"    Using hash: {info_hash.hex()}")

    # ─── Step 3: Get peers from tracker ─────────────────────────
    print(f"\n[3] Contacting trackers...")
    peer_id = generate_peer_id()
    print(f"    Peer ID: {peer_id}")
    
    all_peers = []
    tested_trackers = 0
    for url in meta.announce_list[:5]:  # Test first 5
        tested_trackers += 1
        print(f"\n    [{tested_trackers}] {url[:70]}")
        try:
            peers = tracker_announce(
                url, info_hash, peer_id,
                port=6881,
                left=meta.total_length,
            )
            print(f"        → Got {len(peers)} peers")
            all_peers.extend(peers)
            if peers:
                print(f"        First 3: {peers[:3]}")
        except Exception as e:
            print(f"        → Error: {e}")

    if not all_peers:
        print("\n    ❌ No peers found! Cannot test connections.")
        return

    # Deduplicate
    all_peers = list(set(all_peers))
    print(f"\n    Total unique peers: {len(all_peers)}")

    # ─── Step 4: Test TCP connections ──────────────────────────
    print(f"\n[4] Testing TCP connections to first 20 peers...")
    tcp_success = []
    tcp_fail_reasons = {}

    for ip, port in all_peers[:20]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ip, port))
            tcp_success.append((ip, port))
            sock.close()
        except socket.timeout:
            tcp_fail_reasons["timeout"] = tcp_fail_reasons.get("timeout", 0) + 1
        except ConnectionRefusedError:
            tcp_fail_reasons["refused"] = tcp_fail_reasons.get("refused", 0) + 1
        except OSError as e:
            reason = str(e)[:30]
            tcp_fail_reasons[reason] = tcp_fail_reasons.get(reason, 0) + 1

    print(f"    TCP success: {len(tcp_success)}/{min(20, len(all_peers))}")
    if tcp_fail_reasons:
        print(f"    TCP failures: {tcp_fail_reasons}")

    if not tcp_success:
        print("\n    ❌ No TCP connections succeeded!")
        print("    Possible causes:")
        print("    - Firewall blocking outgoing connections")
        print("    - All peers are offline")
        print("    - ISP blocking BitTorrent traffic")
        # Try with more peers
        print(f"\n    Trying 30 more peers...")
        for ip, port in all_peers[20:50]:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((ip, port))
                tcp_success.append((ip, port))
                sock.close()
                print(f"    ✅ TCP connected to {ip}:{port}")
            except Exception:
                pass
        if not tcp_success:
            print("\n    ❌ Still no connections. Firewall or ISP is likely blocking.")
            return

    # ─── Step 5: Test BitTorrent handshake ─────────────────────
    print(f"\n[5] Testing BitTorrent handshake with {len(tcp_success)} peers...")

    for ip, port in tcp_success[:10]:
        print(f"\n    Peer {ip}:{port}:")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((ip, port))

            # Send handshake
            pstr = b"BitTorrent protocol"
            reserved = bytearray(8)
            reserved[5] = 0x10  # Extension protocol
            
            handshake = bytes([19]) + pstr + bytes(reserved) + info_hash + peer_id
            print(f"        Sending handshake ({len(handshake)} bytes)...")
            print(f"        info_hash in handshake: {info_hash.hex()}")
            sock.sendall(handshake)

            # Receive response
            print(f"        Waiting for response...")
            resp = b""
            while len(resp) < 68:
                chunk = sock.recv(68 - len(resp))
                if not chunk:
                    print(f"        ❌ Connection closed by peer (got {len(resp)} bytes)")
                    break
                resp += chunk

            if len(resp) >= 68:
                pstr_len = resp[0]
                pstr_recv = resp[1:20]
                reserved_recv = resp[20:28]
                remote_hash = resp[28:48]
                remote_peer_id = resp[48:68]

                print(f"        Response pstr_len: {pstr_len}")
                print(f"        Response protocol: {pstr_recv}")
                print(f"        Remote info_hash:  {remote_hash.hex()}")
                print(f"        Remote peer_id:    {remote_peer_id[:8]}")

                if remote_hash == info_hash:
                    print(f"        ✅ HANDSHAKE SUCCESS! Info hash matches!")

                    # Try to receive bitfield/messages
                    try:
                        sock.settimeout(5)
                        msg_len_data = sock.recv(4)
                        if msg_len_data and len(msg_len_data) == 4:
                            msg_len = struct.unpack("!I", msg_len_data)[0]
                            print(f"        First message length: {msg_len}")
                            if msg_len > 0 and msg_len < 1024*1024:
                                msg = sock.recv(min(msg_len, 1024))
                                if msg:
                                    print(f"        First message ID: {msg[0]} ({'BITFIELD' if msg[0]==5 else 'OTHER'})")
                    except socket.timeout:
                        print(f"        (no immediate message after handshake)")

                else:
                    print(f"        ❌ Info hash MISMATCH!")
                    print(f"        Ours:   {info_hash.hex()}")
                    print(f"        Theirs: {remote_hash.hex()}")
            elif len(resp) > 0:
                print(f"        Got partial response: {resp[:20].hex()}...")
            
            sock.close()

        except socket.timeout:
            print(f"        ❌ Timeout during handshake")
        except ConnectionRefusedError:
            print(f"        ❌ Connection refused")
        except Exception as e:
            print(f"        ❌ Error: {e}")

    print(f"\n{'='*60}")
    print("Diagnostics complete!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
