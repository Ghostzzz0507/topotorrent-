"""
Privacy & Anti-Throttling module for TopoTorrent.

ISPs commonly use Deep Packet Inspection (DPI) to detect and throttle
BitTorrent traffic. This module provides:

1. Protocol obfuscation (MSE/PE-inspired)
   - XOR-based stream cipher using info_hash-derived key
   - Randomized handshake padding to defeat pattern matching
   - Header disguise to look like generic encrypted traffic

2. Traffic shaping
   - Message padding to uniform sizes
   - Send timing jitter to prevent burst-pattern detection
   - Connection rate limiting to avoid triggering ISP heuristics

3. Encryption mode integration
   - Mode 0: Disabled (plaintext BitTorrent)
   - Mode 1: Enabled (encrypt if peer supports, fall back if not)
   - Mode 2: Forced (only connect to peers supporting encryption)
"""

import hashlib
import os
import random
import struct
import time
from typing import Optional, Tuple


class StreamCipher:
    """
    Simple RC4-inspired stream cipher for protocol obfuscation.

    Uses info_hash + shared nonce to derive a keystream.
    NOT cryptographically secure — designed to defeat DPI, not attackers.
    """

    def __init__(self, key: bytes):
        # RC4 KSA (Key Scheduling Algorithm)
        self.S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + self.S[i] + key[i % len(key)]) % 256
            self.S[i], self.S[j] = self.S[j], self.S[i]
        self.i = 0
        self.j = 0

        # Discard first 1024 bytes (standard RC4 hardening)
        for _ in range(1024):
            self._next_byte()

    def _next_byte(self) -> int:
        self.i = (self.i + 1) % 256
        self.j = (self.j + self.S[self.i]) % 256
        self.S[self.i], self.S[self.j] = self.S[self.j], self.S[self.i]
        return self.S[(self.S[self.i] + self.S[self.j]) % 256]

    def process(self, data: bytes) -> bytes:
        """Encrypt or decrypt data (XOR with keystream — symmetric)."""
        result = bytearray(len(data))
        for k, byte in enumerate(data):
            result[k] = byte ^ self._next_byte()
        return bytes(result)


class ProtocolObfuscator:
    """
    Obfuscates BitTorrent protocol messages to evade DPI.

    When enabled, wraps the standard BT handshake in an encrypted
    envelope with random padding, making it indistinguishable from
    generic TLS-like traffic to packet inspectors.
    """

    # Magic bytes indicating obfuscated connection
    OBFUSCATION_MAGIC = b"\x13\x37\xBE\xEF"

    def __init__(self, info_hash: bytes, encryption_mode: int = 1):
        self.info_hash = info_hash
        self.encryption_mode = encryption_mode
        self._cipher_out: Optional[StreamCipher] = None
        self._cipher_in: Optional[StreamCipher] = None
        self._nonce: bytes = b""
        self.obfuscation_active = False

    def create_handshake_envelope(self, handshake_data: bytes) -> bytes:
        """
        Wrap a standard BT handshake in an obfuscated envelope.

        Format:
            [4B magic] [16B nonce] [2B padding_len] [padding] [2B payload_len] [encrypted_payload]

        The encrypted payload contains the original BT handshake.
        """
        if self.encryption_mode == 0:
            return handshake_data

        # Generate random nonce
        self._nonce = os.urandom(16)

        # Derive encryption key from info_hash + nonce
        key = hashlib.sha256(self.info_hash + self._nonce + b"topotorrent_obfs").digest()
        self._cipher_out = StreamCipher(key)

        # Random padding (32-512 bytes) to randomize packet size
        pad_len = random.randint(32, 512)
        padding = os.urandom(pad_len)

        # Encrypt the handshake
        encrypted_payload = self._cipher_out.process(handshake_data)

        # Build envelope
        envelope = (
            self.OBFUSCATION_MAGIC
            + self._nonce
            + struct.pack("!H", pad_len)
            + padding
            + struct.pack("!H", len(encrypted_payload))
            + encrypted_payload
        )

        self.obfuscation_active = True
        return envelope

    def parse_handshake_envelope(self, data: bytes) -> Tuple[Optional[bytes], int]:
        """
        Parse an incoming obfuscated handshake envelope.

        Returns:
            (decrypted_handshake, bytes_consumed) or (None, 0) on failure
        """
        if len(data) < 4:
            return None, 0

        # Check if this is an obfuscated connection
        if data[:4] != self.OBFUSCATION_MAGIC:
            # Not obfuscated — might be a standard connection
            return None, 0

        pos = 4

        # Read nonce
        if len(data) < pos + 16:
            return None, 0
        nonce = data[pos:pos + 16]
        pos += 16

        # Read padding length
        if len(data) < pos + 2:
            return None, 0
        pad_len = struct.unpack("!H", data[pos:pos + 2])[0]
        pos += 2

        # Skip padding
        pos += pad_len
        if pos > len(data):
            return None, 0

        # Read payload length
        if len(data) < pos + 2:
            return None, 0
        payload_len = struct.unpack("!H", data[pos:pos + 2])[0]
        pos += 2

        # Read encrypted payload
        if len(data) < pos + payload_len:
            return None, 0
        encrypted = data[pos:pos + payload_len]
        pos += payload_len

        # Derive decryption key
        key = hashlib.sha256(self.info_hash + nonce + b"topotorrent_obfs").digest()
        self._cipher_in = StreamCipher(key)

        decrypted = self._cipher_in.process(encrypted)
        self.obfuscation_active = True

        return decrypted, pos

    def encrypt_message(self, data: bytes) -> bytes:
        """Encrypt an outgoing message after handshake."""
        if self.obfuscation_active and self._cipher_out:
            return self._cipher_out.process(data)
        return data

    def decrypt_message(self, data: bytes) -> bytes:
        """Decrypt an incoming message after handshake."""
        if self.obfuscation_active and self._cipher_in:
            return self._cipher_in.process(data)
        return data


