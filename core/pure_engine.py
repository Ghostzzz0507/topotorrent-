"""
Pure-Python BitTorrent engine for TopoTorrent.

Fully functional fallback when libtorrent DLLs are unavailable.
- Bencode encoding/decoding
- .torrent file parsing with raw info_hash extraction
- HTTP + UDP tracker announce (BEP 15)
- TCP listener for INCOMING peer connections
- BitTorrent peer wire protocol (BEP 3)
- Pipelined block downloading with SHA-1 verification
- Immediate piece-to-disk writes for large files
"""

import hashlib
import os
import random
import struct
import socket
import time
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# Bencode
# ═══════════════════════════════════════════════════════════════════════

class BencodeError(Exception):
    pass

def bdecode(data: bytes) -> Any:
    return _BencodeDecoder(data).decode()

def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return f"i{value}e".encode()
    elif isinstance(value, bytes):
        return f"{len(value)}:".encode() + value
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
        return f"{len(encoded)}:".encode() + encoded
    elif isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    elif isinstance(value, dict):
        items = sorted(value.items(),
            key=lambda x: x[0] if isinstance(x[0], bytes) else x[0].encode())
        encoded = b"d"
        for k, v in items:
            encoded += bencode(k) + bencode(v)
        return encoded + b"e"
    else:
        raise BencodeError(f"Cannot encode type: {type(value)}")

class _BencodeDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def decode(self) -> Any:
        if self.pos >= len(self.data):
            raise BencodeError("Unexpected end of data")
        c = chr(self.data[self.pos])
        if c == "i": return self._decode_int()
        elif c == "l": return self._decode_list()
        elif c == "d": return self._decode_dict()
        elif c.isdigit(): return self._decode_string()
        else: raise BencodeError(f"Invalid bencode char: {c}")

    def _decode_int(self) -> int:
        self.pos += 1
        end = self.data.index(b"e", self.pos)
        val = int(self.data[self.pos:end])
        self.pos = end + 1
        return val

    def _decode_string(self) -> bytes:
        colon = self.data.index(b":", self.pos)
        length = int(self.data[self.pos:colon])
        start = colon + 1
        self.pos = start + length
        return self.data[start:self.pos]

    def _decode_list(self) -> list:
        self.pos += 1
        result = []
        while self.data[self.pos:self.pos+1] != b"e":
            result.append(self.decode())
        self.pos += 1
        return result

    def _decode_dict(self) -> dict:
        self.pos += 1
        result = {}
        while self.data[self.pos:self.pos+1] != b"e":
            key = self._decode_string()
            result[key] = self.decode()
        self.pos += 1
        return result


def _extract_raw_info_value(torrent_data: bytes) -> bytes:
    """Extract raw bytes of 'info' value for correct info_hash computation."""
    search = b"4:info"
    idx = torrent_data.find(search)
    if idx == -1:
        raise BencodeError("Cannot find 'info' key")
    value_start = idx + len(search)
    pos = value_start
    depth = 0
    started = False
    while pos < len(torrent_data):
        c = torrent_data[pos:pos+1]
        if c == b"d" or c == b"l":
            depth += 1; pos += 1; started = True
        elif c == b"e":
            depth -= 1; pos += 1
            if started and depth == 0: break
        elif c == b"i":
            pos = torrent_data.index(b"e", pos+1) + 1
        elif c and c[0:1].isdigit():
            colon = torrent_data.index(b":", pos)
            str_len = int(torrent_data[pos:colon])
            pos = colon + 1 + str_len
        else:
            raise BencodeError(f"Unexpected byte at {pos}")
    return torrent_data[value_start:pos]


# ═══════════════════════════════════════════════════════════════════════
# Torrent Metadata
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TorrentMeta:
    info_hash: bytes
    announce: str
    announce_list: List[str] = field(default_factory=list)
    name: str = ""
    total_length: int = 0
    piece_length: int = 0
    pieces_hashes: List[bytes] = field(default_factory=list)
    files: List[Tuple[str, int]] = field(default_factory=list)
    comment: str = ""
    created_by: str = ""

    @property
    def num_pieces(self) -> int:
        return len(self.pieces_hashes)

    def piece_size(self, index: int) -> int:
        if index == self.num_pieces - 1:
            r = self.total_length % self.piece_length
            return r if r > 0 else self.piece_length
        return self.piece_length


def parse_torrent_file(path: str) -> TorrentMeta:
    with open(path, "rb") as f:
        return parse_torrent_bytes(f.read())


def parse_torrent_bytes(data: bytes) -> TorrentMeta:
    root = bdecode(data)
    if not isinstance(root, dict):
        raise BencodeError("Invalid torrent file")
    info = root.get(b"info", {})
    if not info:
        raise BencodeError("Missing info dictionary")

    try:
        info_hash = hashlib.sha1(_extract_raw_info_value(data)).digest()
    except Exception:
        info_hash = hashlib.sha1(bencode(info)).digest()

    announce = root.get(b"announce", b"").decode("utf-8", errors="replace")
    announce_list = []
    if b"announce-list" in root:
        for tier in root[b"announce-list"]:
            for url in tier:
                announce_list.append(url.decode("utf-8", errors="replace"))
    if announce and announce not in announce_list:
        announce_list.insert(0, announce)

    name = info.get(b"name", b"unknown").decode("utf-8", errors="replace")
    piece_length = info.get(b"piece length", 262144)
    pieces_raw = info.get(b"pieces", b"")
    pieces_hashes = [pieces_raw[i:i+20] for i in range(0, len(pieces_raw), 20)]

    files = []
    total_length = 0
    if b"length" in info:
        total_length = info[b"length"]
        files.append((name, total_length))
    elif b"files" in info:
        for finfo in info[b"files"]:
            parts = [p.decode("utf-8", errors="replace") for p in finfo[b"path"]]
            fpath = os.path.join(name, *parts)
            flen = finfo[b"length"]
            files.append((fpath, flen))
            total_length += flen

    print(f"[Parse] {name}: {total_length} bytes, {len(pieces_hashes)} pieces, {len(announce_list)} trackers")
    print(f"[Parse] Info hash: {info_hash.hex()}")

    return TorrentMeta(
        info_hash=info_hash, announce=announce, announce_list=announce_list,
        name=name, total_length=total_length, piece_length=piece_length,
        pieces_hashes=pieces_hashes, files=files,
        comment=root.get(b"comment", b"").decode("utf-8", errors="replace") if b"comment" in root else "",
        created_by=root.get(b"created by", b"").decode("utf-8", errors="replace") if b"created by" in root else "",
    )


def parse_magnet(uri: str) -> Tuple[bytes, str, List[str]]:
    parsed = urllib.parse.urlparse(uri)
    params = urllib.parse.parse_qs(parsed.query)
    xt = params.get("xt", [""])[0]
    if xt.startswith("urn:btih:"):
        h = xt[9:]
        if len(h) == 40:
            info_hash = bytes.fromhex(h)
        elif len(h) == 32:
            import base64
            info_hash = base64.b32decode(h.upper())
        else:
            raise ValueError(f"Bad hash length: {len(h)}")
    else:
        raise ValueError("Missing xt=urn:btih:")
    return info_hash, params.get("dn", ["Unknown"])[0], params.get("tr", [])


# ═══════════════════════════════════════════════════════════════════════
# Trackers (UDP + HTTP)
# ═══════════════════════════════════════════════════════════════════════

CONNECT_MAGIC = 0x41727101980

def udp_tracker_announce(url: str, info_hash: bytes, peer_id: bytes,
                         port: int = 6881, **kw) -> List[Tuple[str, int]]:
    parsed = urllib.parse.urlparse(url)
    host, tp = parsed.hostname, parsed.port or 80
    try:
        ai = socket.getaddrinfo(host, tp, socket.AF_INET, socket.SOCK_DGRAM)
        if not ai: return []
        addr = ai[0][4]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        tid = random.randint(0, 0xFFFFFFFF)
        sock.sendto(struct.pack("!QII", CONNECT_MAGIC, 0, tid), addr)
        conn_id = None
        try:
            data, _ = sock.recvfrom(2048)
            if len(data) >= 16:
                a, rt, conn_id = struct.unpack("!IIQ", data[:16])
                if a != 0 or rt != tid: conn_id = None
        except socket.timeout:
            sock.close(); return []
        if conn_id is None:
            sock.close(); return []
        tid = random.randint(0, 0xFFFFFFFF)
        req = struct.pack("!QII", conn_id, 1, tid)
        req += info_hash + peer_id
        req += struct.pack("!QQQIIIiH", 0, kw.get("left", 0), 0, 2, 0,
                          random.randint(0, 0xFFFFFFFF), -1, port)
        sock.sendto(req, addr)
        peers = []
        try:
            data, _ = sock.recvfrom(65535)
            if len(data) >= 20:
                a, rt = struct.unpack("!II", data[:8])
                if a == 1 and rt == tid:
                    pd = data[20:]
                    for i in range(0, len(pd), 6):
                        if i+6 > len(pd): break
                        ip = ".".join(str(b) for b in pd[i:i+4])
                        p = struct.unpack("!H", pd[i+4:i+6])[0]
                        if p > 0 and ip != "0.0.0.0":
                            peers.append((ip, p))
        except socket.timeout:
            pass
        sock.close()
        return peers
    except Exception:
        return []