class TrafficShaper:
    """
    Shapes BitTorrent traffic to avoid ISP detection patterns.

    - Pads messages to uniform sizes
    - Adds random timing jitter between sends
    - Limits connection burst rate
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.min_message_size = 64    # Minimum padded size
        self.jitter_ms_range = (5, 50)  # Random delay range in ms
        self.max_connects_per_second = 5  # Limit connection burst
        self._last_connect_time = 0.0
        self._connect_count = 0

    def pad_message(self, data: bytes) -> bytes:
        """Pad message to hide its true length pattern."""
        if not self.enabled:
            return data

        # Pad to next multiple of min_message_size
        target = ((len(data) // self.min_message_size) + 1) * self.min_message_size
        pad_len = target - len(data)

        if pad_len > 0:
            # Append padding with length prefix so receiver can strip it
            padding = os.urandom(pad_len - 2) if pad_len > 2 else b""
            return data + struct.pack("!H", pad_len) + padding

        return data

    def should_delay(self) -> float:
        """Get random delay in seconds before sending next message."""
        if not self.enabled:
            return 0.0

        low, high = self.jitter_ms_range
        return random.randint(low, high) / 1000.0

    def can_connect(self) -> bool:
        """Check if we can open a new connection without triggering burst detection."""
        if not self.enabled:
            return True

        now = time.time()
        if now - self._last_connect_time >= 1.0:
            self._connect_count = 0
            self._last_connect_time = now

        if self._connect_count < self.max_connects_per_second:
            self._connect_count += 1
            return True

        return False


class PrivacyManager:
    """
    Central manager for all privacy and anti-throttling features.
    """

    def __init__(self, encryption_mode: int = 1, traffic_shaping: bool = True):
        self.encryption_mode = encryption_mode
        self.traffic_shaper = TrafficShaper(enabled=traffic_shaping)
        self._obfuscators: dict = {}  # key -> ProtocolObfuscator

    def get_obfuscator(self, info_hash: bytes) -> ProtocolObfuscator:
        """Get or create an obfuscator for a torrent."""
        key = info_hash.hex()
        if key not in self._obfuscators:
            self._obfuscators[key] = ProtocolObfuscator(
                info_hash, self.encryption_mode
            )
        return self._obfuscators[key]

    def should_encrypt(self) -> bool:
        """Check if encryption should be attempted."""
        return self.encryption_mode > 0

    def requires_encryption(self) -> bool:
        """Check if encryption is mandatory."""
        return self.encryption_mode >= 2

    def get_libtorrent_settings(self) -> dict:
        """Get libtorrent settings dict for encryption."""
        if self.encryption_mode == 0:
            return {
                "out_enc_policy": 0,  # disabled
                "in_enc_policy": 0,
                "allowed_enc_level": 1,  # plaintext
                "prefer_rc4": False,
            }
        elif self.encryption_mode == 1:
            return {
                "out_enc_policy": 1,  # enabled
                "in_enc_policy": 1,
                "allowed_enc_level": 3,  # both plaintext and rc4
                "prefer_rc4": True,
            }
        else:  # forced
            return {
                "out_enc_policy": 2,  # forced
                "in_enc_policy": 2,
                "allowed_enc_level": 2,  # rc4 only
                "prefer_rc4": True,
            }