def http_tracker_announce(url: str, info_hash: bytes, peer_id: bytes,
                          port: int = 6881, **kw) -> List[Tuple[str, int]]:
    params = {"info_hash": info_hash, "peer_id": peer_id, "port": port,
              "uploaded": 0, "downloaded": 0, "left": kw.get("left", 0),
              "compact": 1, "numwant": 200, "event": "started"}
    try:
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
        req.add_header("User-Agent", "qBittorrent/4.5.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        response = bdecode(data)
        if b"failure reason" in response: return []
        peers = []
        rp = response.get(b"peers", b"")
        if isinstance(rp, bytes):
            for i in range(0, len(rp), 6):
                if i+6 > len(rp): break
                ip = ".".join(str(b) for b in rp[i:i+4])
                p = struct.unpack("!H", rp[i+4:i+6])[0]
                if p > 0: peers.append((ip, p))
        elif isinstance(rp, list):
            for p in rp:
                ip = p.get(b"ip", b"").decode("utf-8")
                pv = p.get(b"port", 0)
                if ip and pv > 0: peers.append((ip, pv))
        return peers
    except Exception:
        return []


def tracker_announce(url, info_hash, peer_id, port=6881, **kw):
    if url.startswith("udp://"):
        return udp_tracker_announce(url, info_hash, peer_id, port, **kw)
    elif url.startswith("http://") or url.startswith("https://"):
        return http_tracker_announce(url, info_hash, peer_id, port, **kw)
    return []


def generate_peer_id() -> bytes:
    return b"-qB4500-" + bytes(random.randint(0, 255) for _ in range(12))


# ═══════════════════════════════════════════════════════════════════════
# Peer Wire Protocol
# ═══════════════════════════════════════════════════════════════════════

MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8
BLOCK_SIZE = 16384  # 16 KB — standard BT spec, best compatibility


class PeerConnection:
    def __init__(self, ip: str, port: int, info_hash: bytes, peer_id: bytes,
                 sock: socket.socket = None):
        self.ip = ip
        self.port = port
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.remote_peer_id = b""
        self.remote_client = ""
        self.peer_choking = True
        self.am_interested = False
        self.bitfield: Optional[bytearray] = None
        self.connected = False
        self._sock = sock  # Can be pre-connected (incoming)
        self.bytes_downloaded = 0
        self.bytes_uploaded = 0
        self.download_speed = 0.0
        self.upload_speed = 0.0
        self._spd_bytes = 0
        self._spd_time = time.time()

    def connect(self, timeout: float = 5) -> bool:
        """Outgoing connection."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._apply_socket_opts(self._sock)
            self._sock.settimeout(timeout)
            self._sock.connect((self.ip, self.port))
            return self._do_handshake_outgoing()
        except Exception:
            self.disconnect()
            return False

    def _apply_socket_opts(self, sock: socket.socket):
        """Apply deep TCP optimizations for maximum throughput."""
        try:
            # Disable Nagle's algorithm for instant BT protocol messages
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Increase OS buffers for high-bandwidth connections (1MB)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        except Exception:
            pass

    def accept_handshake(self, timeout: float = 10) -> bool:
        """Handle incoming connection handshake (we already have the socket)."""
        if not self._sock:
            return False
        try:
            self._apply_socket_opts(self._sock)
            self._sock.settimeout(timeout)
            # Read their handshake first
            resp = self._recv_exact(68)
            if resp is None or resp[0] != 19:
                return False
            remote_hash = resp[28:48]
            if remote_hash != self.info_hash:
                return False
            self.remote_peer_id = resp[48:68]
            self._parse_client_name()

            # Send our handshake back
            pstr = b"BitTorrent protocol"
            reserved = bytearray(8)
            reserved[5] = 0x10
            handshake = bytes([19]) + pstr + bytes(reserved) + self.info_hash + self.peer_id
            self._sock.sendall(handshake)

            self.connected = True
            return True
        except Exception:
            self.disconnect()
            return False

    def _do_handshake_outgoing(self) -> bool:
        """Perform outgoing handshake."""
        try:
            pstr = b"BitTorrent protocol"
            reserved = bytearray(8)
            reserved[5] = 0x10
            handshake = bytes([19]) + pstr + bytes(reserved) + self.info_hash + self.peer_id
            self._sock.sendall(handshake)

            resp = self._recv_exact(68)
            if resp is None or resp[0] != 19:
                self.disconnect()
                return False
            if resp[28:48] != self.info_hash:
                self.disconnect()
                return False
            self.remote_peer_id = resp[48:68]
            self._parse_client_name()
            self.connected = True
            return True
        except Exception:
            self.disconnect()
            return False

    def _parse_client_name(self):
        try:
            pid = self.remote_peer_id
            if pid[0:1] == b"-" and pid[7:8] == b"-":
                self.remote_client = pid[1:7].decode("ascii", errors="replace")
            else:
                self.remote_client = pid[:8].decode("utf-8", errors="replace")
        except Exception:
            self.remote_client = "Unknown"

    def disconnect(self):
        self.connected = False
        if self._sock:
            try: self._sock.close()
            except: pass
            self._sock = None

    def send_interested(self):
        self._send_msg(MSG_INTERESTED)
        self.am_interested = True

    def send_unchoke(self):
        """Tell peer we won't choke them — unlocks tit-for-tat."""
        self._send_msg(MSG_UNCHOKE)

    def send_have(self, piece_index: int):
        """Announce a completed piece to the peer."""
        self._send_msg(MSG_HAVE, struct.pack("!I", piece_index))

    def send_piece(self, index: int, begin: int, data: bytes):
        """Send a PIECE block to the peer (upload)."""
        payload = struct.pack("!II", index, begin) + data
        self._send_msg(MSG_PIECE, payload)
        self.bytes_uploaded += len(data)

    def send_request(self, idx: int, begin: int, length: int):
        self._send_msg(MSG_REQUEST, struct.pack("!III", idx, begin, length))

    def receive_message(self, timeout: float = 10) -> Optional[Tuple[int, bytes]]:
        if not self._sock: return None
        try:
            self._sock.settimeout(timeout)
            ld = self._recv_exact(4)
            if ld is None: return None
            length = struct.unpack("!I", ld)[0]
            if length == 0: return (-1, b"")
            if length > 2*1024*1024: return None
            md = self._recv_exact(length)
            if md is None: return None
            msg_id, payload = md[0], md[1:]
            if msg_id == MSG_PIECE:
                bl = len(payload) - 8
                if bl > 0:
                    self.bytes_downloaded += bl
                    now = time.time()
                    el = now - self._spd_time
                    if el >= 1.0:
                        self.download_speed = (self.bytes_downloaded - self._spd_bytes) / el
                        self._spd_time = now
                        self._spd_bytes = self.bytes_downloaded
            return (msg_id, payload)
        except socket.timeout:
            return None
        except Exception:
            self.connected = False
            return None

    def has_piece(self, index: int) -> bool:
        if self.bitfield is None: return False
        bi = index // 8
        if bi >= len(self.bitfield): return False
        return bool(self.bitfield[bi] & (1 << (7 - index % 8)))

    def handle_bitfield(self, payload: bytes):
        self.bitfield = bytearray(payload)

    def handle_have(self, payload: bytes):
        if len(payload) < 4: return
        idx = struct.unpack("!I", payload[:4])[0]
        if self.bitfield is None:
            self.bitfield = bytearray((idx // 8) + 1)
        bi = idx // 8
        while bi >= len(self.bitfield):
            self.bitfield.append(0)
        self.bitfield[bi] |= (1 << (7 - idx % 8))

    def _send_msg(self, msg_id: int, payload: bytes = b""):
        if not self._sock: return
        try:
            self._sock.sendall(struct.pack("!IB", 1 + len(payload), msg_id) + payload)
        except Exception:
            self.connected = False

    def _recv_exact(self, length: int) -> Optional[bytes]:
        data = bytearray()
        while len(data) < length:
            try:
                # Read up to 256KB at once for massive throughput
                chunk = self._sock.recv(min(length - len(data), 262144))
                if not chunk: return None
                data.extend(chunk)
            except: return None
        return bytes(data)


# ═══════════════════════════════════════════════════════════════════════
# Piece Manager
# ═══════════════════════════════════════════════════════════════════════

class PieceManager:
    def __init__(self, meta: TorrentMeta):
        self.meta = meta
        self.num_pieces = meta.num_pieces
        self.piece_data: Dict[int, bytearray] = {}
        self.completed_pieces: set = set()
        self.in_progress: Dict[int, float] = {}
        self._block_received: Dict[int, set] = {}
        self._lock = threading.Lock()

    @property
    def progress(self) -> float:
        return len(self.completed_pieces) / self.num_pieces if self.num_pieces else 0.0

    @property
    def is_complete(self) -> bool:
        return len(self.completed_pieces) == self.num_pieces

    @property
    def bytes_downloaded(self) -> int:
        return sum(self.meta.piece_size(i) for i in self.completed_pieces)

    def get_needed_piece(self, peer_has=None) -> Optional[int]:
        with self._lock:
            cands = [i for i in range(self.num_pieces)
                     if i not in self.completed_pieces and i not in self.in_progress
                     and (peer_has is None or peer_has(i))]
            if cands:
                # Pick randomly from first 20% of candidates for some variety
                # This prevents all peers from requesting the same piece
                if len(cands) > 10:
                    return random.choice(cands[:max(5, len(cands) // 5)])
                return random.choice(cands)
            now = time.time()
            stale = [i for i, t in self.in_progress.items()
                     if now - t > 30 and i not in self.completed_pieces
                     and (peer_has is None or peer_has(i))]
            if stale: return random.choice(stale)
        return None

    def get_blocks(self, piece_idx: int) -> List[Tuple[int, int]]:
        ps = self.meta.piece_size(piece_idx)
        blocks, off = [], 0
        while off < ps:
            blocks.append((off, min(BLOCK_SIZE, ps - off)))
            off += BLOCK_SIZE
        return blocks

    def mark_in_progress(self, idx: int):
        with self._lock:
            self.in_progress[idx] = time.time()

    def add_block(self, idx: int, begin: int, data: bytes) -> bool:
        with self._lock:
            if idx in self.completed_pieces: return False
            if idx not in self.piece_data:
                self.piece_data[idx] = bytearray(self.meta.piece_size(idx))
                self._block_received[idx] = set()
            end = begin + len(data)
            if end <= len(self.piece_data[idx]):
                self.piece_data[idx][begin:end] = data
                self._block_received[idx].add(begin)
            expected = {b[0] for b in self.get_blocks(idx)}
            if self._block_received.get(idx, set()) >= expected:
                return self._verify(idx)
        return False

    def _verify(self, idx: int) -> bool:
        if idx >= len(self.meta.pieces_hashes): return False
        if hashlib.sha1(bytes(self.piece_data[idx])).digest() == self.meta.pieces_hashes[idx]:
            self.completed_pieces.add(idx)
            self.in_progress.pop(idx, None)
            return True
        self.piece_data.pop(idx, None)
        self._block_received.pop(idx, None)
        self.in_progress.pop(idx, None)
        return False

    def write_piece(self, idx: int, save_path: str, files: List[Tuple[str, int]]):
        if idx not in self.piece_data: return
        piece_bytes = bytes(self.piece_data[idx])
        piece_offset = idx * self.meta.piece_length
        file_offset = 0
        for fpath, fsize in files:
            fend = file_offset + fsize
            if piece_offset + len(piece_bytes) > file_offset and piece_offset < fend:
                sip = max(0, file_offset - piece_offset)
                sif = max(0, piece_offset - file_offset)
                wl = min(len(piece_bytes) - sip, fsize - sif)
                if wl > 0:
                    fp = os.path.join(save_path, fpath)
                    d = os.path.dirname(fp)
                    if d: os.makedirs(d, exist_ok=True)
                    if not os.path.exists(fp):
                        with open(fp, "wb") as f:
                            f.seek(fsize - 1); f.write(b"\x00")
                    with open(fp, "r+b") as f:
                        f.seek(sif); f.write(piece_bytes[sip:sip+wl])
            file_offset = fend
        del self.piece_data[idx]

    def read_piece_from_disk(self, piece_index: int, save_path: str,
                            files: List[Tuple[str, int]]) -> Optional[bytes]:
        """Read a completed piece from disk to serve to peers."""
        if piece_index not in self.completed_pieces:
            return None
        piece_offset = piece_index * self.meta.piece_length
        piece_size = self.meta.piece_size(piece_index)
        result = bytearray(piece_size)
        file_offset = 0
        for fpath, fsize in files:
            fend = file_offset + fsize
            if piece_offset + piece_size > file_offset and piece_offset < fend:
                sip = max(0, file_offset - piece_offset)
                sif = max(0, piece_offset - file_offset)
                rl = min(piece_size - sip, fsize - sif)
                if rl > 0:
                    fp = os.path.join(save_path, fpath)
                    try:
                        with open(fp, "rb") as f:
                            f.seek(sif)
                            result[sip:sip+rl] = f.read(rl)
                    except Exception:
                        return None
            file_offset = fend
        return bytes(result)


# ═══════════════════════════════════════════════════════════════════════
# Peer Worker (with upload support)
# ═══════════════════════════════════════════════════════════════════════

class PeerWorker(threading.Thread):
    PIPELINE = 50  # Extreme pipelining to saturate high-bandwidth connections

    def __init__(self, peer, pm, meta, save_path, completed_pieces_ref=None):
        super().__init__(daemon=True, name=f"PW-{peer.ip}:{peer.port}")
        self.peer = peer
        self.pm = pm
        self.meta = meta
        self.save_path = save_path
        self._running = True
        self._cur_piece = None
        self._pending = []
        self._requested = set()
        self._completed_ref = completed_pieces_ref  # shared set of completed pieces
        self._have_announced = set()  # pieces we've told this peer about

    def stop(self): self._running = False

    def run(self):
        p = self.peer
        try:
            # CRITICAL: Send UNCHOKE immediately — lets peer know we'll upload
            p.send_unchoke()
            # Send INTERESTED — tells peer we want to download
            p.send_interested()

            # Send bitfield of pieces we already have
            self._send_our_bitfield()

            while self._running and p.connected:
                # Announce any new completed pieces
                self._announce_new_pieces()

                msg = p.receive_message(timeout=5)
                if msg is None:
                    # Timeout — re-send requests if needed
                    if not p.peer_choking and self._cur_piece is not None:
                        self._send_reqs()
                    continue

                mid, payload = msg

                if mid == MSG_CHOKE:
                    p.peer_choking = True
                    self._cur_piece = None
                    self._pending = []
                    self._requested = set()

                elif mid == MSG_UNCHOKE:
                    p.peer_choking = False
                    self._pick()

                elif mid == MSG_INTERESTED:
                    # Peer wants to download from us — make sure we're unchoking
                    p.send_unchoke()

                elif mid == MSG_BITFIELD:
                    p.handle_bitfield(payload)
                    if not p.peer_choking:
                        self._pick()

                elif mid == MSG_HAVE:
                    p.handle_have(payload)

                elif mid == MSG_REQUEST:
                    # UPLOAD: Peer is requesting a block from us!
                    if len(payload) >= 12:
                        req_idx = struct.unpack("!I", payload[:4])[0]
                        req_begin = struct.unpack("!I", payload[4:8])[0]
                        req_len = struct.unpack("!I", payload[8:12])[0]
                        self._serve_block(req_idx, req_begin, req_len)

                elif mid == MSG_PIECE and len(payload) >= 8:
                    idx = struct.unpack("!I", payload[:4])[0]
                    begin = struct.unpack("!I", payload[4:8])[0]
                    self._requested.discard(begin)

                    if self.pm.add_block(idx, begin, payload[8:]):
                        # Piece complete and verified!
                        try:
                            self.pm.write_piece(idx, self.save_path, self.meta.files)
                        except: pass

                        self._cur_piece = None
                        self._pending = []
                        self._requested = set()
                        # Get next piece immediately
                        self._pick()
                    else:
                        self._send_reqs()

                elif mid == -1:
                    pass  # Keep-alive

        except Exception:
            pass
        finally:
            p.disconnect()

    def _serve_block(self, piece_idx: int, begin: int, length: int):
        """Read a block from disk and send it to the peer."""
        try:
            piece_data = self.pm.read_piece_from_disk(
                piece_idx, self.save_path, self.meta.files
            )
            if piece_data and begin + length <= len(piece_data):
                block = piece_data[begin:begin + length]
                self.peer.send_piece(piece_idx, begin, block)
        except Exception:
            pass

    def _send_our_bitfield(self):
        """Send our bitfield to the peer so they know what we have."""
        completed = self.pm.completed_pieces
        if not completed:
            return
        num_bytes = (self.pm.num_pieces + 7) // 8
        bf = bytearray(num_bytes)
        for idx in completed:
            bf[idx // 8] |= (1 << (7 - idx % 8))
        self.peer._send_msg(MSG_BITFIELD, bytes(bf))

    def _announce_new_pieces(self):
        """Send HAVE messages for pieces completed since last check."""
        completed = self.pm.completed_pieces
        new = completed - self._have_announced
        for idx in new:
            self.peer.send_have(idx)
            self._have_announced.add(idx)

    def _pick(self):
        if self.peer.peer_choking: return
        idx = self.pm.get_needed_piece(peer_has=self.peer.has_piece)
        if idx is None: return
        self._cur_piece = idx
        self.pm.mark_in_progress(idx)
        recv = self.pm._block_received.get(idx, set())
        self._pending = [(b, l) for b, l in self.pm.get_blocks(idx) if b not in recv]
        self._requested = set()
        self._send_reqs()

    def _send_reqs(self):
        if self._cur_piece is None or self.peer.peer_choking: return
        while len(self._requested) < self.PIPELINE and self._pending:
            b, l = self._pending.pop(0)
            if b not in self._requested:
                self.peer.send_request(self._cur_piece, b, l)
                self._requested.add(b)


# ═══════════════════════════════════════════════════════════════════════
# TCP Listener for INCOMING connections
# ═══════════════════════════════════════════════════════════════════════

class PeerListener:
    """
    Listens on a TCP port for incoming peer connections.
    When we announce to trackers, peers learn our IP:port and connect TO us.
    This is essential when outgoing connections fail (NAT/firewall).
    """

    def __init__(self, port: int, info_hash: bytes, peer_id: bytes,
                 on_new_peer: callable):
        self.port = port
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.on_new_peer = on_new_peer
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.actual_port = port

    def start(self):
        """Start listening. Try several ports if the default is taken."""
        for p in range(self.port, self.port + 20):
            try:
                self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server.settimeout(2)
                self._server.bind(("0.0.0.0", p))
                self._server.listen(50)
                self.actual_port = p
                self._running = True
                self._thread = threading.Thread(target=self._accept_loop,
                                               daemon=True, name=f"Listener-{p}")
                self._thread.start()
                print(f"[Listener] Listening for incoming peers on port {p}")
                return True
            except OSError:
                if self._server:
                    self._server.close()
                continue
        print("[Listener] Could not bind to any port!")
        return False

    def stop(self):
        self._running = False
        if self._server:
            try: self._server.close()
            except: pass

    def _accept_loop(self):
        while self._running:
            try:
                client_sock, addr = self._server.accept()
                ip, port = addr
                print(f"[Listener] Incoming connection from {ip}:{port}")

                # Handle handshake in a separate thread
                threading.Thread(
                    target=self._handle_incoming,
                    args=(client_sock, ip, port),
                    daemon=True
                ).start()

            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    continue
                break

    def _handle_incoming(self, sock: socket.socket, ip: str, port: int):
        """Handle an incoming peer connection."""
        peer = PeerConnection(ip, port, self.info_hash, self.peer_id, sock=sock)
        if peer.accept_handshake(timeout=10):
            print(f"[Listener] Handshake OK from {ip}:{port} ({peer.remote_client})")
            self.on_new_peer(peer)
        else:
            peer.disconnect()


# ═══════════════════════════════════════════════════════════════════════
# Pure Python Torrent Handle
# ═══════════════════════════════════════════════════════════════════════

class PurePythonTorrentHandle:
    def __init__(self, meta: TorrentMeta, save_path: str, peer_id: bytes):
        self.meta = meta
        self.save_path = save_path
        self.peer_id = peer_id
        self.added_time = time.time()
        self.topology_avg_score = 0.0

        self.piece_manager = PieceManager(meta)
        self.peer_workers: List[PeerWorker] = []
        self.all_peers: List[PeerConnection] = []
        self.discovered_peers: List[Tuple[str, int]] = []
        self.tracker_status = "Not contacted"
        self.tracker_message = ""

        self._paused = False
        self._state = "Queued"
        self._download_speed = 0.0
        self._upload_speed = 0.0

        self._manager_thread: Optional[threading.Thread] = None
        self._listener: Optional[PeerListener] = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def is_valid(self): return True

    def start(self):
        if self._running: return
        self._running = True
        self._paused = False
        self._state = "Starting"

        # Start listener for incoming connections
        self._listener = PeerListener(
            6881, self.meta.info_hash, self.peer_id,
            on_new_peer=self._on_incoming_peer
        )
        self._listener.start()

        self._manager_thread = threading.Thread(
            target=self._manager_loop, daemon=True,
            name=f"Mgr-{self.meta.name[:20]}"
        )
        self._manager_thread.start()

    def pause(self):
        self._paused = True
        self._state = "Paused"

    def resume(self):
        self._paused = False
        if not self._running: self.start()
        elif self.piece_manager.is_complete: self._state = "Seeding"
        else: self._state = "Downloading"

    def stop(self):
        self._running = False
        if self._listener: self._listener.stop()
        for w in self.peer_workers: w.stop()
        for p in self.all_peers: p.disconnect()

    def force_reannounce(self):
        threading.Thread(target=self._do_announces, daemon=True).start()

    def _on_incoming_peer(self, peer: PeerConnection):
        """Called when a new peer connects to us."""
        with self._lock:
            self.all_peers.append(peer)

        worker = PeerWorker(peer, self.piece_manager, self.meta, self.save_path)
        worker.start()

        with self._lock:
            self.peer_workers.append(worker)

        print(f"[Incoming] Started worker for {peer.ip}:{peer.port}")

    def get_status(self) -> Dict[str, Any]:
        progress = self.piece_manager.progress
        if self.piece_manager.is_complete and not self._paused:
            self._state = "Seeding"
        self._download_speed = sum(p.download_speed for p in self.all_peers if p.connected)
        eta = -1
        remaining = self.meta.total_length * (1 - progress)
        if self._download_speed > 0: eta = int(remaining / self._download_speed)
        connected = [p for p in self.all_peers if p.connected]
        num_seeds = len([p for p in connected if p.bitfield and
                        all(p.has_piece(i) for i in range(min(self.meta.num_pieces, 8)))])
        return {
            "name": self.meta.name,
            "total_size": self.meta.total_length,
            "progress": progress,
            "state": self._state,
            "state_idx": 3 if "Download" in self._state else (5 if "Seed" in self._state else 0),
            "download_speed": self._download_speed,
            "upload_speed": self._upload_speed,
            "eta": eta,
            "num_seeds": num_seeds,
            "num_peers": len(connected),
            "num_complete": num_seeds,
            "num_incomplete": max(0, len(connected) - num_seeds),
            "total_downloaded": self.piece_manager.bytes_downloaded,
            "total_uploaded": 0,
            "ratio": 0.0,
            "save_path": self.save_path,
            "info_hash": self.meta.info_hash.hex(),
            "added_time": self.added_time,
            "is_paused": self._paused,
            "is_seeding": self.piece_manager.is_complete,
            "topology_score": self.topology_avg_score,
        }

    def get_peers(self) -> list:
        from core.torrent_handle import TorrentPeerInfo
        return [TorrentPeerInfo(
            ip=p.ip, port=p.port, client=p.remote_client,
            flags="C" if p.peer_choking else "",
            progress=0.0,
            download_speed=p.download_speed, upload_speed=p.upload_speed,
            total_downloaded=p.bytes_downloaded, total_uploaded=p.bytes_uploaded,
        ) for p in self.all_peers]

    def get_files(self) -> list:
        from core.torrent_handle import TorrentFileInfo
        return [TorrentFileInfo(
            index=i, path=path, size=size,
            progress=self.piece_manager.progress, priority=4,
        ) for i, (path, size) in enumerate(self.meta.files)]

    def get_trackers(self) -> list:
        from core.torrent_handle import TorrentTrackerInfo
        urls = self.meta.announce_list or ([self.meta.announce] if self.meta.announce else [])
        return [TorrentTrackerInfo(
            url=url, status=self.tracker_status,
            peers=len(self.discovered_peers),
            message=self.tracker_message, tier=0,
        ) for url in urls]

    def set_file_priorities(self, priorities): pass

    # ─── manager loop ─────────────────────────────────────────────────

    def _manager_loop(self):
        import queue
        MAX_PEERS = 500
        last_announce = 0
        tried_peers = set()
        
        # Connection worker pool
        peer_queue = queue.Queue()
        
        def connect_worker():
            while self._running:
                try:
                    ip, port = peer_queue.get(timeout=1)
                    if len([p for p in self.all_peers if p.connected]) >= MAX_PEERS:
                        peer_queue.task_done()
                        continue
                    
                    peer = PeerConnection(ip, port, self.meta.info_hash, self.peer_id)
                    if peer.connect(timeout=3):
                        with self._lock:
                            self.all_peers.append(peer)
                        worker = PeerWorker(peer, self.piece_manager, self.meta, self.save_path)
                        worker.start()
                        with self._lock:
                            self.peer_workers.append(worker)
                        print(f"[Outgoing] {ip}:{port} ({peer.remote_client})")
                    else:
                        peer.disconnect()
                    peer_queue.task_done()
                except queue.Empty:
                    pass
                except Exception:
                    pass

        # Start 30 connect workers (acts as a pool to prevent thread explosions)
        for _ in range(30):
            threading.Thread(target=connect_worker, daemon=True).start()

        self._state = "Contacting Trackers"
        threading.Thread(target=self._do_announces, daemon=True).start()

        while self._running and not self.piece_manager.is_complete:
            if self._paused:
                time.sleep(1); continue

            now = time.time()

            if now - last_announce > 60:
                last_announce = now
                threading.Thread(target=self._do_announces, daemon=True).start()

            self.all_peers = [p for p in self.all_peers if p.connected]
            self.peer_workers = [w for w in self.peer_workers if w.is_alive()]
            connected = len(self.all_peers)

            # --- Pseudo-PEX (Subnet discovery) ---
            # For every connected peer, there might be other peers nearby (e.g. ISPs)
            # Scan nearby IP blocks on common BitTorrent ports to find hidden peers
            if connected > 0 and connected < MAX_PEERS and peer_queue.qsize() < 100:
                for p in self.all_peers:
                    if not p.connected: continue
                    try:
                        # Extract subnet A.B.C.*
                        octets = p.ip.split('.')
                        if len(octets) == 4:
                            base_ip = f"{octets[0]}.{octets[1]}.{octets[2]}."
                            # Add a few nearby IPs to the queue to probe
                            for offset in [-1, 1, -2, 2]:
                                last = int(octets[3]) + offset
                                if 1 <= last <= 254:
                                    sc_ip = f"{base_ip}{last}"
                                    if (sc_ip, p.port) not in tried_peers:
                                        tried_peers.add((sc_ip, p.port))
                                        peer_queue.put((sc_ip, p.port))
                                        
                                    # Also try common BT ports for this specific peer IP
                                    for sport in [6881, 1337, 6882]:
                                        if (p.ip, sport) not in tried_peers:
                                            tried_peers.add((p.ip, sport))
                                            peer_queue.put((p.ip, sport))
                    except: pass

            # Feed the connect queue with tracker peers
            if connected < MAX_PEERS and self.discovered_peers:
                existing = {(p.ip, p.port) for p in self.all_peers}
                new = [a for a in self.discovered_peers 
                       if a not in existing and a not in tried_peers]
                
                # Only feed if queue is getting low
                if peer_queue.qsize() < 50 and new:
                    for ip, port in new[:100]:
                        tried_peers.add((ip, port))
                        peer_queue.put((ip, port))

            if connected > 0:
                self._state = f"Downloading ({connected} peers)"
            elif self.discovered_peers:
                self._state = f"Connecting (Queue: {peer_queue.qsize()})"
            else:
                self._state = "Finding Peers"

            untried = len([a for a in self.discovered_peers if a not in tried_peers])
            if untried == 0 and connected < 20 and peer_queue.empty():
                tried_peers.clear()

            time.sleep(1)

        if self.piece_manager.is_complete:
            self._state = "Seeding"

    def _do_announces(self):
        from core.trackers import get_all_trackers
        own_urls = self.meta.announce_list or ([self.meta.announce] if self.meta.announce else [])
        urls = get_all_trackers(own_urls)  # Merge with 100+ public trackers
        self.tracker_status = "Announcing..."
        listen_port = self._listener.actual_port if self._listener else 6881

        # Announce ALL trackers in parallel (batches of 10)
        for i in range(0, len(urls), 10):
            batch = urls[i:i+10]
            results = []
            threads = []
            def _ann(url):
                try:
                    peers = tracker_announce(url, self.meta.info_hash, self.peer_id,
                                           port=listen_port,
                                           left=max(0, self.meta.total_length - self.piece_manager.bytes_downloaded))
                    if peers: results.append((url, peers))
                except: pass
            for url in batch:
                if not url: continue
                t = threading.Thread(target=_ann, args=(url,), daemon=True)
                threads.append(t); t.start()
            for t in threads:
                t.join(timeout=8)

            existing = {(p.ip, p.port) for p in self.all_peers}
            existing.update(set(self.discovered_peers))
            for url, peers in results:
                new = [p for p in peers if p not in existing]
                self.discovered_peers.extend(new)
                existing.update(new)
                if new:
                    print(f"[Tracker] {url[:60]}... → {len(peers)} peers ({len(new)} new)")

        if self.discovered_peers:
            self.tracker_status = "Working"
            self.tracker_message = f"{len(self.discovered_peers)} peers discovered"
        else:
            self.tracker_status = "No peers"

    def _try_connect(self, ip: str, port: int):
        peer = PeerConnection(ip, port, self.meta.info_hash, self.peer_id)
        if peer.connect(timeout=3):  # Fast 3s timeout
            with self._lock:
                self.all_peers.append(peer)
            worker = PeerWorker(peer, self.piece_manager, self.meta, self.save_path)
            worker.start()
            with self._lock:
                self.peer_workers.append(worker)
            print(f"[Outgoing] Connected to {ip}:{port} ({peer.remote_client})")
        else:
            peer.disconnect()

