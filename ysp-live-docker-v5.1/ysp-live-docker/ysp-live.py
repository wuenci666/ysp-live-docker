#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ysptp CCTV live m3u8 proxy — Python port.

Faithful single-file Python port of ``main-2.rs`` (the ysptp-project Rust
source, v0.1.0).  Standard library only: no third-party packages needed.

The program impersonates the 央视频 TV app, resolves live stream URLs for
a small set of CCTV channels (cctv5/cctv5p/cctv164k/cctv4k/cctv8k) and
serves them as local ``.m3u8`` playlists plus a ``/proxy.ts`` segment proxy.

Crypto note: AES-256-GCM and RSA-OAEP-SHA256 are implemented in pure
Python below (``_aes256_*`` / ``aes_gcm_*`` / ``rsa_oaep_sha256_encrypt``)
and were cross-validated against the ``cryptography`` package and NIST
vectors before release.  Behavior (key normalization, nonce layout,
base64 alphabets, 190-byte RSA chunking) mirrors the ``aes-gcm`` 0.10 and
``rsa`` 0.9 crates used by the Rust build.

JSON note: serde_json without ``preserve_order`` serializes maps with
keys sorted; every ``json.dumps`` in this file therefore uses
``sort_keys=True`` with compact separators to stay byte-compatible.

Usage:
    python3 ysptp.py [--host 0.0.0.0] [--port 18766] [options]
    python3 ysptp.py --self-test     # run the ported unit tests
"""

from __future__ import annotations

import argparse
import base64
import binascii
import collections
import contextlib
import dataclasses
import gzip
import hashlib
import hmac as _hmac
import http.client
import http.cookiejar
import json
import math
import os
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Constants (mirrors main-2.rs)
# ---------------------------------------------------------------------------

AK = "9f5c54c4ed0e50109b800f7e28fec205"
RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkKeLy4ywWLSnBkwRyqYgF3HMIj05V5uuh5HjyEsZOWnu1NHu3jPQv3sr32wwQNYv5qa"
    "psNXmNgLUDHtgHZxqPQAYXltjSRc0qhcD286t62wOIHId8zXS3s1Jy4rgU4qjQWzI9rp/1sE0pMsmwTaJa4zuJ5iz8VwF8Av5oJ1k+HxY+/HLnj"
    "NlW1hmWLpuDYmkZYuAoTHa1VGeHQh9FEKI8ZcL3GTQphShUoC+Kg3P1hGUVTtCYapmzPS5lkAdwebuzwvTCfGiTErYZCnPBUSeV7BVlgjtLYIi2"
    "9KvF0a8FHsJMfe/UdHcyW/RihsIYOtDQcRRpFGXyPXbVrzFJse24QIDAQAB"
)

CLOUD_GET_URL = "https://ytpcloudws.cctv.cn/cloudps/wssapi/device/v2/get"
CLOUD_REGISTER_URL = "https://ytpcloudws.cctv.cn/cloudps/wssapi/device/v2/register"
APP_START_URL = "https://ytpaddr.cctv.cn/gsnw/api/app/start/v1/01"
DRM_CONFIG_URL = "https://ytpaddr.cctv.cn/gsnw/drm/config/obtain/v1"
VERSION_CONFIG_URL = "https://ytpaddr.cctv.cn/gsnw/version/config/obtain/v1"
DICTIONARY_URL = "https://ytpaddr.cctv.cn/gsnw/player/dictionary/obtain/v1"
INDEX_URL = "https://ytpaddr.cctv.cn/gsnw/api/index/v1/01"
REPORT_SINGLE_URL = "https://ytpdata.cctv.cn/das/app/data/message/single"
COLLECT_REPORT_URL = "https://collect.cctv.cn/cctvmobileinf/rest/cctv/receive/new/app"
LIVE_V1_01_URL = "https://ytpaddr.cctv.cn/gsnw/api/live/v1/01"
LIVE_V1_02_URL = "https://ytpaddr.cctv.cn/gsnw/api/live/v1/02"
VDN_GETSTREAM_URL = "https://ytpvdn.cctv.cn/cctvmobileinf/rest/cctv/videoliveUrl/getstream"

DEFAULT_LIVE_USER_ID = "BAEBFF2B-C516-4F34-ABC0-A824A6461CBD"
DEFAULT_DEVICE_NAME = "央视频电视投屏助手"
VDN_APP_NAME = "央视频电视投屏助手"
REPORT_APP_KEY = "1178c84d-4818-44ff-b415-02106e87e144"
COLLECT_SDK_VERSION = "1.0.0"
DEFAULT_PAGE_NAME = "com.cctv.tv.mvp.ui.activity.MainActivity"
DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.8"
SWITCH_TOKEN = "GPT_858"
AD_REDIRECT_URL = ""
DEFAULT_REDIRECT_URL = "https://t.me/GPT_858"
RESULT_OK = 0
RESULT_NEEDS_REGISTER = 601
RESULT_GET_MISSING_OR_INVALID = 2
RESULT_REGISTERED_ELSEWHERE = 694
RESULT_REGISTER_RETRY_LATER = 695
# ---------------------------------------------------------------------------
# Pure-stdlib crypto: AES-256 (FIPS-197), GCM (SP 800-38D), RSA-OAEP-SHA256
# (RFC 8017) and a minimal DER parser for the bundled SPKI public key.
# Mirrors the aes-gcm 0.10 / rsa 0.9 (Oaep<Sha256>) / sha2 / base64 0.22
# crates. Cross-validated against the `cryptography` package.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------- AES-256

_SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = tuple(_INV_SBOX)

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x11B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _aes256_key_schedule(key: bytes):
    """Return 15 round keys (each 16 bytes) for a 32-byte key."""
    assert len(key) == 32
    w = [int.from_bytes(key[i:i + 4], "big") for i in range(0, 32, 4)]
    for i in range(8, 60):
        temp = w[i - 1]
        if i % 8 == 0:
            temp = (_SBOX[(temp >> 16) & 0xFF] << 24) | (_SBOX[(temp >> 8) & 0xFF] << 16) | \
                   (_SBOX[temp & 0xFF] << 8) | _SBOX[(temp >> 24) & 0xFF]
            temp ^= _RCON[i // 8 - 1] << 24
        elif i % 8 == 4:
            temp = (_SBOX[(temp >> 24) & 0xFF] << 24) | (_SBOX[(temp >> 16) & 0xFF] << 16) | \
                   (_SBOX[(temp >> 8) & 0xFF] << 8) | _SBOX[temp & 0xFF]
        w.append(w[i - 8] ^ temp)
    return [b"".join(word.to_bytes(4, "big") for word in w[i:i + 4]) for i in range(0, 60, 4)]


def _add_round_key(state, rk: bytes):
    for i in range(16):
        state[i] ^= rk[i]


def _sub_bytes(state):
    for i in range(16):
        state[i] = _SBOX[state[i]]


def _inv_sub_bytes(state):
    for i in range(16):
        state[i] = _INV_SBOX[state[i]]


def _shift_rows(s):
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]


def _inv_shift_rows(s):
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]


def _mix_columns(s):
    # Standard column-major layout: column c = bytes s[4c .. 4c+3].
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        s[4 * c] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
        s[4 * c + 1] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
        s[4 * c + 2] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
        s[4 * c + 3] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)


def _mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _inv_mix_columns(s):
    # Standard column-major layout: column c = bytes s[4c .. 4c+3].
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        s[4 * c] = _mul(a0, 0x0E) ^ _mul(a1, 0x0B) ^ _mul(a2, 0x0D) ^ _mul(a3, 0x09)
        s[4 * c + 1] = _mul(a0, 0x09) ^ _mul(a1, 0x0E) ^ _mul(a2, 0x0B) ^ _mul(a3, 0x0D)
        s[4 * c + 2] = _mul(a0, 0x0D) ^ _mul(a1, 0x09) ^ _mul(a2, 0x0E) ^ _mul(a3, 0x0B)
        s[4 * c + 3] = _mul(a0, 0x0B) ^ _mul(a1, 0x0D) ^ _mul(a2, 0x09) ^ _mul(a3, 0x0E)


def aes256_encrypt_block(key: bytes, block: bytes) -> bytes:
    rk = _aes256_key_schedule(key)
    s = bytearray(block)
    _add_round_key(s, rk[0])
    for rnd in range(1, 14):
        _sub_bytes(s)
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, rk[rnd])
    _sub_bytes(s)
    _shift_rows(s)
    _add_round_key(s, rk[14])
    return bytes(s)


def aes256_decrypt_block(key: bytes, block: bytes) -> bytes:
    rk = _aes256_key_schedule(key)
    s = bytearray(block)
    _add_round_key(s, rk[14])
    for rnd in range(13, 0, -1):
        _inv_shift_rows(s)
        _inv_sub_bytes(s)
        _add_round_key(s, rk[rnd])
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    _inv_sub_bytes(s)
    _add_round_key(s, rk[0])
    return bytes(s)


# ------------------------------------------------------------- GCM mode


def _gf_mult(x: int, y: int) -> int:
    r = 0xE1000000000000000000000000000000
    z = 0
    v = y
    for _ in range(128):
        if x & 0x80000000000000000000000000000000:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ r
        else:
            v >>= 1
        x = (x << 1) & 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
    return z


def _ghash(h: int, aad: bytes, ct: bytes) -> int:
    x = 0

    def _blocks(data: bytes):
        for i in range(0, len(data), 16):
            blk = data[i:i + 16]
            if len(blk) < 16:
                blk = blk + b"\x00" * (16 - len(blk))
            yield int.from_bytes(blk, "big")

    for b in _blocks(aad):
        x = _gf_mult(x ^ b, h)
    for b in _blocks(ct):
        x = _gf_mult(x ^ b, h)
    lens = ((len(aad) * 8) << 64) | (len(ct) * 8)
    x = _gf_mult(x ^ lens, h)
    return x


def _inc32(block: bytes) -> bytes:
    ctr = int.from_bytes(block[12:], "big")
    return block[:12] + ((ctr + 1) & 0xFFFFFFFF).to_bytes(4, "big")


def _gctr(key: bytes, icb: bytes, data: bytes) -> bytes:
    """GCTR_K(ICB, X): first block uses E(K, ICB) directly (SP 800-38D)."""
    out = bytearray()
    cb = icb
    for i in range(0, len(data), 16):
        ks = aes256_encrypt_block(key, cb)
        chunk = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, ks))
        cb = _inc32(cb)
    return bytes(out)


def aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Return ciphertext || 16-byte tag (matches aes-gcm crate output layout)."""
    assert len(key) == 32 and len(nonce) == 12
    h = int.from_bytes(aes256_encrypt_block(key, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ct = _gctr(key, _inc32(j0), plaintext)
    s = _ghash(h, aad, ct).to_bytes(16, "big")
    # T = MSB_128(GCTR_K(J0, S)): the tag block uses E(K, J0) directly.
    tag = bytes(a ^ b for a, b in zip(aes256_encrypt_block(key, j0), s))
    return ct + tag


def aes_gcm_decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
    if len(ct_and_tag) < 16:
        raise ValueError("AES-GCM payload too short")
    ct, tag = ct_and_tag[:-16], ct_and_tag[-16:]
    h = int.from_bytes(aes256_encrypt_block(key, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    s = _ghash(h, aad, ct).to_bytes(16, "big")
    expect = bytes(a ^ b for a, b in zip(aes256_encrypt_block(key, j0), s))
    if not _hmac.compare_digest(tag, expect):
        raise ValueError("AES-GCM decrypt failed")
    return _gctr(key, _inc32(j0), ct)


# ------------------------------------------------- RSA-OAEP-SHA256


def _mgf1_sha256(seed: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _oaep_encode_sha256(message: bytes, k: int, seed: bytes) -> bytes:
    hlen = 32
    if len(message) > k - 2 * hlen - 2:
        raise ValueError("OAEP message too long")
    lhash = hashlib.sha256(b"").digest()
    ps = b"\x00" * (k - len(message) - 2 * hlen - 2)
    db = lhash + ps + b"\x01" + message
    db_mask = _mgf1_sha256(seed, k - hlen - 1)
    masked_db = bytes(a ^ b for a, b in zip(db, db_mask))
    seed_mask = _mgf1_sha256(masked_db, hlen)
    masked_seed = bytes(a ^ b for a, b in zip(seed, seed_mask))
    return b"\x00" + masked_seed + masked_db


def _der_read_tlv(der: bytes, pos: int):
    assert der[pos] == 0x30, "expected SEQUENCE"
    pos += 1
    ln, pos = _der_read_len(der, pos)
    end = pos + ln
    items = []
    while pos < end:
        tag = der[pos]
        pos += 1
        ln2, pos = _der_read_len(der, pos)
        items.append((tag, der[pos:pos + ln2]))
        pos += ln2
    return items


def _der_read_len(der: bytes, pos: int):
    first = der[pos]
    pos += 1
    if first & 0x80 == 0:
        return first, pos
    n = first & 0x7F
    return int.from_bytes(der[pos:pos + n], "big"), pos + n


def _der_read_int(raw: bytes) -> int:
    return int.from_bytes(raw, "big")


def parse_spki_rsa_pubkey(der: bytes):
    """Parse DER SubjectPublicKeyInfo -> (n, e)."""
    outer = _der_read_tlv(der, 0)
    bitstring = outer[1][1]
    assert bitstring[0] == 0x00
    inner = _der_read_tlv(bitstring[1:], 0)
    n = _der_read_int(inner[0][1])
    e = _der_read_int(inner[1][1])
    return n, e


def rsa_oaep_sha256_encrypt(der: bytes, message: bytes, seed: bytes) -> bytes:
    n, e = parse_spki_rsa_pubkey(der)
    k = (n.bit_length() + 7) // 8
    em = _oaep_encode_sha256(message, k, seed)
    m = int.from_bytes(em, "big")
    c = pow(m, e, n)
    return c.to_bytes(k, "big")

# ---------------------------------------------------------------------------
# Error type, small helpers
# ---------------------------------------------------------------------------

class YsptpError(Exception):
    """Port of anyhow::Error for this program (message carries full context)."""


def now_f64() -> float:
    return time.time()


def current_time_ms() -> int:
    return int(now_f64() * 1000.0)


def rust_round(x: float) -> float:
    """Rust f64::round(): halfway cases away from zero (we only use >= 0)."""
    return math.floor(x + 0.5)


def sha1_upper(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest().upper()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def log_program_initializing() -> None:
    print("程序初始化中", file=sys.stderr)


def log_program_fetching() -> None:
    print("程序获取直播源中", file=sys.stderr)


def log_channel_ready(channel: str) -> None:
    print(f"程序已成功获取{channel}的直播源", file=sys.stderr)


def java_string_hashcode(value: str) -> int:
    """Java String.hashCode() with 32-bit wrapping (Rust wrapping_mul/add)."""
    h = 0
    for ch in value:
        h = ((h * 31) + ord(ch)) & 0xFFFFFFFF
    return h - 0x100000000 if h & 0x80000000 else h


def java_uuid_from_hashes(msb_hash: int, lsb_hash: int) -> str:
    # Rust: (msb_hash as i64 as u64 as u128) << 64 | (lsb_hash as i64 as u64 as u128)
    msb = msb_hash & 0xFFFFFFFFFFFFFFFF
    lsb = lsb_hash & 0xFFFFFFFFFFFFFFFF
    return f"{(msb << 64) | lsb:032x}"


def native_day0_ms(now_s: int) -> int:
    return 86_400_000 * ((now_s + 28_800) // 86_400) - 28_800_000


def compute_fingerprint(x_uid: str, now_ms: int):
    day0_ms = native_day0_ms(now_ms // 1000)
    first = sha256_hex(f"{AK}{x_uid}{now_ms}{day0_ms}")
    return sha256_hex(first), now_ms, day0_ms


def random_hex_string(length: int) -> str:
    return "".join(secrets.choice("0123456789abcdef") for _ in range(length))


def random_mac_address() -> str:
    raw = bytearray(secrets.token_bytes(6))
    raw[0] = (raw[0] | 0x02) & 0xFE
    return ":".join(f"{b:02x}" for b in raw)


def sanitize_profile_id(value: str) -> str:
    out = []
    last_underscore = False
    for ch in value:
        if ch.isascii() and ch.isalnum():
            out.append(ch.lower())
            last_underscore = False
        elif not last_underscore:
            out.append("_")
            last_underscore = True
    return "".join(out).strip("_")


def resolution_from_screen_param(screen_param: str) -> str:
    parts = screen_param.split("-")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f"{parts[0]}*{parts[1]}"
    return "3840*2160"


def channels():
    return [
        ("cctv1", "Live1717729995180256"),
        ("cctv2", "Live1718261577870260"),
        ("cctv3", "Live1718261955077261"),
        ("cctv4", "Live1718276148119264"),
        ("cctv5", "Live1719474204987287"),
        ("cctv5p", "Live1719473996025286"),
        ("cctv7", "Live1718276412224269"),
        ("cctv8", "Live1718276458899270"),
        ("cctv9", "Live1718276503187272"),
        ("cctv10", "Live1718276550002273"),
        ("cctv11", "Live1718276603690275"),
        ("cctv12", "Live1718276623932276"),
        ("cctv13", "Live1718276575708274"),
        ("cctv14", "Live1718276498748271"),
        ("cctv15", "Live1718276319614267"),
        ("cctv16", "Live1718276256572265"),
        ("cctv17", "Live1718276138318263"),
        ("cctv4k", "Live1767871224782105"),
        ("cctv8k", "Live1688400593818102"),
        ("cctv164k", "Live1704966749996185"),
        ("cgtn", "Live1719392219423280"),
        ("cgtnfr", "Live1719392670442283"),
        ("cgtnru", "Live1719392779653284"),
        ("cgtnar", "Live1719392885692285"),
        ("cgtnes", "Live1719392560433282"),
        ("cgtndoc", "Live1719392360336281"),
    ]


def channel_by_name(name: str):
    for c, live_id in channels():
        if c == name:
            return live_id
    return None


def has_http_400(lower: str) -> bool:
    return "http 400" in lower or "status=400" in lower


def is_session_core_http_400(lower: str) -> bool:
    return has_http_400(lower) and any(
        needle in lower for needle in ("app/start", "live/v1/01", "live/v1/02", "vdn", "heartbeat")
    )


def is_control_plane_http_400(lower: str) -> bool:
    return has_http_400(lower) and any(
        needle in lower
        for needle in (
            "app/start", "dictionary", "app event", "page event", "cloud_get", "cloud get",
            "cloud_register", "cloud register", "device info", "heartbeat", "index",
            "drm config", "version config", "live/v1/01", "live/v1/02", "vdn", "collect report",
        )
    )


def is_session_invalidating_error(message: str) -> bool:
    lower = message.lower()
    return (
        "app/start" in lower
        or "decrypt" in lower
        or "session_key" in lower
        or "missing encrypted session key" in lower
        or is_session_core_http_400(lower)
        or "live/v1/01 http 400" in lower
        or "live/v1/01 http 401" in lower
        or "live/v1/01 http 403" in lower
        or "live/v1/02 http 400" in lower
        or "live/v1/02 http 401" in lower
        or "live/v1/02 http 403" in lower
    )


def is_identity_recoverable_error(message: str) -> bool:
    lower = message.lower()
    return (
        is_session_invalidating_error(message)
        or is_control_plane_http_400(lower)
        or "upstream m3u8 http 400" in lower
        or "upstream m3u8 http 401" in lower
        or "upstream m3u8 http 403" in lower
        or "upstream m3u8 http 404" in lower
        or "playlist response missing" in lower
        or "invalid playlist status" in lower
        or "connection reset" in lower
        or "connection refused" in lower
        or "operation timed out" in lower
        or "timed out" in lower
        or "network is unreachable" in lower
        or "nodename nor servname" in lower
        or "failed to lookup address" in lower
    )


def normalize_channel(raw: str) -> str:
    key = raw.strip().strip("/").lower()
    if key.endswith(".m3u8"):
        key = key[: -len(".m3u8")]
    if key in ("cctv16-4k", "cctv16_4k", "cctv16/4k", "cctv16(4k)"):
        return "cctv164k"
    return key


# ---------------------------------------------------------------------------
# Data records (mirrors the Rust structs; JSON keys stay sorted on dump)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DeviceProfile:
    android_id: str = ""
    mac: str = ""
    hardware: str = ""
    board: str = ""
    brand: str = ""
    device: str = ""
    manufacturer: str = ""
    model: str = ""
    product: str = ""
    tags: str = ""
    build_type: str = ""
    user: str = ""
    resolution: str = ""
    display: str = ""
    version_id: str = ""
    host: str = ""
    fingerprint: str = ""
    report_model: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceProfile":
        return cls(**{f.name: str(d.get(f.name, "")) for f in dataclasses.fields(cls)})


@dataclasses.dataclass
class DeviceState:
    schema_version: int = 1
    profile_source: str = ""
    profile: DeviceProfile = dataclasses.field(default_factory=DeviceProfile)
    screen_param: str = ""
    cast_model: str = ""
    x_uid: str = ""
    cloud_guid: str = ""
    registered_at: float = 0.0
    updated_at: float = 0.0


@dataclasses.dataclass
class Identity:
    x_uid: str = ""
    x_fingerprint: str = ""
    fingerprint_timestamp_ms: int = 0
    headers: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ChannelEntry:
    channel: str = ""
    live_id: str = ""
    final_url: str = ""
    playback_headers: dict = dataclasses.field(default_factory=dict)
    android_id: str = ""
    x_uid: str = ""
    rate: str = ""
    rate_name: str = ""
    raw_live_host: str = ""
    final_host: str = ""
    refreshed_at: float = 0.0
    expires_at: float = 0.0
    generation: int = 0
    last_refresh_error: str = ""
    last_refresh_failed_at: float = 0.0
    session_generation: int = 0

    def fresh(self, now: float) -> bool:
        return bool(self.final_url) and now < self.expires_at

    def stale_usable(self, now: float, ttl: float) -> bool:
        return bool(self.final_url) and ttl > 0.0 and now < self.expires_at + ttl

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelEntry":
        kwargs = {}
        for f in dataclasses.fields(cls):
            v = d.get(f.name, f.default if f.default is not dataclasses.MISSING else None)
            if f.name == "playback_headers":
                v = dict(v) if isinstance(v, dict) else {}
            kwargs[f.name] = v
        return cls(**kwargs)


@dataclasses.dataclass
class PlaylistCacheEntry:
    body: str = ""
    content_type: str = ""
    cached_at: float = 0.0
    expires_at: float = 0.0
    final_url: str = ""
    android_id: str = ""
    proxy_origin: str = ""
    proxy_prefix: str = ""


@dataclasses.dataclass
class PlaylistCacheProbe:
    state: str = ""
    age_ms: int | None = None


@dataclasses.dataclass
class AppStartResult:
    session_key: str = ""


@dataclasses.dataclass
class Live01Result:
    live_url: str = ""
    rate: str = ""
    rate_name: str = ""


@dataclasses.dataclass
class VdnGetStreamResult:
    final_url: str = ""
    app_sign: str = ""
    app_random_str: str = ""


def inspect_playlist_cache(cached, entry: ChannelEntry, proxy_origin: str,
                           proxy_prefix, now: float) -> PlaylistCacheProbe:
    if cached is None:
        return PlaylistCacheProbe(state="miss:not_found", age_ms=None)
    age_ms = int(rust_round(max(0.0, now - cached.cached_at) * 1000.0))
    fresh = now < cached.expires_at
    same_final_url = cached.final_url == entry.final_url
    same_android_id = cached.android_id == entry.android_id
    same_origin = cached.proxy_origin == proxy_origin
    same_prefix = cached.proxy_prefix == (proxy_prefix or "")
    if fresh and same_final_url and same_android_id and same_origin and same_prefix:
        return PlaylistCacheProbe(state="hit", age_ms=age_ms)
    reasons = []
    if not fresh:
        reasons.append("expired")
    if not same_final_url:
        reasons.append("final_url_changed")
    if not same_android_id:
        reasons.append("android_id_changed")
    if not same_origin:
        reasons.append("origin_changed")
    if not same_prefix:
        reasons.append("prefix_changed")
    if not reasons:
        reasons.append("unknown")
    return PlaylistCacheProbe(state="miss:" + "+".join(reasons), age_ms=age_ms)
# ---------------------------------------------------------------------------
# Device profile pool (converted verbatim from device_profile_pool() in main-2.rs)
# ---------------------------------------------------------------------------

DEVICE_PROFILE_POOL = [
    {
        "source": "sony_8k_pool.XR-85Z9K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-85Z9K",
        "report_model": "XR85Z9K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_85Z9K",
        "screen_param": "7680-4320-280",
        "cast_model": "XR-85Z9K",
    },
    {
        "source": "sony_8k_pool.XR-75Z9K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-75Z9K",
        "report_model": "XR75Z9K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_75Z9K",
        "screen_param": "7680-4320-260",
        "cast_model": "XR-75Z9K",
    },
    {
        "source": "sony_8k_pool.XR-85Z9J",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-85Z9J",
        "report_model": "XR85Z9J",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2021.XR_85Z9J",
        "screen_param": "7680-4320-280",
        "cast_model": "XR-85Z9J",
    },
    {
        "source": "sony_8k_pool.XR-75Z9J",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-75Z9J",
        "report_model": "XR75Z9J",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2021.XR_75Z9J",
        "screen_param": "7680-4320-260",
        "cast_model": "XR-75Z9J",
    },
    {
        "source": "sony_8k_pool.KD-98ZG9",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "KD-98ZG9",
        "report_model": "KD98ZG9",
        "hardware": "mt5893",
        "board": "mt5893",
        "version_id": "SONYTV.2019.KD_98ZG9",
        "screen_param": "7680-4320-320",
        "cast_model": "KD-98ZG9",
    },
    {
        "source": "sony_8k_pool.KD-85ZG9",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "KD-85ZG9",
        "report_model": "KD85ZG9",
        "hardware": "mt5893",
        "board": "mt5893",
        "version_id": "SONYTV.2019.KD_85ZG9",
        "screen_param": "7680-4320-280",
        "cast_model": "KD-85ZG9",
    },
    {
        "source": "sony_8k_pool.KD-85ZH8",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "KD-85ZH8",
        "report_model": "KD85ZH8",
        "hardware": "mt5893",
        "board": "mt5893",
        "version_id": "SONYTV.2020.KD_85ZH8",
        "screen_param": "7680-4320-280",
        "cast_model": "KD-85ZH8",
    },
    {
        "source": "sony_8k_pool.KD-75ZH8",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "KD-75ZH8",
        "report_model": "KD75ZH8",
        "hardware": "mt5893",
        "board": "mt5893",
        "version_id": "SONYTV.2020.KD_75ZH8",
        "screen_param": "7680-4320-260",
        "cast_model": "KD-75ZH8",
    },
    {
        "source": "samsung_8k_pool.QA85QN900A",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN900A",
        "report_model": "QA85QN900A",
        "hardware": "s5e9925",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2021.QN900A",
        "screen_param": "7680-4320-280",
        "cast_model": "QA85QN900A",
    },
    {
        "source": "samsung_8k_pool.QA75QN900A",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA75QN900A",
        "report_model": "QA75QN900A",
        "hardware": "s5e9925",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2021.QN900A",
        "screen_param": "7680-4320-260",
        "cast_model": "QA75QN900A",
    },
    {
        "source": "samsung_8k_pool.QA85QN900B",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN900B",
        "report_model": "QA85QN900B",
        "hardware": "s5e9925",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2022.QN900B",
        "screen_param": "7680-4320-280",
        "cast_model": "QA85QN900B",
    },
    {
        "source": "samsung_8k_pool.QA75QN900B",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA75QN900B",
        "report_model": "QA75QN900B",
        "hardware": "s5e9925",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2022.QN900B",
        "screen_param": "7680-4320-260",
        "cast_model": "QA75QN900B",
    },
    {
        "source": "samsung_8k_pool.QA85QN900C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN900C",
        "report_model": "QA85QN900C",
        "hardware": "s5e9935",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2023.QN900C",
        "screen_param": "7680-4320-280",
        "cast_model": "QA85QN900C",
    },
    {
        "source": "samsung_8k_pool.QA75QN900C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA75QN900C",
        "report_model": "QA75QN900C",
        "hardware": "s5e9935",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2023.QN900C",
        "screen_param": "7680-4320-260",
        "cast_model": "QA75QN900C",
    },
    {
        "source": "samsung_8k_pool.QA85QN900D",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN900D",
        "report_model": "QA85QN900D",
        "hardware": "s5e9945",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2024.QN900D",
        "screen_param": "7680-4320-280",
        "cast_model": "QA85QN900D",
    },
    {
        "source": "samsung_8k_pool.QA98QN990C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA98QN990C",
        "report_model": "QA98QN990C",
        "hardware": "s5e9935",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2023.QN990C",
        "screen_param": "7680-4320-320",
        "cast_model": "QA98QN990C",
    },
    {
        "source": "samsung_8k_pool.QA85QN800C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN800C",
        "report_model": "QA85QN800C",
        "hardware": "s5e9935",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2023.QN800C",
        "screen_param": "7680-4320-280",
        "cast_model": "QA85QN800C",
    },
    {
        "source": "samsung_8k_pool.QA75QN800D",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA75QN800D",
        "report_model": "QA75QN800D",
        "hardware": "s5e9945",
        "board": "neo8k",
        "version_id": "SAMSUNGTV.2024.QN800D",
        "screen_param": "7680-4320-260",
        "cast_model": "QA75QN800D",
    },
    {
        "source": "lg_8k_pool.OLED88Z1PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED88Z1PCA",
        "report_model": "OLED88Z1PCA",
        "hardware": "alpha9gen4",
        "board": "lg8k",
        "version_id": "LGTV.2021.OLED88Z1",
        "screen_param": "7680-4320-320",
        "cast_model": "OLED88Z1PCA",
    },
    {
        "source": "lg_8k_pool.OLED77Z1PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED77Z1PCA",
        "report_model": "OLED77Z1PCA",
        "hardware": "alpha9gen4",
        "board": "lg8k",
        "version_id": "LGTV.2021.OLED77Z1",
        "screen_param": "7680-4320-260",
        "cast_model": "OLED77Z1PCA",
    },
    {
        "source": "lg_8k_pool.OLED88Z2PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED88Z2PCA",
        "report_model": "OLED88Z2PCA",
        "hardware": "alpha9gen5",
        "board": "lg8k",
        "version_id": "LGTV.2022.OLED88Z2",
        "screen_param": "7680-4320-320",
        "cast_model": "OLED88Z2PCA",
    },
    {
        "source": "lg_8k_pool.OLED77Z2PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED77Z2PCA",
        "report_model": "OLED77Z2PCA",
        "hardware": "alpha9gen5",
        "board": "lg8k",
        "version_id": "LGTV.2022.OLED77Z2",
        "screen_param": "7680-4320-260",
        "cast_model": "OLED77Z2PCA",
    },
    {
        "source": "lg_8k_pool.OLED88Z3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED88Z3PCA",
        "report_model": "OLED88Z3PCA",
        "hardware": "alpha9gen6",
        "board": "lg8k",
        "version_id": "LGTV.2023.OLED88Z3",
        "screen_param": "7680-4320-320",
        "cast_model": "OLED88Z3PCA",
    },
    {
        "source": "lg_8k_pool.OLED77Z3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED77Z3PCA",
        "report_model": "OLED77Z3PCA",
        "hardware": "alpha9gen6",
        "board": "lg8k",
        "version_id": "LGTV.2023.OLED77Z3",
        "screen_param": "7680-4320-260",
        "cast_model": "OLED77Z3PCA",
    },
    {
        "source": "lg_8k_pool.OLED88Z4PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED88Z4PCA",
        "report_model": "OLED88Z4PCA",
        "hardware": "alpha9gen7",
        "board": "lg8k",
        "version_id": "LGTV.2024.OLED88Z4",
        "screen_param": "7680-4320-320",
        "cast_model": "OLED88Z4PCA",
    },
    {
        "source": "lg_8k_pool.86QNED99",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "86QNED99",
        "report_model": "86QNED99",
        "hardware": "alpha9gen4",
        "board": "lg8k",
        "version_id": "LGTV.2021.86QNED99",
        "screen_param": "7680-4320-300",
        "cast_model": "86QNED99",
    },
    {
        "source": "sony_4k_pool.XR-85X95K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-85X95K",
        "report_model": "XR85X95K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_85X95K",
        "screen_param": "3840-2160-300",
        "cast_model": "XR-85X95K",
    },
    {
        "source": "sony_4k_pool.XR-75X95K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-75X95K",
        "report_model": "XR75X95K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_75X95K",
        "screen_param": "3840-2160-280",
        "cast_model": "XR-75X95K",
    },
    {
        "source": "sony_4k_pool.XR-65X90K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-65X90K",
        "report_model": "XR65X90K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_65X90K",
        "screen_param": "3840-2160-260",
        "cast_model": "XR-65X90K",
    },
    {
        "source": "sony_4k_pool.XR-55X90K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-55X90K",
        "report_model": "XR55X90K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_55X90K",
        "screen_param": "3840-2160-240",
        "cast_model": "XR-55X90K",
    },
    {
        "source": "sony_4k_pool.XR-65A95K",
        "brand": "Sony",
        "manufacturer": "Sony",
        "model": "XR-65A95K",
        "report_model": "XR65A95K",
        "hardware": "mt5895",
        "board": "mt5895",
        "version_id": "SONYTV.2022.XR_65A95K",
        "screen_param": "3840-2160-260",
        "cast_model": "XR-65A95K",
    },
    {
        "source": "samsung_4k_pool.QA85QN90C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA85QN90C",
        "report_model": "QA85QN90C",
        "hardware": "s5e9935",
        "board": "neo4k",
        "version_id": "SAMSUNGTV.2023.QN90C",
        "screen_param": "3840-2160-300",
        "cast_model": "QA85QN90C",
    },
    {
        "source": "samsung_4k_pool.QA75QN90C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA75QN90C",
        "report_model": "QA75QN90C",
        "hardware": "s5e9935",
        "board": "neo4k",
        "version_id": "SAMSUNGTV.2023.QN90C",
        "screen_param": "3840-2160-280",
        "cast_model": "QA75QN90C",
    },
    {
        "source": "samsung_4k_pool.QA65QN90C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA65QN90C",
        "report_model": "QA65QN90C",
        "hardware": "s5e9935",
        "board": "neo4k",
        "version_id": "SAMSUNGTV.2023.QN90C",
        "screen_param": "3840-2160-260",
        "cast_model": "QA65QN90C",
    },
    {
        "source": "samsung_4k_pool.QA55QN90C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA55QN90C",
        "report_model": "QA55QN90C",
        "hardware": "s5e9935",
        "board": "neo4k",
        "version_id": "SAMSUNGTV.2023.QN90C",
        "screen_param": "3840-2160-240",
        "cast_model": "QA55QN90C",
    },
    {
        "source": "samsung_4k_pool.QA65S95C",
        "brand": "Samsung",
        "manufacturer": "Samsung",
        "model": "QA65S95C",
        "report_model": "QA65S95C",
        "hardware": "s5e9935",
        "board": "oled4k",
        "version_id": "SAMSUNGTV.2023.S95C",
        "screen_param": "3840-2160-260",
        "cast_model": "QA65S95C",
    },
    {
        "source": "lg_4k_pool.OLED83C3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED83C3PCA",
        "report_model": "OLED83C3PCA",
        "hardware": "alpha9gen6",
        "board": "lg4k",
        "version_id": "LGTV.2023.OLED83C3",
        "screen_param": "3840-2160-300",
        "cast_model": "OLED83C3PCA",
    },
    {
        "source": "lg_4k_pool.OLED77C3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED77C3PCA",
        "report_model": "OLED77C3PCA",
        "hardware": "alpha9gen6",
        "board": "lg4k",
        "version_id": "LGTV.2023.OLED77C3",
        "screen_param": "3840-2160-280",
        "cast_model": "OLED77C3PCA",
    },
    {
        "source": "lg_4k_pool.OLED65C3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED65C3PCA",
        "report_model": "OLED65C3PCA",
        "hardware": "alpha9gen6",
        "board": "lg4k",
        "version_id": "LGTV.2023.OLED65C3",
        "screen_param": "3840-2160-260",
        "cast_model": "OLED65C3PCA",
    },
    {
        "source": "lg_4k_pool.OLED55C3PCA",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "OLED55C3PCA",
        "report_model": "OLED55C3PCA",
        "hardware": "alpha9gen6",
        "board": "lg4k",
        "version_id": "LGTV.2023.OLED55C3",
        "screen_param": "3840-2160-240",
        "cast_model": "OLED55C3PCA",
    },
    {
        "source": "lg_4k_pool.86QNED90",
        "brand": "LG",
        "manufacturer": "LGE",
        "model": "86QNED90",
        "report_model": "86QNED90",
        "hardware": "alpha7gen5",
        "board": "lg4k",
        "version_id": "LGTV.2022.86QNED90",
        "screen_param": "3840-2160-300",
        "cast_model": "86QNED90",
    },
    {
        "source": "tcl_8k_pool.85X925PRO",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "85X925 PRO",
        "report_model": "85X925PRO",
        "hardware": "mt9615",
        "board": "tcl8k",
        "version_id": "TCLTV.2021.X925PRO",
        "screen_param": "7680-4320-280",
        "cast_model": "85X925 PRO",
    },
    {
        "source": "tcl_8k_pool.75X925PRO",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "75X925 PRO",
        "report_model": "75X925PRO",
        "hardware": "mt9615",
        "board": "tcl8k",
        "version_id": "TCLTV.2021.X925PRO",
        "screen_param": "7680-4320-260",
        "cast_model": "75X925 PRO",
    },
    {
        "source": "tcl_4k_pool.85C845",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "85C845",
        "report_model": "85C845",
        "hardware": "mt9615",
        "board": "tcl4k",
        "version_id": "TCLTV.2023.C845",
        "screen_param": "3840-2160-300",
        "cast_model": "85C845",
    },
    {
        "source": "tcl_4k_pool.75C845",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "75C845",
        "report_model": "75C845",
        "hardware": "mt9615",
        "board": "tcl4k",
        "version_id": "TCLTV.2023.C845",
        "screen_param": "3840-2160-280",
        "cast_model": "75C845",
    },
    {
        "source": "tcl_4k_pool.65C845",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "65C845",
        "report_model": "65C845",
        "hardware": "mt9615",
        "board": "tcl4k",
        "version_id": "TCLTV.2023.C845",
        "screen_param": "3840-2160-260",
        "cast_model": "65C845",
    },
    {
        "source": "tcl_4k_pool.75C745",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "75C745",
        "report_model": "75C745",
        "hardware": "mt9615",
        "board": "tcl4k",
        "version_id": "TCLTV.2023.C745",
        "screen_param": "3840-2160-280",
        "cast_model": "75C745",
    },
    {
        "source": "tcl_4k_pool.65C745",
        "brand": "TCL",
        "manufacturer": "TCL",
        "model": "65C745",
        "report_model": "65C745",
        "hardware": "mt9615",
        "board": "tcl4k",
        "version_id": "TCLTV.2023.C745",
        "screen_param": "3840-2160-260",
        "cast_model": "65C745",
    },
    {
        "source": "changhong_4k_pool.U65G7",
        "brand": "CHANGHONG",
        "manufacturer": "CHANGHONG",
        "model": "U65G7",
        "report_model": "U65G7",
        "hardware": "mt9632",
        "board": "changhong4k",
        "version_id": "CHANGHONGTV.2022.U65G7",
        "screen_param": "3840-2160-260",
        "cast_model": "U65G7",
    },
    {
        "source": "changhong_4k_pool.U55G7",
        "brand": "CHANGHONG",
        "manufacturer": "CHANGHONG",
        "model": "U55G7",
        "report_model": "U55G7",
        "hardware": "mt9632",
        "board": "changhong4k",
        "version_id": "CHANGHONGTV.2022.U55G7",
        "screen_param": "3840-2160-240",
        "cast_model": "U55G7",
    },
    {
        "source": "changhong_4k_pool.L55QCN1",
        "brand": "CHANGHONG",
        "manufacturer": "CHANGHONG",
        "model": "L55QCN1",
        "report_model": "L55QCN1",
        "hardware": "mt9632",
        "board": "changhong4k",
        "version_id": "CHANGHONGTV.2021.L55QCN1",
        "screen_param": "3840-2160-240",
        "cast_model": "L55QCN1",
    },
    {
        "source": "changhong_4k_pool.U43QCN1",
        "brand": "CHANGHONG",
        "manufacturer": "CHANGHONG",
        "model": "U43QCN1",
        "report_model": "U43QCN1",
        "hardware": "mt9632",
        "board": "changhong4k",
        "version_id": "CHANGHONGTV.2021.U43QCN1",
        "screen_param": "3840-2160-220",
        "cast_model": "U43QCN1",
    },
    {
        "source": "changhong_4k_pool.UD65YC5500UA",
        "brand": "CHANGHONG",
        "manufacturer": "CHANGHONG",
        "model": "UD65YC5500UA",
        "report_model": "UD65YC5500UA",
        "hardware": "mt9632",
        "board": "changhong4k",
        "version_id": "CHANGHONGTV.2020.UD65YC5500UA",
        "screen_param": "3840-2160-260",
        "cast_model": "UD65YC5500UA",
    },
]

# ---------------------------------------------------------------------------
# Device identity / state files
# ---------------------------------------------------------------------------

def random_device_template() -> dict:
    return secrets.choice(DEVICE_PROFILE_POOL)


def device_profile_from_template(template: dict, android_id: str, mac: str) -> DeviceProfile:
    brand_id = sanitize_profile_id(template["brand"])
    model_id = sanitize_profile_id(template["model"])
    device = f"{brand_id}_{model_id}"
    product = f"{brand_id}_{model_id}"
    return DeviceProfile(
        android_id=android_id,
        mac=mac,
        hardware=template["hardware"],
        board=template["board"],
        brand=template["brand"],
        device=device,
        manufacturer=template["manufacturer"],
        model=template["model"],
        product=product,
        tags="release-keys",
        build_type="user",
        user="build",
        resolution=resolution_from_screen_param(template["screen_param"]),
        display=f"{template['model']}-user 13 {template['version_id']} 2024 release-keys",
        version_id=template["version_id"],
        host=f"{brand_id}-tv-build",
        fingerprint=(
            f"{template['manufacturer']}/{product}/{device}:13/"
            f"{template['version_id']}/2024:user/release-keys"
        ),
        report_model=template["report_model"],
    )


def default_device_state() -> DeviceState:
    template = random_device_template()
    profile = device_profile_from_template(template, random_hex_string(16), random_mac_address())
    return DeviceState(
        schema_version=1,
        profile_source=template["source"],
        profile=profile,
        screen_param=template["screen_param"],
        cast_model=template["cast_model"],
        x_uid=compute_x_uid(profile),
        cloud_guid="",
        registered_at=0.0,
        updated_at=now_f64(),
    )


def binary_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__)) or "."


def load_device_state(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return default_device_state(), False
    try:
        data = json.loads(text)
    except ValueError:
        return default_device_state(), False
    try:
        profile = DeviceProfile.from_dict(data.get("profile", {}))
        state = DeviceState(
            schema_version=int(data.get("schema_version", 1)),
            profile_source=str(data.get("profile_source", "")),
            profile=profile,
            screen_param=str(data.get("screen_param", "")),
            cast_model=str(data.get("cast_model", "")),
            x_uid=str(data.get("x_uid", "")),
            cloud_guid=str(data.get("cloud_guid", "")),
            registered_at=float(data.get("registered_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )
    except (ValueError, TypeError, AttributeError):
        return default_device_state(), False
    if len(state.profile.android_id) != 16 or not state.profile.mac:
        return default_device_state(), False
    state.x_uid = compute_x_uid(state.profile)
    if not state.screen_param:
        state.screen_param = "7680-4320-280"
    if not state.cast_model:
        state.cast_model = state.profile.model
    return state, True


def save_device_state(path: str, state: DeviceState) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(dataclasses.asdict(state), indent=2, ensure_ascii=False,
                           sort_keys=True) + "\n")


def infer_os_version(profile: DeviceProfile) -> str:
    if ":" in profile.fingerprint:
        tail = profile.fingerprint.split(":", 1)[1]
        if "/" in tail:
            return tail.split("/", 1)[0]
    return ""


def infer_sdk_int(profile: DeviceProfile) -> str:
    major = infer_os_version(profile).split(".")[0] if infer_os_version(profile) else ""
    return {
        "13": "33", "12": "31", "11": "30", "10": "29",
        "9": "28", "8": "26", "7": "24", "6": "23",
    }.get(major, "")


def compute_x_uid(profile: DeviceProfile) -> str:
    build_identity_str = (
        "1698"
        + profile.hardware + profile.board + profile.brand + profile.device
        + profile.manufacturer + profile.model + profile.product + profile.tags
        + profile.build_type + profile.user + profile.resolution + profile.mac
    )
    uuid_part = java_uuid_from_hashes(
        java_string_hashcode(build_identity_str),
        java_string_hashcode(profile.model),
    )
    return sha1_upper(f"{profile.android_id}|{uuid_part}")


def build_identity(profile: DeviceProfile, app_channel: str, version: str) -> Identity:
    x_uid = compute_x_uid(profile)
    x_fingerprint, ts, _day0 = compute_fingerprint(x_uid, current_time_ms())
    headers = {
        "Accept": "application/json",
        "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
        "Referer": "api.cctv.cn",
        "User-Agent": "cctv_app_tv",
        "UID": profile.android_id,
        "appChannel": app_channel,
        "X-Uid": x_uid,
        "X-Fingerprint": x_fingerprint,
        "X-Version": version,
        "Content-Type": "application/json; charset=utf-8",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Cache-Control": "no-cache",
    }
    return Identity(x_uid=x_uid, x_fingerprint=x_fingerprint,
                   fingerprint_timestamp_ms=ts, headers=headers)


def next_request_timestamp_ms() -> int:
    return current_time_ms()


def fresh_headers(template: dict, content_type: str, accept=None, force_ts=None) -> dict:
    headers = {}
    if accept is not None:
        headers["Accept"] = accept
    headers["X-Timestamp"] = str(force_ts if force_ts is not None else next_request_timestamp_ms())
    headers["X-Nonce"] = str(uuid.uuid4())
    for key in ("Accept-Language", "Referer", "User-Agent", "UID",
                "appChannel", "X-Uid", "X-Fingerprint", "X-Version"):
        if key in template:
            headers[key] = template[key]
    headers["Content-Type"] = content_type
    for key in ("Connection", "Accept-Encoding", "Cache-Control"):
        if key in template:
            headers[key] = template[key]
    return headers


# ---------------------------------------------------------------------------
# JSON / crypto wrappers (mirror the Rust helpers byte-for-byte)
# ---------------------------------------------------------------------------

def compact_json_bytes(value, escape_forward_slashes: bool) -> bytes:
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    if escape_forward_slashes:
        text = text.replace("/", "\\/")
    return text.encode("utf-8")


def normalize_aes_key(value: str) -> bytes:
    raw = value.encode("utf-8")
    return (raw + b"\x00" * 32)[:32]


def aes_gcm_decrypt_b64(value: str, key: str) -> str:
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise YsptpError(f"base64 decode failed: {e}")
    if len(raw) <= 12:
        raise YsptpError("AES-GCM payload too short")
    try:
        plain = aes_gcm_decrypt(normalize_aes_key(key), raw[:12], raw[12:])
    except ValueError:
        raise YsptpError("AES-GCM decrypt failed")
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as e:
        raise YsptpError(f"AES-GCM plaintext is not UTF-8: {e}")


def aes_gcm_encrypt_b64(value: str, key: str) -> str:
    nonce = secrets.token_bytes(12)
    try:
        encrypted = aes_gcm_encrypt(normalize_aes_key(key), nonce, value.encode("utf-8"))
    except ValueError:
        raise YsptpError("AES-GCM encrypt failed")
    return base64.b64encode(nonce + encrypted).decode("ascii")


def rsa_encrypt_device_id(device_id: str) -> str:
    try:
        der = base64.b64decode(RSA_PUBLIC_KEY_B64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise YsptpError(f"RSA public key base64 decode failed: {e}")
    n, e = parse_spki_rsa_pubkey(der)
    k = (n.bit_length() + 7) // 8
    hlen = 32  # SHA-256
    chunk_size = k - 2 * hlen - 2  # 190 for a 2048-bit key
    out = bytearray()
    data = device_id.encode("utf-8")
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        out += rsa_oaep_sha256_encrypt(der, chunk, secrets.token_bytes(hlen))
    return base64.b64encode(bytes(out)).decode("ascii")


def report_model_from_build(profile: DeviceProfile) -> str:
    return profile.model.replace(profile.manufacturer, "").replace(" ", "")


def app_start_field(value, limit: int) -> str:
    return str(value)[:limit] if limit >= 0 else str(value)


def build_report_common_value(profile: DeviceProfile, x_uid: str, app_channel: str,
                              version: str, sdk_version: str, data_time_ms: int) -> dict:
    model = profile.report_model or report_model_from_build(profile)
    return {
        "cctv_id": app_start_field(x_uid, 64),
        "device_id": app_start_field(profile.android_id, 64),
        "idfa": "",
        "idfv": "",
        "user_id": "",
        "app_key": app_start_field(REPORT_APP_KEY, 64),
        "imei": "",
        "android_id": app_start_field(profile.android_id, 64),
        "mac": app_start_field(profile.mac, 64),
        "device_builder_type": app_start_field(profile.build_type, 64),
        "device_hardware": app_start_field(profile.hardware, 64),
        "device_board": app_start_field(profile.board, 64),
        "device_brand": app_start_field(profile.brand, 64),
        "device_params": app_start_field(profile.device, 64),
        "device_display": app_start_field(profile.display, 64),
        "device_version_id": app_start_field(profile.version_id, 64),
        "device_host": app_start_field(profile.host, 128),
        "device_product": app_start_field(profile.product, 64),
        "device_tags": app_start_field(profile.tags, 64),
        "device_user": app_start_field(profile.user, 30),
        "device_fingerprint": app_start_field(profile.fingerprint, 128),
        "device_manufacturer": app_start_field(profile.manufacturer, 64),
        "device_model": app_start_field(model, 50),
        "device_resolution": app_start_field(profile.resolution, 20),
        "system_type": "Android",
        "device_type": "TV",
        "app_language": "CHINESE",
        "app_version": app_start_field(version, 30),
        "sdk_version": app_start_field(sdk_version, 30),
        "os_version": app_start_field(infer_os_version(profile), 20),
        "app_channel": app_start_field(app_channel, 50),
        "data_time": app_start_field(data_time_ms, 13),
    }


def build_app_start_body(profile: DeviceProfile, x_uid: str, app_channel: str,
                         version: str, data_time_ms: int) -> dict:
    return {
        "key": "app_start_d1",
        "value": build_report_common_value(profile, x_uid, app_channel, version, "",
                                           data_time_ms),
    }


def build_collect_headers(profile: DeviceProfile) -> dict:
    release = infer_os_version(profile)
    return {
        "Content-type": "application/x-www-form-urlencoded",
        "Charset": "UTF-8",
        "User-Agent": (
            "Dalvik/2.1.0 (Linux; U; Android "
            + (release if release else "Android")
            + f"; {profile.model} Build/{profile.version_id})"
        ),
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
    }


def parse_result_code(value) -> int | None:
    if isinstance(value, dict):
        for key in ("result", "code", "errCode", "errcode", "ret"):
            if key in value:
                raw = value[key]
                if isinstance(raw, bool):
                    continue
                if isinstance(raw, int):
                    return raw
                if isinstance(raw, str):
                    try:
                        return int(raw)
                    except ValueError:
                        pass
        for key in ("data", "error", "response"):
            if key in value:
                found = parse_result_code(value[key])
                if found is not None:
                    return found
    return None


def extract_guid(value) -> str:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, dict):
            guid = data.get("guid")
            if isinstance(guid, str):
                return guid
    return ""


def root_headers(version: str) -> dict:
    return {
        "X-Uid": "ROOT",
        "X-Fingerprint": "ROOT",
        "X-Nonce": str(uuid.uuid4()),
        "X-Timestamp": str(next_request_timestamp_ms()),
        "X-Version": version,
        "UID": "ROOT",
        "Referer": "api.cctv.cn",
        "User-Agent": "cctv_app_tv",
        "appChannel": "ROOT",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
    }


def build_vdn_appcommon(version: str) -> str:
    return json.dumps({"adid": "", "av": version, "an": VDN_APP_NAME, "ap": "cctv_app_tv"},
                      separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def url_host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def live_playback_host_needs_signed_headers(host: str) -> bool:
    lower = host.lower()
    return "liveali" in lower or "liveten" in lower


def default_cctv_playback_headers(uid: str) -> dict:
    return {
        "UID": uid,
        "APPID": AK,
        "Referer": "api.cctv.cn",
        "User-Agent": "cctv_app_tv",
    }


def cctv_playback_headers_for_entry(entry: ChannelEntry) -> dict:
    headers = default_cctv_playback_headers(entry.android_id)
    for key in ("UID", "APPID", "APPRANDOMSTR", "Referer", "User-Agent", "APPSIGN"):
        value = entry.playback_headers.get(key)
        if value:
            headers[key] = value
    return headers


def entry_missing_signed_playback_headers(entry: ChannelEntry) -> bool:
    host = entry.final_host or url_host(entry.final_url)
    if not live_playback_host_needs_signed_headers(host):
        return False
    for key in ("APPID", "APPSIGN", "APPRANDOMSTR"):
        value = entry.playback_headers.get(key, "")
        if not value.strip():
            return True
    return False


def ts_path_matches_final_url(ts_path: str, final_url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(final_url)
    except ValueError:
        return False
    prefix = parsed.path
    if prefix.endswith(".m3u8"):
        prefix = prefix[: -len(".m3u8")]
    return bool(prefix) and ts_path.startswith(prefix)


def generate_app_random_str() -> str:
    return (
        f"{secrets.randbits(32):08x}-0000-"
        f"{secrets.randbits(32) & 0xFFFF:04x}-0000-"
        f"00000000{secrets.randbits(32) & 0xFFFF:04x}"
    )


def compute_vdn_code(app_secret: str, random_str=None):
    r = random_str if random_str is not None else generate_app_random_str()
    return md5_hex(f"{AK}{app_secret}{r}"), r


def is_ts_url(value: str) -> bool:
    try:
        path = urllib.parse.urlsplit(value).path
    except ValueError:
        return False
    return path.lower().endswith(".ts")


def _rs_lines(text: str):
    """Split like Rust str::lines(): split on \n, strip one trailing \r."""
    norm = text.replace("\r\n", "\n")
    lines = norm.split("\n")
    if norm.endswith("\n"):
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def rewrite_playlist_urls(text: str, base_url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(base_url)
        has_base = bool(parts.scheme and parts.netloc)
    except ValueError:
        has_base = False
    out = []
    for line in _rs_lines(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
        elif has_base:
            out.append(urllib.parse.urljoin(base_url, stripped))
        else:
            out.append(stripped)
    return "".join(ln + "\n" for ln in out)


def header_value_clean(value: str) -> str:
    return value.replace("\r", "").replace("\n", "")

# ---------------------------------------------------------------------------
# HTTP client (urllib-based; mirrors the reqwest blocking clients)
# ---------------------------------------------------------------------------

def form_urlencode_value(s: str) -> str:
    """WHATWG application/x-www-form-urlencoded value encoding.

    Mirrors serde_urlencoded: everything except [A-Za-z0-9] and ``*-._``
    is percent-encoded (uppercase hex, UTF-8), space becomes ``+``.
    """
    out = []
    for byte in s.encode("utf-8"):
        ch = chr(byte)
        if ch.isascii() and (ch.isalnum() or ch in "*-._"):
            out.append(ch)
        elif byte == 0x20:
            out.append("+")
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def form_encode(pairs) -> bytes:
    return "&".join(f"{form_urlencode_value(k)}={form_urlencode_value(v)}"
                    for k, v in pairs).encode("utf-8")


def _normalize_network_error(e: Exception) -> str:
    """Map Python network errors to the message shapes the Rust reqwest
    errors produced, so is_identity_recoverable_error() keeps working."""
    if isinstance(e, ConnectionRefusedError):
        return f"connection refused: {e}"
    if isinstance(e, ConnectionResetError):
        return f"connection reset by peer: {e}"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return f"operation timed out: {e}"
    if isinstance(e, socket.gaierror):
        return f"failed to lookup address: {e}"
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, Exception):
            return _normalize_network_error(reason)
        return f"network error: {e}"
    return f"network error: {e}"


class HttpResponse:
    def __init__(self, status: int, reason: str, headers: dict, body: bytes):
        self.status = status
        self.reason = reason
        self.headers = headers  # lower-cased keys
        self.body = body

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpClient:
    """Blocking HTTP client with cookie jar, timeout and TLS options.

    Replaces reqwest::blocking::Client.  gzip responses are transparently
    decoded (reqwest was built with the ``gzip`` feature).
    """

    def __init__(self, timeout: float, insecure_tls: bool):
        self._timeout = max(timeout, 0.1)
        if insecure_tls:
            self._ctx = ssl._create_unverified_context()
        else:
            self._ctx = ssl.create_default_context()
        jar = http.cookiejar.CookieJar()
        https_handler = urllib.request.HTTPSHandler(context=self._ctx)
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            https_handler,
        )
        self._jar = jar

    def _send(self, method: str, url: str, headers: dict, data: bytes | None) -> HttpResponse:
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in headers.items():
            # urllib skips empty values on some paths; force them through.
            req.add_header(k, v)
        try:
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    status = resp.status
                    reason = resp.reason or ""
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    body = resp.read()
            except urllib.error.HTTPError as e:
                status = e.code
                reason = e.reason or ""
                try:
                    hdrs = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
                except Exception:
                    hdrs = {}
                try:
                    body = e.read()
                except Exception:
                    body = b""
        except Exception as e:
            raise YsptpError(_normalize_network_error(e))
        if "gzip" in hdrs.get("content-encoding", "").lower():
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError):
                pass
        return HttpResponse(status, reason, hdrs, body)

    def get(self, url: str, headers: dict) -> HttpResponse:
        return self._send("GET", url, headers, None)

    def post_bytes(self, url: str, headers: dict, data: bytes) -> HttpResponse:
        return self._send("POST", url, headers, data)

    def post_json(self, url: str, headers: dict, body) -> HttpResponse:
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
        return self._send("POST", url, headers, data)

    def post_form(self, url: str, headers: dict, pairs) -> HttpResponse:
        return self._send("POST", url, headers, form_encode(pairs))


def response_json(resp: HttpResponse):
    status = resp.status
    text = resp.text()
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    return status, text, value


def http_status_text(status: int, reason: str) -> str:
    return f"{status} {reason}".strip() if reason else str(status)

# ---------------------------------------------------------------------------
# Resolver: cache/state, background queues, worker threads
# ---------------------------------------------------------------------------

class ResolverState:
    def __init__(self):
        self.cache: dict[str, ChannelEntry] = {}
        self.playlist_cache: dict[str, PlaylistCacheEntry] = {}
        self.background_refreshing: dict[str, float] = {}
        self.generation = 0
        self.session_generation = 0
        self.app_session = None  # AppSession | None
        self.last_control_request_at = 0.0
        self.last_business_end_at = 0.0
        self.refreshing_channel = ""
        self.last_error = ""
        self.last_error_at = 0.0
        self.identity_reset_error_count = 0
        self.identity_reset_count = 0
        self.last_identity_reset_at = 0.0
        self.last_identity_reset_reason = ""
        self.ad_display = False


@dataclasses.dataclass
class AppSession:
    profile: DeviceProfile
    identity: Identity
    client: HttpClient
    session_key: str
    cloud_guid: str
    version: str
    screen_param: str
    cast_model: str
    created_at: float
    generation: int
    last_heartbeat_at: float
    heartbeat_count: int
    last_heartbeat_result: str
    last_heartbeat_error: str


class RefreshQueue:
    def __init__(self):
        self.cond = threading.Condition()
        self.queued: collections.deque[str] = collections.deque()
        self.queued_channels: dict[str, float] = {}
        self.running_channel = ""
        self.running_started_at = 0.0
        self.enqueued_total = 0
        self.dropped_total = 0
        self.completed_total = 0
        self.failed_total = 0
        self.last_enqueued_at = 0.0
        self.last_finished_at = 0.0
        self.last_dropped_at = 0.0
        self.last_drop_reason = ""


class HttpQueue:
    def __init__(self):
        self.cond = threading.Condition()
        self.queued: collections.deque = collections.deque()
        self.active_workers = 0
        self.enqueued_total = 0
        self.rejected_total = 0
        self.completed_total = 0
        self.panicked_total = 0
        self.last_enqueued_at = 0.0
        self.last_completed_at = 0.0
        self.last_rejected_at = 0.0


class Resolver:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.generic_client = HttpClient(args.timeout, args.insecure_tls)
        self.state = ResolverState()
        self.state_lock = threading.Lock()
        self.control_lock = threading.Lock()
        self.playlist_locks_lock = threading.Lock()
        self.playlist_locks: dict[str, threading.Lock] = {}
        self.refresh_queue = RefreshQueue()
        self.http_queue = HttpQueue()
        # cache restored from the meta JSON written by write_meta()
        self.state.cache = load_cache(args.meta_json)

    # -- background threads -------------------------------------------------

    def start_heartbeat(self):
        def loop():
            while True:
                time.sleep(max(self.args.heartbeat_interval, 0.0))
                try:
                    self.send_heartbeat()
                except Exception:
                    pass
        t = threading.Thread(target=loop, daemon=True, name="heartbeat")
        t.start()

    def start_refresh_worker(self):
        t = threading.Thread(target=self.refresh_worker_loop, daemon=True,
                             name="refresh-worker")
        t.start()

    def start_http_workers(self):
        count = max(1, int(self.args.http_workers))
        for i in range(count):
            t = threading.Thread(target=self.http_worker_loop, daemon=True,
                                 name=f"http-worker-{i}")
            t.start()

    def enqueue_http_request(self, request) -> None:
        """Called by the accept loop; 503s here when the queue is full."""
        now = now_f64()
        q = self.http_queue
        with q.cond:
            limit = int(self.args.http_queue_limit)
            if limit == 0 or len(q.queued) >= limit:
                q.rejected_total += 1
                q.last_rejected_at = now
                respond_text(request, 503, "server busy\n",
                             "text/plain; charset=utf-8",
                             [("Retry-After", "1")])
                return
            q.queued.append(request)
            q.enqueued_total += 1
            q.last_enqueued_at = now
            q.cond.notify()

    def http_worker_loop(self):
        q = self.http_queue
        while True:
            with q.cond:
                while not q.queued:
                    q.cond.wait()
                request = q.queued.popleft()
                q.active_workers += 1
            try:
                handle_request(self, request)
                ok = True
            except Exception:
                ok = False
                try:
                    request.sock.close()
                except OSError:
                    pass
            with q.cond:
                q.active_workers = max(0, q.active_workers - 1)
                q.last_completed_at = now_f64()
                if ok:
                    q.completed_total += 1
                else:
                    q.panicked_total += 1

    def enqueue_background_refresh(self, channel: str):
        now = now_f64()
        q = self.refresh_queue
        with q.cond:
            if q.running_channel == channel or channel in q.queued_channels:
                return "already_active", ""
            limit = int(self.args.background_refresh_queue_limit)
            if limit == 0 or len(q.queued) >= limit:
                q.dropped_total += 1
                q.last_dropped_at = now
                if limit == 0:
                    q.last_drop_reason = (
                        f"background refresh queue disabled; channel={channel}")
                else:
                    q.last_drop_reason = (
                        f"background refresh queue full ({len(q.queued)}/{limit}); "
                        f"channel={channel}")
                return "rejected", q.last_drop_reason
            q.queued.append(channel)
            q.queued_channels[channel] = now
            q.enqueued_total += 1
            q.last_enqueued_at = now
            q.cond.notify()
            return "enqueued", ""

    def refresh_worker_loop(self):
        q = self.refresh_queue
        while True:
            with q.cond:
                while not q.queued:
                    q.cond.wait()
                channel = q.queued.popleft()
                q.queued_channels.pop(channel, None)
                q.running_channel = channel
                q.running_started_at = now_f64()
            ok = self.refresh_channel_background(channel)
            with q.cond:
                q.running_channel = ""
                q.running_started_at = 0.0
                q.last_finished_at = now_f64()
                if ok:
                    q.completed_total += 1
                else:
                    q.failed_total += 1

    # -- control-plane pacing ----------------------------------------------

    @contextlib.contextmanager
    def control_request_slot(self, _label: str):
        min_interval = max(0.0, float(self.args.refresh_interval))
        while True:
            with self.state_lock:
                last = self.state.last_control_request_at
                wait = 0.0 if last <= 0.0 else min_interval - (now_f64() - last)
            if wait <= 0.0:
                break
            time.sleep(wait)
        jitter = self.control_step_jitter_duration()
        if jitter is not None:
            time.sleep(jitter)
        try:
            yield
        finally:
            with self.state_lock:
                self.state.last_control_request_at = now_f64()

    def control_step_jitter_duration(self):
        min_ms = int(self.args.control_step_jitter_min_ms)
        max_ms = int(self.args.control_step_jitter_max_ms)
        if max_ms == 0:
            return None
        upper = max(max_ms, min_ms)
        span = upper - min_ms
        extra = secrets.randbelow(span + 1) if span else 0
        return (min_ms + extra) / 1000.0

    # -- session bookkeeping -------------------------------------------------

    def app_session_fresh(self, sess: AppSession) -> bool:
        return float(self.args.session_ttl) <= 0.0 or \
            (now_f64() - sess.created_at) < float(self.args.session_ttl)

    def app_session_ttl_remaining(self, sess: AppSession):
        if float(self.args.session_ttl) <= 0.0:
            return None
        return float(self.args.session_ttl) - (now_f64() - sess.created_at)

    # -- error recovery / identity reset -------------------------------------

    def record_recoverable_error(self, reason: str) -> bool:
        if not is_identity_recoverable_error(reason):
            return False
        threshold = int(self.args.identity_reset_error_threshold)
        if threshold == 0:
            return False
        with self.state_lock:
            if self.state.last_identity_reset_at > 0.0 and \
                    now_f64() - self.state.last_identity_reset_at < \
                    float(self.args.identity_reset_cooldown):
                return False
            self.state.identity_reset_error_count = \
                min(self.state.identity_reset_error_count + 1, 2 ** 31 - 1)
            should_reset = self.state.identity_reset_error_count >= threshold
        if should_reset:
            self.reset_identity_and_cache(reason)
            return True
        try:
            self.write_meta()
        except Exception:
            pass
        return False

    def clear_recoverable_error_count(self):
        with self.state_lock:
            self.state.identity_reset_error_count = 0

    def reset_identity_and_cache(self, reason: str):
        try:
            os.remove(self.args.device_json)
        except OSError:
            pass
        try:
            os.remove(self.args.meta_json)
        except OSError:
            pass
        with self.state_lock:
            self.state.cache.clear()
            self.state.playlist_cache.clear()
            self.state.background_refreshing.clear()
            self.state.app_session = None
            self.state.refreshing_channel = ""
            self.state.identity_reset_error_count = 0
            self.state.identity_reset_count += 1
            self.state.last_identity_reset_at = now_f64()
            self.state.last_identity_reset_reason = reason
            self.state.last_error = f"identity reset after recoverable errors: {reason}"
            self.state.last_error_at = now_f64()
        with self.refresh_queue.cond:
            self.refresh_queue.cond.notify_all()

    # -- channel refresh -------------------------------------------------------

    def ensure_channel(self, channel: str) -> ChannelEntry:
        now = now_f64()
        with self.state_lock:
            entry = self.state.cache.get(channel)
            if entry is not None:
                if entry.fresh(now) and not entry_missing_signed_playback_headers(entry):
                    return entry
                if entry.stale_usable(now, float(self.args.stale_while_refresh_ttl)) and \
                        not entry_missing_signed_playback_headers(entry):
                    retry_at = entry.last_refresh_failed_at + \
                        float(self.args.refresh_error_cooldown)
                    started = self.state.background_refreshing.get(channel)
                    should_refresh = now >= retry_at and \
                        (started is None or now - started > 300.0)
                    clone = entry
                    if should_refresh:
                        self.state.background_refreshing[channel] = now
                        status, reason = self.enqueue_background_refresh(channel)
                        if status == "rejected":
                            self.state.background_refreshing.pop(channel, None)
                            e2 = self.state.cache.get(channel)
                            if e2 is not None:
                                e2.last_refresh_error = reason
                                e2.last_refresh_failed_at = now
                    return clone

        with self.control_lock:
            now = now_f64()
            with self.state_lock:
                entry = self.state.cache.get(channel)
                if entry is not None and entry.fresh(now) and \
                        not entry_missing_signed_playback_headers(entry):
                    return entry
            try:
                entry = self.refresh_channel_controlled(channel)
            except YsptpError as err:
                err_text = str(err)
                with self.state_lock:
                    self.state.last_error = err_text
                    self.state.last_error_at = now_f64()
                    last_error = self.state.last_error
                    last_error_at = self.state.last_error_at
                    entry = self.state.cache.get(channel)
                    if entry is not None and entry.final_url:
                        entry.last_refresh_error = last_error
                        entry.last_refresh_failed_at = last_error_at
                        clone = entry
                    else:
                        clone = None
                did_reset = self.record_recoverable_error(err_text)
                if not did_reset:
                    try:
                        self.write_meta()
                    except Exception:
                        pass
                if did_reset or clone is None:
                    raise
                return clone
            self.clear_recoverable_error_count()
            try:
                self.write_meta()
            except Exception:
                pass
            return entry

    def refresh_channel_background(self, channel: str) -> bool:
        try:
            with self.control_lock:
                now = now_f64()
                with self.state_lock:
                    entry = self.state.cache.get(channel)
                    if entry is not None and entry.fresh(now) and \
                            not entry_missing_signed_playback_headers(entry):
                        return True
                self.refresh_channel_controlled(channel)
                try:
                    self.write_meta()
                except Exception:
                    pass
            ok = True
        except YsptpError as err:
            err_text = str(err)
            with self.state_lock:
                self.state.last_error = err_text
                self.state.last_error_at = now_f64()
                last_error = self.state.last_error
                last_error_at = self.state.last_error_at
                entry = self.state.cache.get(channel)
                if entry is not None:
                    entry.last_refresh_error = last_error
                    entry.last_refresh_failed_at = last_error_at
                if is_session_invalidating_error(err_text):
                    self.state.app_session = None
            did_reset = self.record_recoverable_error(err_text)
            if not did_reset:
                try:
                    self.write_meta()
                except Exception:
                    pass
            ok = False
        with self.state_lock:
            self.state.background_refreshing.pop(channel, None)
        return ok

    def refresh_channel_controlled(self, channel: str) -> ChannelEntry:
        with self.state_lock:
            wait = float(self.args.refresh_interval) - \
                (now_f64() - self.state.last_business_end_at)
        if wait > 0.0:
            time.sleep(wait)
        with self.state_lock:
            self.state.refreshing_channel = channel
            self.state.generation += 1
            generation = self.state.generation
        log_program_fetching()
        try:
            app_session = self.get_app_session_controlled()
            live_id = channel_by_name(channel)
            if live_id is None:
                raise YsptpError(f"unknown channel {channel}")
            entry = self.resolve_channel_once(app_session, channel, live_id, generation)
        except YsptpError as err:
            with self.state_lock:
                self.state.last_business_end_at = now_f64()
                self.state.refreshing_channel = ""
                if is_session_invalidating_error(str(err)):
                    self.state.app_session = None
            raise
        with self.state_lock:
            self.state.last_business_end_at = now_f64()
            self.state.refreshing_channel = ""
            self.state.cache[channel] = entry
            self.state.playlist_cache.pop(channel, None)
            self.state.identity_reset_error_count = 0
            self.state.last_error = ""
            log_channel_ready(channel)
        return entry

    # -- app session -------------------------------------------------------------

    def get_app_session_controlled(self) -> AppSession:
        with self.state_lock:
            sess = self.state.app_session
            if sess is not None and self.app_session_fresh(sess):
                return sess
            self.state.app_session = None
        with self.state_lock:
            self.state.session_generation += 1
            gen = self.state.session_generation
        sess = self.bootstrap_session(gen)
        with self.state_lock:
            self.state.app_session = sess
        return sess

    def new_ytpaddr_client(self) -> HttpClient:
        return HttpClient(float(self.args.timeout), bool(self.args.insecure_tls))

    def bootstrap_session(self, generation: int) -> AppSession:
        device_state, existed = load_device_state(self.args.device_json)
        if not existed:
            device_state.updated_at = now_f64()
            try:
                save_device_state(self.args.device_json, device_state)
            except OSError:
                pass
        profile = device_state.profile
        app_channel = "dangbei"
        version = "1.4.1"
        identity = build_identity(profile, app_channel, version)
        device_state.x_uid = identity.x_uid
        client = self.new_ytpaddr_client()
        page_session_id = str(uuid.uuid4())

        with self.control_request_slot("collect_report_start"):
            self.collect_report(profile, {
                "key": "app_start_d1",
                "value": build_report_common_value(
                    profile, identity.x_uid, app_channel, version,
                    COLLECT_SDK_VERSION, current_time_ms()),
            })
        self.dictionary_obtain(version)
        app_start = self.app_start_flow(client, profile, identity, app_channel, version)
        session_key = app_start.session_key

        # The app/start payload above is sent as raw bytes; the app_start
        # event below follows immediately after, same fields, no extra call.
        try:
            self.app_event_flow(profile, identity, app_channel, version)
        except YsptpError:
            pass

        with self.control_request_slot("collect_report_page"):
            page_end_time_ms = current_time_ms()
            page_start_time_ms = page_end_time_ms - 1000
            page_value = build_report_common_value(
                profile, identity.x_uid, app_channel, version, "",
                page_end_time_ms + 2)
            page_value["start_time"] = str(page_start_time_ms)
            page_value["end_time"] = str(page_end_time_ms)
            page_value["duration"] = str(page_end_time_ms - page_start_time_ms)
            page_value["page_name"] = DEFAULT_PAGE_NAME
            page_value["session_id"] = page_session_id
            page_value["network_type"] = "WIFI"
            try:
                self.collect_report(profile, {"key": "page_d1", "value": page_value})
            except YsptpError:
                pass
            page_times = (page_start_time_ms, page_end_time_ms)
        try:
            self.page_event_flow(profile, identity, app_channel, version,
                                 page_session_id, page_times[0], page_times[1])
        except YsptpError:
            pass

        try:
            cloud_guid = self.cloud_registration_flow(identity, identity.x_uid)
        except YsptpError:
            cloud_guid = ""
        now = now_f64()
        device_state.x_uid = identity.x_uid
        device_state.updated_at = now
        if cloud_guid:
            device_state.cloud_guid = cloud_guid
            if device_state.registered_at <= 0.0:
                device_state.registered_at = now
        try:
            save_device_state(self.args.device_json, device_state)
        except OSError:
            pass
        if cloud_guid:
            try:
                self.device_info_report_flow(profile, identity, app_channel, version,
                                             cloud_guid)
            except YsptpError:
                pass
        hb = self.heartbeat_flow(profile, identity, app_channel, version, cloud_guid)
        self.index_flow(client, identity, app_channel)
        self.warmup_flow(client, identity, version)

        return AppSession(
            profile=profile,
            identity=identity,
            client=client,
            session_key=session_key,
            cloud_guid=cloud_guid,
            version=version,
            screen_param=device_state.screen_param,
            cast_model=device_state.cast_model,
            created_at=now_f64(),
            generation=generation,
            last_heartbeat_at=now_f64(),
            heartbeat_count=1,
            last_heartbeat_result=hb,
            last_heartbeat_error="",
        )

    # -- upstream flows --------------------------------------------------------

    def collect_report(self, profile: DeviceProfile, body: dict) -> None:
        headers = build_collect_headers(profile)
        resp = self.generic_client.post_form(
            COLLECT_REPORT_URL, headers,
            [("info", json.dumps(body, separators=(",", ":"), ensure_ascii=False,
                                 sort_keys=True))])
        if not 200 <= resp.status < 300:
            raise YsptpError(f"collect report HTTP {http_status_text(resp.status, resp.reason)}")

    def dictionary_obtain(self, version: str) -> None:
        with self.control_request_slot("dictionary_obtain"):
            resp = self.generic_client.post_bytes(DICTIONARY_URL, root_headers(version), b"")
        if not 200 <= resp.status < 300:
            raise YsptpError(f"dictionary HTTP {http_status_text(resp.status, resp.reason)}")

    def app_start_flow(self, client: HttpClient, profile: DeviceProfile,
                       identity: Identity, app_channel: str, version: str) -> AppStartResult:
        last = ""
        for attempt in range(1, 5):
            if attempt > 1:
                time.sleep(float(attempt - 1))
            with self.control_request_slot("app_start"):
                token_time = current_time_ms()
                xfp, ts, _day0 = compute_fingerprint(identity.x_uid, token_time)
                identity.x_fingerprint = xfp
                identity.fingerprint_timestamp_ms = ts
                identity.headers["X-Fingerprint"] = xfp
                body = build_app_start_body(profile, identity.x_uid, app_channel,
                                            version, current_time_ms())
                payload = compact_json_bytes(body, True)
                headers = fresh_headers(identity.headers, "application/json",
                                        "application/json", ts)
                headers["UID"] = ""
                resp = client.post_bytes(APP_START_URL, headers, payload)
                status, text, value = response_json(resp)
                if 200 <= status < 300:
                    data = value.get("data") if isinstance(value, dict) else None
                    encrypted = ""
                    if isinstance(data, dict):
                        key_val = data.get("key", data)
                        encrypted = key_val if isinstance(key_val, str) else ""
                    elif isinstance(data, str):
                        encrypted = data
                    if encrypted:
                        session_key = aes_gcm_decrypt_b64(encrypted, xfp[:32])
                        return AppStartResult(session_key=session_key)
                last = f"app/start HTTP {status}: {text[:300]}"
        raise YsptpError(last)

    def app_event_flow(self, profile: DeviceProfile, identity: Identity,
                       app_channel: str, version: str) -> None:
        with self.control_request_slot("app_event"):
            event_time = current_time_ms()
            value = build_report_common_value(profile, identity.x_uid, app_channel,
                                              version, "", event_time)
            value["event_id"] = "app_start"
            value["event_name"] = "应用启动"
            value["event_time"] = str(event_time)
            value["network_type"] = "WIFI"
            value["cur_version"] = version
            value["channel"] = app_channel
            value["pre_version"] = version
            body = {"key": "event", "value": value}
            headers = fresh_headers(identity.headers, "application/json",
                                    "application/json", None)
            headers["UID"] = ""
            resp = self.generic_client.post_json(REPORT_SINGLE_URL, headers, body)
            if not 200 <= resp.status < 300:
                raise YsptpError(f"app event HTTP {http_status_text(resp.status, resp.reason)}")

    def page_event_flow(self, profile: DeviceProfile, identity: Identity, app_channel: str,
                        version: str, session_id: str, start_ms: int, end_ms: int) -> None:
        with self.control_request_slot("page_event"):
            value = build_report_common_value(profile, identity.x_uid, app_channel,
                                              version, "", end_ms + 2)
            value["start_time"] = str(start_ms)
            value["end_time"] = str(end_ms)
            value["duration"] = str(max(0, end_ms - start_ms))
            value["page_name"] = DEFAULT_PAGE_NAME
            value["session_id"] = session_id
            value["network_type"] = "WIFI"
            body = {"key": "page_d1", "value": value}
            resp = self.generic_client.post_json(
                REPORT_SINGLE_URL,
                fresh_headers(identity.headers, "application/json", "application/json", None),
                body)
            if not 200 <= resp.status < 300:
                raise YsptpError(f"page event HTTP {http_status_text(resp.status, resp.reason)}")

    def cloud_registration_flow(self, identity: Identity, cloud_device_id: str) -> str:
        body = {"device_name": DEFAULT_DEVICE_NAME,
                "device_id": rsa_encrypt_device_id(cloud_device_id)}
        with self.control_request_slot("cloud_get"):
            resp = self.generic_client.post_json(
                CLOUD_GET_URL,
                fresh_headers(identity.headers, "application/json", "application/json", None),
                body)
        _, _, value = response_json(resp)
        result = parse_result_code(value)
        if result == RESULT_OK:
            return extract_guid(value)
        if result != RESULT_NEEDS_REGISTER and result != RESULT_GET_MISSING_OR_INVALID:
            return ""
        last_result = None
        guid = ""
        for _ in range(2):
            with self.control_request_slot("cloud_register"):
                resp = self.generic_client.post_json(
                    CLOUD_REGISTER_URL,
                    fresh_headers(identity.headers, "application/json", "application/json",
                                  None),
                    body)
            _, _, value = response_json(resp)
            last_result = parse_result_code(value)
            guid = extract_guid(value)
            if guid or last_result != RESULT_REGISTER_RETRY_LATER:
                break
        if guid:
            return guid
        if last_result in (RESULT_OK, RESULT_REGISTERED_ELSEWHERE,
                           RESULT_GET_MISSING_OR_INVALID):
            with self.control_request_slot("cloud_get_after_register"):
                resp = self.generic_client.post_json(
                    CLOUD_GET_URL,
                    fresh_headers(identity.headers, "application/json", "application/json",
                                  None),
                    body)
            _, _, value = response_json(resp)
            return extract_guid(value)
        return ""

    def device_info_report_flow(self, profile: DeviceProfile, identity: Identity,
                                app_channel: str, version: str, cloud_guid: str) -> None:
        with self.control_request_slot("device_info_report"):
            value = build_report_common_value(profile, identity.x_uid, app_channel,
                                              version, "", current_time_ms())
            sdk_int = infer_sdk_int(profile)
            system_info = infer_os_version(profile)
            if sdk_int:
                system_info = sdk_int if not system_info else f"{system_info}/{sdk_int}"
            value["version"] = version
            value["network_status"] = "WiFi"
            value["device_info"] = f"{profile.brand}-{profile.model}"
            value["manufacturer"] = profile.manufacturer
            value["cpu_info"] = ""
            value["chip_info"] = profile.hardware
            value["ram_info"] = ""
            value["memory_info"] = ""
            value["system_info"] = system_info
            value["guid"] = cloud_guid
            body = {"key": "app_device_info", "value": value}
            resp = self.generic_client.post_json(
                REPORT_SINGLE_URL,
                fresh_headers(identity.headers, "application/json", "application/json", None),
                body)
            if not 200 <= resp.status < 300:
                raise YsptpError(f"device info HTTP {http_status_text(resp.status, resp.reason)}")

    def heartbeat_flow(self, profile: DeviceProfile, identity: Identity, app_channel: str,
                       version: str, cloud_guid: str) -> str:
        with self.control_request_slot("heartbeat"):
            value = build_report_common_value(profile, identity.x_uid, app_channel,
                                              version, "", current_time_ms())
            value["network_type"] = "WiFi"
            value["guid"] = cloud_guid
            value["other"] = ""
            body = {"key": "app_heartbeat", "value": value}
            resp = self.generic_client.post_json(
                REPORT_SINGLE_URL,
                fresh_headers(identity.headers, "application/json", "application/json", None),
                body)
            status, _text, value = response_json(resp)
            if not 200 <= status < 300:
                raise YsptpError(f"heartbeat HTTP {status}")
            code = parse_result_code(value)
            return "" if code is None else str(code)

    def index_flow(self, client: HttpClient, identity: Identity, app_channel: str) -> None:
        body = {"channel": app_channel, "source": "application"}
        with self.control_request_slot("index"):
            resp = client.post_json(
                INDEX_URL,
                fresh_headers(identity.headers, "application/json", "application/json", None),
                body)
            if not 200 <= resp.status < 300:
                raise YsptpError(f"index HTTP {http_status_text(resp.status, resp.reason)}")

    def warmup_flow(self, client: HttpClient, identity: Identity, version: str) -> None:
        appcommon = build_vdn_appcommon(version)
        with self.control_request_slot("drm_config"):
            resp = client.post_form(
                DRM_CONFIG_URL,
                fresh_headers(identity.headers, "application/x-www-form-urlencoded", None, None),
                [("appcommon", appcommon)])
            if not 200 <= resp.status < 300:
                raise YsptpError(f"drm config HTTP {http_status_text(resp.status, resp.reason)}")
        url = f"{VERSION_CONFIG_URL}?appcommon={form_urlencode_value(appcommon)}"
        with self.control_request_slot("version_config"):
            headers = fresh_headers(identity.headers, "", None, None)
            headers.pop("Content-Type", None)
            resp = client.get(url, headers)
            if not 200 <= resp.status < 300:
                raise YsptpError(
                    f"version config HTTP {http_status_text(resp.status, resp.reason)}")

    def resolve_channel_once(self, sess: AppSession, channel: str, live_id: str,
                             generation: int) -> ChannelEntry:
        live01 = self.live_v1_01_flow(sess, live_id)
        app_secret = self.live_v1_02_flow(sess)
        vdn = self.vdn_getstream_flow(sess.identity, sess.version, live01.live_url, app_secret)
        now = now_f64()
        playback_headers = default_cctv_playback_headers(sess.profile.android_id)
        playback_headers["APPRANDOMSTR"] = vdn.app_random_str
        playback_headers["APPSIGN"] = vdn.app_sign
        return ChannelEntry(
            channel=channel,
            live_id=live_id,
            final_url=vdn.final_url,
            playback_headers=playback_headers,
            android_id=sess.profile.android_id,
            x_uid=sess.identity.x_uid,
            rate=live01.rate,
            rate_name=live01.rate_name,
            raw_live_host=url_host(live01.live_url),
            final_host=url_host(vdn.final_url),
            refreshed_at=now,
            expires_at=now + float(self.args.cache_ttl),
            generation=generation,
            last_refresh_error="",
            last_refresh_failed_at=0.0,
            session_generation=sess.generation,
        )

    def live_v1_01_flow(self, sess: AppSession, live_id: str) -> Live01Result:
        body = {
            "screenParam": sess.screen_param,
            "rate": "",
            "systemType": "ios",
            "model": sess.cast_model,
            "id": live_id,
            "userId": DEFAULT_LIVE_USER_ID,
            "clientSign": "cctvVideo",
            "deviceId": {"serial": "", "imei": "", "android_id": ""},
        }
        with self.control_request_slot("live_v1_01"):
            resp = sess.client.post_json(
                LIVE_V1_01_URL,
                fresh_headers(sess.identity.headers, "application/json", "application/json",
                              None),
                body)
        status, text, value = response_json(resp)
        if not 200 <= status < 300:
            raise YsptpError(f"live/v1/01 HTTP {status}: {text[:300]}")
        videos = None
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict):
                videos = data.get("videoList") or data.get("videos")
        if not isinstance(videos, list):
            raise YsptpError("live/v1/01 missing videos")
        selected = None
        fallback = None
        for item in videos:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue
            if fallback is None:
                fallback = item
            if item.get("rate") == "36p":
                selected = item
                break
        video = selected if selected is not None else fallback
        if video is None:
            raise YsptpError("live/v1/01 no usable URL")
        raw_url = video.get("url", "")
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            live_url = raw_url
        else:
            live_url = aes_gcm_decrypt_b64(raw_url, sess.session_key)
        rate = video.get("rate")
        rate_name = video.get("rateName")
        return Live01Result(
            live_url=live_url,
            rate=rate if isinstance(rate, str) else "",
            rate_name=rate_name if isinstance(rate_name, str) else "",
        )

    def live_v1_02_flow(self, sess: AppSession) -> str:
        with self.control_request_slot("live_v1_02"):
            encrypted_guid = aes_gcm_encrypt_b64("", sess.session_key)
            body = {"guid": encrypted_guid}
            resp = sess.client.post_json(
                LIVE_V1_02_URL,
                fresh_headers(sess.identity.headers, "application/json", "application/json",
                              None),
                body)
        status, text, value = response_json(resp)
        if not 200 <= status < 300:
            raise YsptpError(f"live/v1/02 HTTP {status}: {text[:300]}")
        encrypted = None
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict):
                for key in ("appSecret", "app_secret"):
                    v = data.get(key)
                    if isinstance(v, str):
                        encrypted = v
                        break
            if encrypted is None and isinstance(data, str):
                encrypted = data
        if encrypted is None:
            raise YsptpError("live/v1/02 missing appSecret")
        return aes_gcm_decrypt_b64(encrypted, sess.session_key)

    def vdn_getstream_flow(self, identity: Identity, version: str, live_url: str,
                           app_secret: str) -> VdnGetStreamResult:
        with self.control_request_slot("vdn_getstream"):
            app_sign, random_str = compute_vdn_code(app_secret, None)
            headers = fresh_headers(identity.headers, "application/x-www-form-urlencoded",
                                    None, None)
            headers["APPID"] = AK
            headers["APPSIGN"] = app_sign
            headers["APPRANDOMSTR"] = random_str
            appcommon = build_vdn_appcommon(version)
            resp = self.generic_client.post_form(
                VDN_GETSTREAM_URL, headers,
                [("appcommon", appcommon), ("url", live_url)])
        status, text, value = response_json(resp)
        if not 200 <= status < 300:
            raise YsptpError(f"VDN HTTP {status}: {text[:300]}")
        if isinstance(value, dict):
            succeed = value.get("succeed")
            succeed_str = succeed.strip('"') if isinstance(succeed, str) else \
                json.dumps(succeed, separators=(",", ":"), ensure_ascii=False).strip('"') \
                if succeed is not None else ""
            if succeed_str == "1":
                url = value.get("url")
                if isinstance(url, str):
                    return VdnGetStreamResult(final_url=url, app_sign=app_sign,
                                              app_random_str=random_str)
        raise YsptpError(f"VDN did not return final URL: {text[:400]}")

    def send_heartbeat(self) -> None:
        if not self.control_lock.acquire(blocking=False):
            return
        try:
            while True:
                with self.state_lock:
                    sess = self.state.app_session
                    if sess is None:
                        return
                    if not self.app_session_fresh(sess):
                        sess.last_heartbeat_error = (
                            "session soft TTL reached; heartbeat paused until next "
                            "on-demand refresh")
                        need_meta = True
                    else:
                        remaining = self.app_session_ttl_remaining(sess)
                        if remaining is not None and \
                                remaining <= max(0.0, float(self.args.heartbeat_ttl_guard)):
                            sess.last_heartbeat_error = (
                                "session TTL guard active; heartbeat skipped with "
                                f"{max(0.0, remaining):.3f}s remaining")
                            need_meta = True
                        else:
                            need_meta = False
                            wait = float(self.args.refresh_interval) - \
                                (now_f64() - self.state.last_business_end_at)
                            if wait > 0.0:
                                do_sleep = True
                            else:
                                do_sleep = False
                    if need_meta:
                        try:
                            self.write_meta()
                        except Exception:
                            pass
                        return
                if do_sleep:
                    time.sleep(wait)
                    continue
                try:
                    code = self.heartbeat_flow(sess.profile, sess.identity, "dangbei",
                                              sess.version, sess.cloud_guid)
                except YsptpError as err:
                    err_text = str(err)
                    with self.state_lock:
                        if self.state.app_session is not None:
                            self.state.app_session.last_heartbeat_error = err_text
                        self.state.last_error = f"heartbeat failed: {err_text}"
                        self.state.last_error_at = now_f64()
                    try:
                        self.write_meta()
                    except Exception:
                        pass
                    raise
                with self.state_lock:
                    self.state.last_business_end_at = now_f64()
                    if self.state.app_session is not None:
                        s = self.state.app_session
                        s.last_heartbeat_at = now_f64()
                        s.heartbeat_count += 1
                        s.last_heartbeat_result = code
                        s.last_heartbeat_error = ""
                try:
                    self.write_meta()
                except Exception:
                    pass
                return
        finally:
            self.control_lock.release()

    # -- playlist proxy ----------------------------------------------------------

    def proxy_playlist_ts_urls(self, playlist: str, proxy_origin: str,
                               proxy_prefix=None) -> str:
        origin = (proxy_prefix or proxy_origin).rstrip("/")
        proxy_query = "/proxy?ts=" if proxy_prefix is not None else "/proxy.ts?="
        out = []
        for line in _rs_lines(playlist):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                out.append(line)
            elif is_ts_url(stripped):
                encoded = base64.urlsafe_b64encode(stripped.encode("utf-8")).rstrip(b"=")
                out.append(f"{origin}{proxy_query}{encoded.decode('ascii')}")
            else:
                out.append(line)
        return "".join(ln + "\n" for ln in out)

    def playback_headers_for_ts_url(self, real_url: str) -> dict:
        try:
            parsed = urllib.parse.urlsplit(real_url)
        except ValueError as e:
            raise YsptpError(f"invalid proxy ts URL: {e}")
        host = parsed.hostname
        if not host:
            raise YsptpError("proxy ts URL missing host")
        ts_path = parsed.path
        same_host_headers = None
        with self.state_lock:
            for entry in self.state.cache.values():
                if entry_missing_signed_playback_headers(entry):
                    continue
                entry_host = entry.final_host or url_host(entry.final_url)
                if entry_host.lower() != host.lower():
                    continue
                headers = cctv_playback_headers_for_entry(entry)
                if ts_path_matches_final_url(ts_path, entry.final_url):
                    return headers
                if same_host_headers is None:
                    same_host_headers = headers
        if same_host_headers is not None:
            return same_host_headers
        if live_playback_host_needs_signed_headers(host):
            raise YsptpError(f"missing cached signed playback headers for {host}")
        uid = current_cctv_android_id(self)
        return default_cctv_playback_headers(uid)

    def fetch_playlist(self, entry: ChannelEntry, proxy_origin: str, proxy_prefix=None):
        now = now_f64()
        with self.state_lock:
            probe = inspect_playlist_cache(self.state.playlist_cache.get(entry.channel),
                                           entry, proxy_origin, proxy_prefix, now)
            if probe.state == "hit":
                cached = self.state.playlist_cache[entry.channel]
                return cached.body, cached.content_type, probe

        with self.playlist_locks_lock:
            lock = self.playlist_locks.get(entry.channel)
            if lock is None:
                lock = threading.Lock()
                self.playlist_locks[entry.channel] = lock
        with lock:
            now = now_f64()
            with self.state_lock:
                probe = inspect_playlist_cache(
                    self.state.playlist_cache.get(entry.channel),
                    entry, proxy_origin, proxy_prefix, now)
                if probe.state == "hit":
                    cached = self.state.playlist_cache[entry.channel]
                    return cached.body, cached.content_type, probe
                miss_probe = probe

            playback_headers = cctv_playback_headers_for_entry(entry)
            try:
                status, content_type, text = fetch_playlist_http1(
                    entry.final_url, playback_headers)
            except YsptpError as err:
                self.record_recoverable_error(str(err))
                raise
            if not 200 <= status < 300:
                err = YsptpError(f"upstream m3u8 HTTP {status}: {text[:300]}")
                self.record_recoverable_error(str(err))
                raise err
            self.clear_recoverable_error_count()
            # Keep upstream playlist content as-is except for making media
            # segment URLs absolute for players that resolve relative paths
            # against this proxy instead of the upstream m3u8 URL.
            body = rewrite_playlist_urls(text, entry.final_url)
            body = self.proxy_playlist_ts_urls(body, proxy_origin, proxy_prefix)
            if float(self.args.playlist_cache_ttl) > 0.0:
                now = now_f64()
                cached = PlaylistCacheEntry(
                    body=body, content_type=content_type, cached_at=now,
                    expires_at=now + float(self.args.playlist_cache_ttl),
                    final_url=entry.final_url, android_id=entry.android_id,
                    proxy_origin=proxy_origin, proxy_prefix=proxy_prefix or "")
                with self.state_lock:
                    self.state.playlist_cache[entry.channel] = cached
            return body, content_type, miss_probe

    # -- status / persistence ------------------------------------------------------

    def status_json(self) -> dict:
        with self.state_lock:
            state_snapshot = {
                "refreshing_channel": self.state.refreshing_channel,
                "last_business_end_at": self.state.last_business_end_at,
                "last_error": self.state.last_error,
                "identity_reset_error_count": self.state.identity_reset_error_count,
                "identity_reset_count": self.state.identity_reset_count,
                "last_identity_reset_at": self.state.last_identity_reset_at,
                "last_identity_reset_reason": self.state.last_identity_reset_reason,
                "ad_display": self.state.ad_display,
                "generation": self.state.generation,
                "session_generation": self.state.session_generation,
                "cache": dict(self.state.cache),
                "playlist_cache": dict(self.state.playlist_cache),
                "background_refreshing": dict(self.state.background_refreshing),
                "app_session": self.state.app_session,
            }
        now = now_f64()
        if os.path.exists(self.args.device_json):
            loaded, existed = load_device_state(self.args.device_json)
            device_state = {
                "exists": existed,
                "android_id": loaded.profile.android_id,
                "mac": loaded.profile.mac,
                "brand": loaded.profile.brand,
                "model": loaded.profile.model,
                "screen_param": loaded.screen_param,
                "x_uid": loaded.x_uid,
                "cloud_guid_present": bool(loaded.cloud_guid),
                "registered_at": loaded.registered_at,
                "updated_at": loaded.updated_at,
            }
        else:
            device_state = {"exists": False}
        cache = {}
        for k, v in state_snapshot["cache"].items():
            cache[k] = {
                "live_id": v.live_id,
                "fresh": v.fresh(now),
                "age_seconds": rust_round((now - v.refreshed_at) * 1000.0) / 1000.0,
                "expires_in_seconds": rust_round((v.expires_at - now) * 1000.0) / 1000.0,
                "rate": v.rate,
                "rate_name": v.rate_name,
                "final_host": v.final_host,
                "generation": v.generation,
                "session_generation": v.session_generation,
                "last_refresh_error": v.last_refresh_error,
            }
        playlist_cache = {}
        for k, v in state_snapshot["playlist_cache"].items():
            playlist_cache[k] = {
                "fresh": now < v.expires_at,
                "age_seconds": rust_round((now - v.cached_at) * 1000.0) / 1000.0,
                "expires_in_seconds": rust_round((v.expires_at - now) * 1000.0) / 1000.0,
                "bytes": len(v.body.encode("utf-8")),
                "content_type": v.content_type,
                "proxy_origin": v.proxy_origin,
                "proxy_prefix": v.proxy_prefix,
            }
        sess = state_snapshot["app_session"]
        if sess is not None:
            expires_in = (float(self.args.session_ttl) - (now - sess.created_at)
                          if float(self.args.session_ttl) > 0.0 else None)
            app_session = {
                "active": True,
                "generation": sess.generation,
                "age_seconds": rust_round((now - sess.created_at) * 1000.0) / 1000.0,
                "fresh": self.app_session_fresh(sess),
                "expires_in_seconds": expires_in,
                "android_id": sess.profile.android_id,
                "mac": sess.profile.mac,
                "brand": sess.profile.brand,
                "model": sess.profile.model,
                "screen_param": sess.screen_param,
                "cast_model": sess.cast_model,
                "x_uid": sess.identity.x_uid,
                "cloud_guid_present": bool(sess.cloud_guid),
                "fingerprint_timestamp_ms": sess.identity.fingerprint_timestamp_ms,
                "heartbeat_count": sess.heartbeat_count,
                "last_heartbeat_age_seconds":
                    rust_round((now - sess.last_heartbeat_at) * 1000.0) / 1000.0,
                "last_heartbeat_result": sess.last_heartbeat_result,
                "last_heartbeat_error": sess.last_heartbeat_error,
            }
        else:
            app_session = {"active": False, "generation": state_snapshot["session_generation"]}
        rq = self.refresh_queue
        with rq.cond:
            refresh_queue = {
                "limit": int(self.args.background_refresh_queue_limit),
                "queued_depth": len(rq.queued),
                "queued_channels": list(rq.queued),
                "running_channel": rq.running_channel,
                "running_age_seconds": (
                    rust_round((now - rq.running_started_at) * 1000.0) / 1000.0
                    if rq.running_started_at > 0.0 else None),
                "enqueued_total": rq.enqueued_total,
                "dropped_total": rq.dropped_total,
                "completed_total": rq.completed_total,
                "failed_total": rq.failed_total,
                "last_enqueued_age_seconds": (
                    rust_round((now - rq.last_enqueued_at) * 1000.0) / 1000.0
                    if rq.last_enqueued_at > 0.0 else None),
                "last_finished_age_seconds": (
                    rust_round((now - rq.last_finished_at) * 1000.0) / 1000.0
                    if rq.last_finished_at > 0.0 else None),
                "last_dropped_age_seconds": (
                    rust_round((now - rq.last_dropped_at) * 1000.0) / 1000.0
                    if rq.last_dropped_at > 0.0 else None),
                "last_drop_reason": rq.last_drop_reason,
            }
        hq = self.http_queue
        with hq.cond:
            http_queue = {
                "workers": max(1, int(self.args.http_workers)),
                "limit": int(self.args.http_queue_limit),
                "queued_depth": len(hq.queued),
                "active_workers": hq.active_workers,
                "enqueued_total": hq.enqueued_total,
                "rejected_total": hq.rejected_total,
                "completed_total": hq.completed_total,
                "panicked_total": hq.panicked_total,
                "last_enqueued_age_seconds": (
                    rust_round((now - hq.last_enqueued_at) * 1000.0) / 1000.0
                    if hq.last_enqueued_at > 0.0 else None),
                "last_completed_age_seconds": (
                    rust_round((now - hq.last_completed_at) * 1000.0) / 1000.0
                    if hq.last_completed_at > 0.0 else None),
                "last_rejected_age_seconds": (
                    rust_round((now - hq.last_rejected_at) * 1000.0) / 1000.0
                    if hq.last_rejected_at > 0.0 else None),
            }
        return {
            "channels": [c for c, _ in channels()],
            "cache_ttl_seconds": float(self.args.cache_ttl),
            "stale_while_refresh_ttl_seconds": float(self.args.stale_while_refresh_ttl),
            "refresh_error_cooldown_seconds": float(self.args.refresh_error_cooldown),
            "playlist_cache_ttl_seconds": float(self.args.playlist_cache_ttl),
            "background_refresh_queue_limit": int(self.args.background_refresh_queue_limit),
            "http_workers": max(1, int(self.args.http_workers)),
            "http_queue_limit": int(self.args.http_queue_limit),
            "identity_reset_error_threshold": int(self.args.identity_reset_error_threshold),
            "identity_reset_cooldown_seconds": float(self.args.identity_reset_cooldown),
            "refresh_interval_seconds": float(self.args.refresh_interval),
            "control_step_jitter_min_ms": int(self.args.control_step_jitter_min_ms),
            "control_step_jitter_max_ms": int(self.args.control_step_jitter_max_ms),
            "heartbeat_interval_seconds": float(self.args.heartbeat_interval),
            "heartbeat_ttl_guard_seconds": float(self.args.heartbeat_ttl_guard),
            "session_ttl_seconds": float(self.args.session_ttl),
            "meta_json": self.args.meta_json,
            "device_json": self.args.device_json,
            "device_state": device_state,
            "refreshing_channel": state_snapshot["refreshing_channel"],
            "background_refreshing": list(state_snapshot["background_refreshing"].keys()),
            "background_refresh_queue": refresh_queue,
            "http_queue": http_queue,
            "generation": state_snapshot["generation"],
            "app_session": app_session,
            "last_business_end_at": state_snapshot["last_business_end_at"],
            "last_error": state_snapshot["last_error"],
            "identity_reset_error_count": state_snapshot["identity_reset_error_count"],
            "identity_reset_count": state_snapshot["identity_reset_count"],
            "last_identity_reset_at": state_snapshot["last_identity_reset_at"],
            "last_identity_reset_reason": state_snapshot["last_identity_reset_reason"],
            "ad_display": state_snapshot["ad_display"],
            "device_profile_pool_size": len(DEVICE_PROFILE_POOL),
            "cache": cache,
            "playlist_cache": playlist_cache,
        }

    def write_meta(self) -> None:
        payload = self.status_json()
        with self.state_lock:
            cache_detail = {k: dataclasses.asdict(v)
                            for k, v in self.state.cache.items()}
        payload["cache_detail"] = cache_detail
        payload["session_detail_note"] = (
            "Session secrets are intentionally not persisted for reuse; "
            "Python process keeps live session in memory only.")
        payload["device_state_note"] = (
            "Randomized TV device identity is persisted in device-state-rs.json "
            "after cloud registration succeeds; a new identity reset creates a "
            "new profile and MAC.")
        parent = os.path.dirname(os.path.abspath(self.args.meta_json))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.args.meta_json, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return {}
    detail = value.get("cache_detail") if isinstance(value, dict) else None
    if not isinstance(detail, dict):
        return {}
    try:
        return {k: ChannelEntry.from_dict(v) for k, v in detail.items()
                if isinstance(v, dict)}
    except (ValueError, TypeError):
        return {}

# ---------------------------------------------------------------------------
# Upstream HTTP/1.1 fetches (http.client replaces the raw TcpStream code)
# ---------------------------------------------------------------------------

def _playback_request_headers(playback_headers: dict, extra: dict | None = None) -> dict:
    headers = {}
    for key in ("UID", "APPID", "APPRANDOMSTR", "Referer", "User-Agent", "APPSIGN"):
        value = playback_headers.get(key)
        if value is not None and str(value).strip():
            headers[key] = header_value_clean(str(value))
    headers["Accept"] = "*/*"
    headers["Accept-Encoding"] = "identity"
    headers["Connection"] = "close"
    if extra:
        headers.update(extra)
    return headers


def fetch_playlist_http1(final_url: str, playback_headers: dict):
    """GET the upstream m3u8 over plain HTTP/1.1. Returns (status, content_type, text)."""
    try:
        parts = urllib.parse.urlsplit(final_url)
    except ValueError as e:
        raise YsptpError(f"invalid final URL: {e}")
    if parts.scheme != "http":
        raise YsptpError(
            f"manual playlist fetch currently expects http URL, got {parts.scheme}")
    host = parts.hostname
    if not host:
        raise YsptpError("final URL missing host")
    port = parts.port or 80
    host_header = f"{host}:{parts.port}" if parts.port else host
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    headers = _playback_request_headers(playback_headers)
    headers["Host"] = host_header
    conn = http.client.HTTPConnection(host, port, timeout=15)
    try:
        try:
            conn.request("GET", target, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            raise YsptpError(_normalize_network_error(e))
        status = resp.status
        content_type = resp.getheader("Content-Type") or "application/vnd.apple.mpegurl"
        try:
            body = resp.read()
        except Exception as e:
            raise YsptpError(_normalize_network_error(e))
    finally:
        conn.close()
    return status, content_type, body.decode("utf-8", errors="replace")


def build_proxy_ts_response_http1(real_url: str, playback_headers: dict,
                                  range_header: str | None, timeout: float):
    """Fetch an upstream .ts segment; returns (status, headers, body)."""
    try:
        parts = urllib.parse.urlsplit(real_url)
    except ValueError as e:
        raise YsptpError(f"invalid proxy ts URL: {e}")
    if parts.scheme != "http":
        raise YsptpError(f"proxy ts currently requires http URL, got {parts.scheme}")
    host = parts.hostname
    if not host:
        raise YsptpError("proxy ts URL missing host")
    port = parts.port or 80
    host_header = f"{host}:{parts.port}" if parts.port else host
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    extra = {}
    if range_header:
        extra["Range"] = header_value_clean(range_header)
    headers = _playback_request_headers(playback_headers, extra)
    headers["Host"] = host_header
    conn = http.client.HTTPConnection(host, port, timeout=max(1.0, timeout))
    try:
        try:
            conn.request("GET", target, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            raise YsptpError(_normalize_network_error(e))
        status = resp.status
        out_headers = []
        has_content_type = False
        for key, value in resp.getheaders():
            kl = key.lower()
            if kl in ("content-type", "content-range", "accept-ranges"):
                if kl == "content-type":
                    has_content_type = True
                out_headers.append((key, header_value_clean(value)))
        if not has_content_type:
            out_headers.append(("Content-Type", "video/MP2T"))
        out_headers.append(("Cache-Control", "no-cache, no-store, max-age=0"))
        out_headers.append(("Access-Control-Allow-Origin", "*"))
        try:
            body = resp.read()
        except Exception as e:
            raise YsptpError(_normalize_network_error(e))
    finally:
        conn.close()
    return status, out_headers, body


# ---------------------------------------------------------------------------
# Request helpers (operate on _ProxyRequest)
# ---------------------------------------------------------------------------

def query_param(url: str, name: str):
    if "?" not in url:
        return None
    _, query = url.split("?", 1)
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key == name:
            return value
    return None


def query_param_decoded(url: str, name: str):
    if "?" not in url:
        return None
    _, query = url.split("?", 1)
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if key == name:
            return value
    return None


def request_header(request, name: str):
    value = request.headers.get(name.lower())
    if value is None:
        return None
    value = value.strip()
    return value or None


def first_header_value(value: str) -> str:
    return value.split(",", 1)[0].strip()


def request_origin(request, fallback_port: int) -> str:
    scheme = request_header(request, "X-Forwarded-Proto")
    scheme = first_header_value(scheme) if scheme else ""
    if not scheme:
        scheme = "http"
    host = request_header(request, "X-Forwarded-Host") or request_header(request, "Host")
    host = first_header_value(host) if host else ""
    if not host:
        host = f"127.0.0.1:{fallback_port}"
    scheme = scheme[:-len("://")] if scheme.endswith("://") else scheme
    return f"{scheme}://{host.rstrip('/')}"


def proxy_prefix_from_url(url: str):
    raw = query_param_decoded(url, "prefix")
    if raw is None:
        return None
    prefix = raw.strip().rstrip("/")
    if not prefix:
        return None
    try:
        parsed = urllib.parse.urlsplit(prefix)
    except ValueError as e:
        raise YsptpError(f"invalid prefix URL: {e}")
    if parsed.scheme not in ("http", "https"):
        raise YsptpError("prefix scheme must be http or https")
    if not parsed.hostname:
        raise YsptpError("prefix URL missing host")
    return prefix


def build_4kfifa_playlist(origin: str, proxy_prefix=None) -> str:
    origin = origin.rstrip("/")
    suffix = f"?prefix={proxy_prefix.rstrip('/')}" if proxy_prefix else ""
    return (
        "#EXTM3U x-tvg-url=\"http://epg.51zmt.top:8000/e.xml\"\n"
        "#EXTINF:-1 tvg-name=\"cctv5\" tvg-logo=\"https://cdn.jsdelivr.net/gh/sparkssssssssss/epg/logo/cctv5.png\" group-title=\"高码率CCTV5\",cctv5\n"
        f"{origin}/cctv5.m3u8{suffix}\n"
        "#EXTINF:-1 tvg-name=\"cctv5+\" tvg-logo=\"https://cdn.jsdelivr.net/gh/sparkssssssssss/epg/logo/cctv5+.png\" group-title=\"高码率CCTV5+\",cctv5+\n"
        f"{origin}/cctv5p.m3u8{suffix}\n"
        "#EXTINF:-1 tvg-name=\"cctv16-4k\" tvg-logo=\"https://cdn.jsdelivr.net/gh/sparkssssssssss/epg/logo/cctv16.png\" group-title=\"4KUHD-FIFA\",cctv16-4k\n"
        f"{origin}/cctv16.m3u8{suffix}\n"
        "#EXTINF:-1 tvg-name=\"cctv4k\" tvg-logo=\"https://cdn.jsdelivr.net/gh/sparkssssssssss/epg/logo/cctv4k.png\" group-title=\"4KUHD-FIFA\",cctv4k\n"
        f"{origin}/cctv4k.m3u8{suffix}\n"
        "#EXTINF:-1 tvg-name=\"cctv8k\" tvg-logo=\"https://cdn.jsdelivr.net/gh/sparkssssssssss/epg/logo/cctv8k.png\" group-title=\"8K频道\",cctv8k\n"
        f"{origin}/cctv8k.m3u8{suffix}\n"
    )


def proxy_ts_token(url: str):
    if "?" not in url:
        return None
    _, query = url.split("?", 1)
    if query.startswith("="):
        token = query[1:]
        return token or None
    token = query_param(url, "ts")
    if token:
        return token
    token = query_param(url, "url")
    if token:
        return token
    if query and "=" not in query:
        return query
    return None


def decode_proxy_ts_url(token: str) -> str:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as e:
        raise YsptpError(f"invalid proxy ts token: {e}")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise YsptpError(f"proxy ts token is not UTF-8: {e}")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as e:
        raise YsptpError(f"proxy ts token is not a URL: {e}")
    if parsed.scheme != "http":
        raise YsptpError(f"proxy ts currently requires http URL, got {parsed.scheme}")
    if not parsed.path.lower().endswith(".ts"):
        raise YsptpError("proxy ts URL is not a .ts segment")
    return value


def current_cctv_android_id(resolver: Resolver) -> str:
    if os.path.exists(resolver.args.device_json):
        loaded, _ = load_device_state(resolver.args.device_json)
        if loaded.profile.android_id:
            return loaded.profile.android_id
    with resolver.state_lock:
        sess = resolver.state.app_session
        if sess is not None and sess.profile.android_id:
            return sess.profile.android_id
        for entry in resolver.state.cache.values():
            if entry.android_id:
                return entry.android_id
    raise YsptpError("missing cctv android_id")


def status_authorized(request, url: str) -> bool:
    if query_param(url, "token") == SWITCH_TOKEN:
        return True
    if request_header(request, "X-Status-Token") == SWITCH_TOKEN:
        return True
    return request_header(request, "Authorization") == f"Bearer {SWITCH_TOKEN}"


def switch_authorized(request, url: str) -> bool:
    if query_param(url, "token") == SWITCH_TOKEN:
        return True
    if request_header(request, "X-Switch-Token") == SWITCH_TOKEN:
        return True
    return request_header(request, "Authorization") == f"Bearer {SWITCH_TOKEN}"

# ---------------------------------------------------------------------------
# Minimal HTTP/1.1 server (replaces tiny_http: accept -> bounded queue ->
# worker threads handle each request; one request per connection)
# ---------------------------------------------------------------------------

_HTTP_REASONS = {
    200: "OK", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
    405: "Method Not Allowed", 502: "Bad Gateway", 503: "Service Unavailable",
}


class _ProxyRequest:
    def __init__(self, method: str, url: str, headers: dict, sock: socket.socket):
        self.method = method
        self.url = url
        self.headers = headers  # lower-cased names -> value (first wins)
        self.sock = sock
        self._responded = False


def _send_response(request: _ProxyRequest, status: int, headers, body: bytes) -> None:
    if request._responded:
        return
    request._responded = True
    reason = _HTTP_REASONS.get(status, "")
    lines = [f"HTTP/1.1 {status} {reason}".rstrip()]
    for key, value in headers:
        lines.append(f"{key}: {value}")
    lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace")
    try:
        request.sock.sendall(head + body)
    except OSError:
        pass
    finally:
        try:
            request.sock.close()
        except OSError:
            pass


def respond_text(request: _ProxyRequest, status: int, body: str,
                 content_type: str, extra) -> None:
    headers = [("Content-Type", content_type)]
    headers.extend(extra)
    _send_response(request, status, headers, body.encode("utf-8"))


def respond_redirect(request: _ProxyRequest, location: str) -> None:
    respond_text(request, 302, "", "text/plain; charset=utf-8",
                 [("Location", location), ("Cache-Control", "no-store")])


def respond_proxy_ts(resolver: Resolver, request: _ProxyRequest, url: str) -> None:
    token = proxy_ts_token(url)
    if not token:
        respond_text(request, 400, "missing proxy ts token\n",
                     "text/plain; charset=utf-8", [])
        return
    try:
        real_url = decode_proxy_ts_url(token)
    except YsptpError as err:
        respond_text(request, 400, f"{err}\n", "text/plain; charset=utf-8", [])
        return
    try:
        playback_headers = resolver.playback_headers_for_ts_url(real_url)
    except YsptpError as err:
        respond_text(request, 500, f"{err}\n", "text/plain; charset=utf-8", [])
        return
    range_header = request_header(request, "Range")
    try:
        status, headers, body = build_proxy_ts_response_http1(
            real_url, playback_headers, range_header, float(resolver.args.timeout))
    except YsptpError:
        respond_redirect(request, AD_REDIRECT_URL)
        return
    _send_response(request, status, headers, body)


def handle_request(resolver: Resolver, request: _ProxyRequest) -> None:
    method = request.method
    url = request.url
    path = url.split("?", 1)[0]
    if path == "/":
        respond_redirect(request, DEFAULT_REDIRECT_URL)
        return
    if path == "/status":
        if not status_authorized(request, url):
            respond_text(request, 401, "unauthorized\n", "text/plain; charset=utf-8", [])
            return
        respond_text(request, 200,
                     json.dumps(resolver.status_json(), indent=2, ensure_ascii=False,
                                sort_keys=True),
                     "application/json; charset=utf-8", [])
        return
    if path == "/switch":
        if method not in ("GET", "HEAD"):
            respond_text(request, 405, "method not allowed\n",
                         "text/plain; charset=utf-8", [])
            return
        if not switch_authorized(request, url):
            respond_text(request, 401, "unauthorized\n", "text/plain; charset=utf-8", [])
            return
        ad = query_param(url, "ad")
        if ad is None:
            respond_text(request, 400, "missing ad\n", "text/plain; charset=utf-8", [])
            return
        if ad == "display":
            ad_display = True
        elif ad == "nodisplay":
            ad_display = False
        else:
            respond_text(request, 400, "invalid ad\n", "text/plain; charset=utf-8", [])
            return
        with resolver.state_lock:
            resolver.state.ad_display = ad_display
        try:
            resolver.write_meta()
        except Exception:
            pass
        body = (f"switch success\nad=display\nredirect={AD_REDIRECT_URL}\n"
                if ad_display else "switch success\nad=nodisplay\n")
        respond_text(request, 200, body, "text/plain; charset=utf-8", [])
        return
    if path == "/4kfifa.m3u":
        if method not in ("GET", "HEAD"):
            respond_text(request, 405, "method not allowed\n",
                         "text/plain; charset=utf-8", [])
            return
        try:
            proxy_prefix = proxy_prefix_from_url(url)
        except YsptpError as err:
            respond_text(request, 400, f"{err}\n", "text/plain; charset=utf-8", [])
            return
        if method == "HEAD":
            body = ""
        else:
            origin = request_origin(request, int(resolver.args.port))
            body = build_4kfifa_playlist(origin, proxy_prefix)
        respond_text(request, 200, body, "application/vnd.apple.mpegurl; charset=utf-8",
                     [("Cache-Control", "no-cache, no-store, max-age=0"),
                      ("Access-Control-Allow-Origin", "*")])
        return
    if path in ("/proxy.ts", "/proxy"):
        if method != "GET":
            respond_text(request, 405, "method not allowed\n",
                         "text/plain; charset=utf-8", [])
            return
        with resolver.state_lock:
            ad_display = resolver.state.ad_display
        if ad_display:
            respond_redirect(request, AD_REDIRECT_URL)
            return
        respond_proxy_ts(resolver, request, url)
        return
    if method not in ("GET", "HEAD"):
        respond_text(request, 405, "method not allowed\n", "text/plain; charset=utf-8", [])
        return
    channel = normalize_channel(path)
    if channel_by_name(channel) is None:
        respond_redirect(request, DEFAULT_REDIRECT_URL)
        return
    try:
        proxy_prefix = proxy_prefix_from_url(url)
    except YsptpError as err:
        respond_text(request, 400, f"{err}\n", "text/plain; charset=utf-8", [])
        return
    with resolver.state_lock:
        ad_display = resolver.state.ad_display
    if ad_display:
        respond_redirect(request, AD_REDIRECT_URL)
        return
    try:
        entry = resolver.ensure_channel(channel)
        if method == "HEAD":
            body, content_type = "", "application/vnd.apple.mpegurl"
            playlist_probe = PlaylistCacheProbe(state="bypass:head", age_ms=None)
        else:
            proxy_origin = request_origin(request, int(resolver.args.port))
            body, content_type, playlist_probe = resolver.fetch_playlist(
                entry, proxy_origin, proxy_prefix)
    except YsptpError as err:
        respond_text(request, 502, f"{err}\n", "text/plain; charset=utf-8", [])
        return
    headers = [
        ("Cache-Control", "no-cache, no-store, max-age=0"),
        ("Access-Control-Allow-Origin", "*"),
        ("X-CCTV-Channel", entry.channel),
        ("X-CCTV-Rate", entry.rate),
        ("X-CCTV-Cache-Expires-In", str(max(0, int(entry.expires_at - now_f64())))),
        ("X-CCTV-Cache-State", "fresh" if entry.fresh(now_f64()) else "stale-while-refresh"),
        ("X-CCTV-Playlist-Cache", playlist_probe.state),
    ]
    if playlist_probe.age_ms is not None:
        headers.append(("X-CCTV-Playlist-Cache-Age-Ms", str(playlist_probe.age_ms)))
    respond_text(request, 200, body, content_type, headers)


def _read_request(sock: socket.socket):
    data = b""
    sock.settimeout(15)
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        data += chunk
        if len(data) > 65536:
            return None
    head = data.split(b"\r\n\r\n", 1)[0]
    lines = head.split(b"\r\n")
    if not lines:
        return None
    try:
        request_line = lines[0].decode("latin-1")
    except UnicodeDecodeError:
        return None
    parts = request_line.split(" ")
    if len(parts) != 3:
        return None
    method, target, _version = parts
    # tolerate absolute-form targets
    if target.startswith("http://") or target.startswith("https://"):
        try:
            sp = urllib.parse.urlsplit(target)
            target = sp.path or "/"
            if sp.query:
                target += "?" + sp.query
        except ValueError:
            return None
    headers = {}
    for ln in lines[1:]:
        if b":" not in ln:
            continue
        k, v = ln.split(b":", 1)
        kl = k.decode("latin-1", errors="replace").strip().lower()
        if kl and kl not in headers:
            headers[kl] = v.decode("latin-1", errors="replace").strip()
    return _ProxyRequest(method.upper(), target, headers, sock)


def _connection_reader(resolver: Resolver, conn: socket.socket):
    try:
        request = _read_request(conn)
    except Exception:
        request = None
    if request is None:
        try:
            conn.close()
        except OSError:
            pass
        return
    resolver.enqueue_http_request(request)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------



def resolve_paths(args: argparse.Namespace) -> None:
    d = binary_dir()
    if not args.meta_json.strip():
        args.meta_json = os.path.join(d, "proxy-cache-state-rs.json")
    if not args.device_json.strip():
        args.device_json = os.path.join(d, "device-state-rs.json")


# ---------------------------------------------------------------------------
# Ported unit tests (mirrors #[cfg(test)] mod tests in main-2.rs)
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_engine(args):
    """Device-registration engine server (was ysptp.main() minus arg parsing).

    Runs in-process on 127.0.0.0:8766 in a daemon thread; the main program
    talks to it over plain HTTP exactly like it did with the Rust binary.
    Pure stdlib: no third-party packages, no subprocess, no binary.
    """
    resolve_paths(args)
    resolver = Resolver(args)
    resolver.start_heartbeat()
    resolver.start_refresh_worker()
    resolver.start_http_workers()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((args.host, int(args.port)))
    except OSError as e:
        print(f"device engine: bind {args.host}:{args.port} failed: {e}",
              file=sys.stderr)
        return
    srv.listen(128)
    log_program_initializing()
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        t = threading.Thread(target=_connection_reader, args=(resolver, conn),
                             daemon=True)
        t.start()


def _engine_main():
    """Entry point for the in-process device-engine thread."""
    from types import SimpleNamespace
    here = os.path.dirname(os.path.abspath(__file__))
    args = SimpleNamespace(
        host='0.0.0.0',
        port=8766,
        timeout=15.0,
        insecure_tls=False,
        cache_ttl=600.0,
        stale_while_refresh_ttl=120.0,
        refresh_error_cooldown=30.0,
        playlist_cache_ttl=0.0,
        background_refresh_queue_limit=4,
        http_workers=64,
        http_queue_limit=20000,
        identity_reset_error_threshold=3,
        identity_reset_cooldown=300.0,
        refresh_interval=2.0,
        control_step_jitter_min_ms=0,
        control_step_jitter_max_ms=0,
        heartbeat_interval=30.0,
        heartbeat_ttl_guard=60.0,
        session_ttl=7200.0,
        meta_json=os.path.join(here, 'proxy-cache-state-rs.json'),
        device_json=os.path.join(here, 'device-state-rs.json'),
    )
    run_engine(args)


# ======================================================================
# ysp-live v5.1 main program (pure Python; merged from v4.0 native zip + ysptp.py)
# ======================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ysp-live v5.1: 央视频全频道直播代理（纯 Python，零二进制）

双协议:
- JCE PidTimeShift (jacc.ysp.cctv.cn): 主协议, 时移转直播
- bkliveinfo (bkliveinfo.ysp.cctv.cn + cKey): 备用, JCE 返回坏域名时自动切换

真4K: 纯 Python 设备注册引擎（gsnw 设备注册协议，零二进制），
有 live_id 的 26 路 (cctv1-5/5+/7-17/4k/8k/164k + cgtn 全系) 走设备协议
（码率更高，其中 4K 三路为真4K）；后端不在时回落。

播放器直连央视 CDN 拉分片, 本机只下发清单, 不跑视频流量（设备协议 5 路除外）。
仅标准库, 无第三方依赖。
"""

import argparse
import atexit
import base64
import gzip
import json
import os
import random
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ================================================================ JCE 协议

class W:
    def __init__(self): self.b = bytearray()
    def head(self, typ, tag):
        if tag < 15: self.b.append(((tag & 0xf) << 4) | (typ & 0xf))
        else: self.b.append(0xf0 | (typ & 0xf)); self.b.append(tag)
    def byte(self, v, tag):
        v = int(v)
        if v == 0: self.head(12, tag)
        else: self.head(0, tag); self.b += struct.pack('>b', v)
    def short(self, v, tag):
        v = int(v)
        if -128 <= v <= 127: self.byte(v, tag)
        else: self.head(1, tag); self.b += struct.pack('>h', v)
    def int(self, v, tag):
        v = int(v)
        if -32768 <= v <= 32767: self.short(v, tag)
        else: self.head(2, tag); self.b += struct.pack('>i', v)
    def long(self, v, tag):
        v = int(v)
        if -2147483648 <= v <= 2147483647: self.int(v, tag)
        else: self.head(3, tag); self.b += struct.pack('>q', v)
    def float(self, v, tag): self.head(4, tag); self.b += struct.pack('>f', float(v))
    def double(self, v, tag): self.head(5, tag); self.b += struct.pack('>d', float(v))
    def string(self, s, tag):
        if s is None: return
        data = str(s).encode('utf-8')
        if len(data) > 255: self.head(7, tag); self.b += struct.pack('>i', len(data)); self.b += data
        else: self.head(6, tag); self.b.append(len(data)); self.b += data
    def bytes(self, data, tag):
        data = bytes(data); self.head(13, tag); self.head(0, 0); self.int(len(data), 0); self.b += data
    def struct(self, fn, tag): self.head(10, tag); fn(self); self.head(11, 0)
    def list(self, items, tag, wf): self.head(9, tag); self.int(len(items), 0)
    def out(self): return bytes(self.b)


class R:
    def __init__(self, data): self.d = memoryview(data); self.p = 0
    def rem(self): return len(self.d) - self.p
    def get(self, n):
        if self.p + n > len(self.d): raise EOFError
        b = self.d[self.p:self.p + n].tobytes(); self.p += n; return b
    def u8(self): return self.get(1)[0]
    def head(self):
        b = self.u8(); typ = b & 0xf; tag = (b & 0xf0) >> 4
        if tag == 15: tag = self.u8()
        return typ, tag
    def value(self, typ):
        if typ == 0: return struct.unpack('>b', self.get(1))[0]
        if typ == 1: return struct.unpack('>h', self.get(2))[0]
        if typ == 2: return struct.unpack('>i', self.get(4))[0]
        if typ == 3: return struct.unpack('>q', self.get(8))[0]
        if typ == 4: return struct.unpack('>f', self.get(4))[0]
        if typ == 5: return struct.unpack('>d', self.get(8))[0]
        if typ == 6: n = self.u8(); return self.get(n).decode('utf-8', 'replace')
        if typ == 7: n = struct.unpack('>i', self.get(4))[0]; return self.get(n).decode('utf-8', 'replace')
        if typ == 8: n = self._int(); return {self._fv(): self._fv() for _ in range(n)}
        if typ == 9: n = self._int(); return [self._fv() for _ in range(n)]
        if typ == 10: return self.struct()
        if typ == 11: return None
        if typ == 12: return 0
        if typ == 13: t, _ = self.head(); n = self._int(); return self.get(n)
        raise ValueError('type %d' % typ)
    def _fv(self): t, _ = self.head(); return self.value(t)
    def _int(self): t, _ = self.head(); return int(self.value(t))
    def struct(self):
        m = {}
        while self.rem() > 0:
            t, tag = self.head()
            if t == 11: break
            m[tag] = self.value(t)
        return m


VER_NAME, VER_CODE = '3.2.7.26212', '302070'
APP_ID, QMF_APP_ID, QMF_PLATFORM, BIZ_ID = '1200013', 10012, 1, 0
CHAN_ID = '10070'
GUID = ''.join(random.choice('0123456789abcdef') for _ in range(32))


def _qua(w):
    w.string(VER_NAME, 0); w.string(VER_CODE, 1)
    w.int(1080, 2); w.int(2400, 3); w.int(3, 4); w.string('12', 5)
    w.int(1, 6); w.int(1, 7); w.int(420, 8); w.string(CHAN_ID, 9)
    for i in range(10, 15): w.string('', i)
    w.struct(lambda ww: (ww.int(0, 0), ww.byte(0, 1), ww.string('', 2)), 15)
    w.string('', 16); w.string('', 17); w.string('', 18)
    w.struct(lambda ww: (ww.int(0, 0), ww.float(0, 1), ww.float(0, 2), ww.double(0, 3)), 19)
    w.string(GUID[:16], 20); w.string('Pixel 6', 21)
    w.int(1, 22)
    for i in range(23, 27): w.int(0, i)
    w.string('', 27); w.string('', 28); w.string(GUID, 29)


def _head(w, cmd, reqid):
    w.int(reqid, 0); w.int(cmd, 1)
    w.struct(lambda ww: _qua(ww), 2)
    w.string(APP_ID, 3); w.string(GUID, 4)
    w.list([], 5, None); w.struct(lambda ww: None, 6)
    w.list([], 7, None)
    w.int(0, 8); w.int(0, 9); w.int(0, 10)


def _wrap(cmd, body, reqid):
    w = W()
    w.struct(lambda ww: _head(ww, cmd, reqid), 0)
    w.bytes(body, 1)
    reqcmd = w.out()
    inner = bytearray([38]) + struct.pack('>i', len(reqcmd) + 17) + bytes([1]) + b'\x00' * 10 + reqcmd + bytes([40])
    comp = gzip.compress(bytes(inner))
    out = bytearray([19]) + struct.pack('>i', 0) + struct.pack('>H', 2) + struct.pack('>H', 65281)
    out += struct.pack('>H', cmd) + struct.pack('>H', 0) + struct.pack('>q', reqid)
    out += struct.pack('>i', 531) + struct.pack('>i', QMF_APP_ID) + struct.pack('>q', BIZ_ID)
    g = GUID.encode()[:32]; out += g + b'\x00' * (32 - len(g))
    out += struct.pack('>b', QMF_PLATFORM) + struct.pack('>i', int(VER_CODE)) + b'\x00' * 6
    out += bytes([0]) + struct.pack('>H', 0) + struct.pack('>H', 0)
    out += struct.pack('>i', len(inner)) + comp + bytes([3])
    struct.pack_into('>i', out, 1, len(out))
    return bytes(out)


def _unwrap(data):
    if data[:1] != b'\x13' or len(data) < 90: return None
    flags = struct.unpack('>i', data[21:25])[0]
    payload = data[89:-1]
    if flags & 2: payload = gzip.decompress(payload)
    if payload[:1] != b'&' or payload[-1:] != b'(': return None
    rc = R(payload[16:-1]).struct()
    return rc.get(1) or b''


class DeadHostError(RuntimeError):
    pass


def jce_timeshift_url(pid, sid, start, end, stream='fhd'):
    w = W()
    w.string(pid, 0); w.string(sid, 1); w.long(start, 2); w.long(end, 3); w.string(stream, 4)
    body = w.out()
    CMD = 25312
    reqid = int(time.time() * 1000) & 0x7fffffff
    packet = _wrap(CMD, body, reqid)
    req = urllib.request.Request('https://jacc.ysp.cctv.cn', data=packet, method='POST')
    req.add_header('Content-Type', 'application/octet-stream')
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    resp_body = _unwrap(raw)
    if not resp_body: raise RuntimeError('bad response')
    m = R(resp_body).struct()
    err = m.get(0, 0)
    if err != 0: raise RuntimeError(m.get(1, 'errCode=%s' % err))
    url = m.get(2, '')
    if not url: raise RuntimeError('empty m3u8')
    if 'liverecord.video.cloud.cctv.com' in url:
        raise DeadHostError('dead cdn host')
    return url
    return url


# ================================================================ cKey + bkliveinfo
# 移植自 akiralereal/iptv extractors/yangshipin/ckey.js

_CK_PLATFORM = 4330403
_CK_APPVER = 'V8.22.1035.3031'
_CK_TEA = bytes.fromhex('59b2f7cf725ef43c34fdd7c123411ed3')
_CK_GTEA = bytes.fromhex('110DBEC10C23E7D2E56A1CAD6914EF1B')
_CK_XOR = bytes([0x84, 0x2e, 0xed, 0x08, 0xf0, 0x66, 0xe6, 0xea, 0x48, 0xb4, 0xca, 0xa9, 0x91, 0xed, 0x6f, 0xf3])
_CK_GXOR = bytes([0xb3, 0xc9, 0x53, 0xa0, 0x69, 0x13, 0xad, 0x4d])


def _u32(v): return v & 0xFFFFFFFF


def _tea_blk(blk, key):
    y, z = struct.unpack('>2I', blk)
    k = struct.unpack('>4I', key)
    s = 0
    for _ in range(16):
        s = _u32(s + 0x9e3779b9)
        y = _u32(y + _u32(_u32(_u32(z << 4) + k[0]) ^ _u32(z + s) ^ _u32((z >> 5) + k[1])))
        z = _u32(z + _u32(_u32(_u32(y << 4) + k[2]) ^ _u32(y + s) ^ _u32((y >> 5) + k[3])))
    return struct.pack('>2I', y, z)


def _cksum(buf):
    v = 0
    for b in buf: v = (0x83 * v + b) & 0x7fffffff
    return v


def _tea_pkt(data, key):
    pad = (8 - ((len(data) + 10) % 8)) % 8
    plain = bytes([(os.urandom(1)[0] & 0xf8) | pad]) + os.urandom(pad) + os.urandom(2) + data + bytes(7)
    out, pp, pc = b'', bytes(8), bytes(8)
    for off in range(0, len(plain), 8):
        mixed = bytes(a ^ b for a, b in zip(plain[off:off + 8], pc))
        enc = _tea_blk(mixed, key)
        cipher = bytes(a ^ b for a, b in zip(enc, pp))
        out += cipher
        pp, pc = mixed, cipher
    return out


def _lp(s):
    d = s.encode() if isinstance(s, str) else s
    return struct.pack('>H', len(d)) + d


def _ck_guard(ts, guid):
    def tail(v):
        t = str(v); return t[-5:] if len(t) >= 5 else ''
    body = struct.pack('>I', ts) + _lp(tail(guid)) + _lp(tail('null')) + _lp(tail('null')) + _lp('-1')
    plain = _lp(body)
    enc = _tea_pkt(plain, _CK_GTEA) + struct.pack('>I', _cksum(plain))
    enc = bytes(a ^ _CK_GXOR[i & 7] for i, a in enumerate(enc))
    return enc.hex().upper()


def _ckey(channel_id):
    ts = int(time.time())
    guid = os.urandom(16).hex()
    guard = _ck_guard(ts, guid)
    uid = os.urandom(4).hex().upper()
    body = (bytes.fromhex('0000004200000004000004d2') + struct.pack('>I', _CK_PLATFORM)
            + struct.pack('>I', 0) + struct.pack('>I', ts) + _lp('dcgh')
            + _lp('_zj1A5Gh6QYcxWjIUGos2w==') + _lp(_CK_APPVER) + _lp(str(channel_id))
            + _lp(guid) + struct.pack('>I', 1) + struct.pack('>I', 1) + _lp(uid) + _lp('nil')
            + _lp('57eab0c4-2c58-44c6-8ae9-dd2757525dc5') + _lp('nil') + _lp('v0.1.000')
            + _lp('com.cctv.yangshipin.app.iphone') + _lp(str(_CK_PLATFORM))
            + _lp('ex_json_bus') + _lp('ex_json_vs') + _lp(guard))
    pkt = bytearray(struct.pack('>H', len(body)) + body)
    pkt[18:22] = struct.pack('>I', _cksum(bytes(pkt)))
    pkt = bytes(pkt)
    enc = _tea_pkt(pkt, _CK_TEA) + struct.pack('>I', _cksum(pkt))
    enc = bytes(a ^ _CK_XOR[i & 15] for i, a in enumerate(enc))
    b64 = base64.b64encode(enc).decode().replace('+', '_').replace('/', '-').rstrip('=')
    return {'cKey': '--01' + b64, 'guid': guid, 'ts': ts,
            'flowId': '%s_%d' % (uuid.uuid4().hex.upper(), _CK_PLATFORM)}


_BK_H264 = base64.b64encode(b'H(30:1080,60:1080|30:1080,60:1080)').decode()


def bk_playurls(channel_id, live_pid, defn='fhd'):
    t = _ckey(channel_id)
    q = urllib.parse.urlencode({
        'atime': '120', 'livepid': live_pid, 'cnlid': channel_id,
        'appVer': _CK_APPVER, 'app_version': '300090', 'caplv': '1', 'cmd': '2',
        'defn': defn, 'device': 'iPhone', 'encryptVer': '4.2', 'getpreviewinfo': '0',
        'hevclv': '0', 'lang': 'zh-Hans_CN', 'livequeue': '0', 'logintype': '1',
        'nettype': '1', 'newnettype': '1', 'newplatform': str(_CK_PLATFORM),
        'platform': str(_CK_PLATFORM), 'sdtfrom': 'v3021', 'spacode': '23',
        'spaudio': '1', 'spdemuxer': '6', 'spdrm': '2', 'spdynamicrange': '1',
        'spflv': '1', 'spflvaudio': '1', 'sphdrfps': '60', 'sphttps': '1',
        'spvcode': _BK_H264, 'spvideo': '4', 'stream': '1', 'system': '1',
        'sysver': 'ios18.2.1', 'uhd_flag': '0', 'cKey': t['cKey'], 'guid': t['guid'],
        'fntick': str(t['ts']), 'flowid': t['flowId'], 'playbacktime': '0',
    })
    req = urllib.request.Request('https://bkliveinfo.ysp.cctv.cn/?' + q,
                                 headers={'User-Agent': 'qqlive', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        p = json.loads(r.read().decode())
    if int(p.get('iretcode', -1)) != 0:
        raise RuntimeError('iretcode=%s %s' % (p.get('iretcode'), p.get('errinfo', '')))
    urls = []
    if p.get('playurl'): urls.append(p['playurl'])
    bu = p.get('backurl_list') or p.get('backurlList') or p.get('backurl')
    if isinstance(bu, list):
        for it in bu: urls.append(it if isinstance(it, str) else (it.get('url') or it.get('playurl') or ''))
    elif isinstance(bu, str):
        urls += [x for x in re.split(r'[;,]', bu) if x.strip()]
    urls = [u for u in dict.fromkeys(urls) if u and '.cctv.' in u]
    if not urls: raise RuntimeError('no playurl')
    # bklive- 备用 CDN 更稳定, 优先用
    urls.sort(key=lambda u: (0 if 'bklive-' in u else 1, u))
    return urls


def fetch_abs_playlist(url, depth=0):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'qqlive', 'Referer': 'https://live.cctv.cn/',
        'Accept': 'application/vnd.apple.mpegurl,application/json,*/*'})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode('utf-8', 'replace')
        final = r.geturl()
    if depth < 2:
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith('#EXT-X-STREAM-INF'):
                for j in range(i + 1, len(lines)):
                    s = lines[j].strip()
                    if s and not s.startswith('#'):
                        return fetch_abs_playlist(urllib.parse.urljoin(final, s), depth + 1)
                break
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith('#'):
            out.append(urllib.parse.urljoin(final, s))
        else:
            out.append(ln)
    return '\n'.join(out)
    return '\n'.join(out)


# ================================================================ 频道表

CHANNELS = [
    ('cctv1', 'CCTV-1 综合', '2024078201', '600001859', 'fhd'),
    ('cctv2', 'CCTV-2 财经', '2024075401', '600001800', 'fhd'),
    ('cctv3', 'CCTV-3 综艺', '2024068501', '600001801', 'fhd'),
    ('cctv4', 'CCTV-4 中文国际', '2029797101', '600001814', 'fhd'),
    ('cctv5', 'CCTV-5 体育', '2024078401', '600001818', 'fhd'),
    ('cctv5p', 'CCTV-5+ 体育赛事', '2024078001', '600001817', 'fhd'),
    ('cctv6', 'CCTV-6 电影', '2013693901', '600108442', 'fhd'),
    ('cctv7', 'CCTV-7 国防军事', '2024072001', '600004092', 'fhd'),
    ('cctv8', 'CCTV-8 电视剧', '2029793001', '600001803', 'fhd'),
    ('cctv9', 'CCTV-9 纪录', '2024078601', '600004078', 'fhd'),
    ('cctv10', 'CCTV-10 科教', '2024078701', '600001805', 'fhd'),
    ('cctv11', 'CCTV-11 戏曲', '2027248701', '600001806', 'fhd'),
    ('cctv12', 'CCTV-12 社会与法', '2027248801', '600001807', 'fhd'),
    ('cctv13', 'CCTV-13 新闻', '2029797201', '600001811', 'fhd'),
    ('cctv14', 'CCTV-14 少儿', '2027248901', '600001809', 'fhd'),
    ('cctv15', 'CCTV-15 音乐', '2027249001', '600001815', 'fhd'),
    ('cctv16', 'CCTV-16 奥林匹克', '2027249101', '600098637', 'fhd'),
    ('cctv164k', 'CCTV-16 4K', '2027249301', '600099502', 'fhd'),
    ('cctv17', 'CCTV-17 农业农村', '2027249401', '600001810', 'fhd'),
    ('cctv4k', 'CCTV-4K 超高清', '2029810301', '600002264', 'fhd'),
    ('cctv8k', 'CCTV-8K 超高清', '2026774101', '600156816', 'fhd'),
    ('cgtn', 'CGTN', '2024181701', '600014550', 'fhd'),
    ('cgtnfr', 'CGTN 法语', '2024181801', '600084704', 'fhd'),
    ('cgtnru', 'CGTN 俄语', '2024181901', '600084758', 'fhd'),
    ('cgtnar', 'CGTN 阿拉伯语', '2024182001', '600084782', 'fhd'),
    ('cgtnes', 'CGTN 西班牙语', '2024182101', '600084744', 'fhd'),
    ('cgtndoc', 'CGTN 纪录', '2024182301', '600084781', 'fhd'),
    ('cctvfyjc', 'CCTV 风云剧场', '2025637103', '600099658', 'shd'),
    ('cctvdyjc', 'CCTV 第一剧场', '2026874203', '600099655', 'shd'),
    ('cctvhjjc', 'CCTV 怀旧剧场', '2026874303', '600099620', 'shd'),
    ('bjws', '北京卫视', '2024052703', '600002309', 'fhd'),
    ('jsws', '江苏卫视', '2024171103', '600002521', 'fhd'),
    ('dfws', '东方卫视', '2024054503', '600002483', 'fhd'),
    ('zjws', '浙江卫视', '2024054703', '600002520', 'fhd'),
    ('hnws', '湖南卫视', '2024054803', '600002475', 'fhd'),
    ('hbws', '湖北卫视', '2024171203', '600002508', 'fhd'),
    ('gdws', '广东卫视', '2024060903', '600002485', 'fhd'),
    ('gxws', '广西卫视', '2024060703', '600002509', 'fhd'),
    ('hljws', '黑龙江卫视', '2029797003', '600002498', 'fhd'),
    ('hainanws', '海南卫视', '2024055603', '600002506', 'fhd'),
    ('cqws', '重庆卫视', '2024061103', '600002531', 'fhd'),
    ('szws', '深圳卫视', '2024061303', '600002481', 'fhd'),
    ('scws', '四川卫视', '2024061403', '600002516', 'fhd'),
    ('henanws', '河南卫视', '2029797303', '600002525', 'fhd'),
    ('dnws', '东南卫视', '2024061503', '600002484', 'fhd'),
    ('gzws', '贵州卫视', '2024061603', '600002490', 'fhd'),
    ('jxws', '江西卫视', '2024061703', '600002503', 'fhd'),
    ('lnws', '辽宁卫视', '2024171303', '600002505', 'fhd'),
    ('ahws', '安徽卫视', '2024171403', '600002532', 'fhd'),
    ('hebws', '河北卫视', '2024171503', '600002493', 'fhd'),
    ('sdws', '山东卫视', '2029787903', '600002513', 'fhd'),
    ('tjws', '天津卫视', '2019927003', '600152137', 'fhd'),
    ('jlws', '吉林卫视', '2025561503', '600190405', 'fhd'),
    ('saxws', '陕西卫视', '2029795103', '600190400', 'fhd'),
    ('nxws', '宁夏卫视', '2025608503', '600190737', 'fhd'),
    ('nmgws', '内蒙古卫视', '2025561203', '600190401', 'fhd'),
    ('ynws', '云南卫视', '2025561303', '600190402', 'fhd'),
    ('shanxiws', '山西卫视', '2025560803', '600190407', 'fhd'),
    ('gsws', '甘肃卫视', '2025561703', '600190408', 'fhd'),
    ('qhws', '青海卫视', '2025559103', '600190406', 'fhd'),
    ('xizangws', '西藏卫视', '2025558003', '600190403', 'fhd'),
    ('xjws', '新疆卫视', '2019927403', '600152138', 'fhd'),
    ('cetv1', 'CETV-1', '2022823801', '600171827', 'fhd'),
    ('guoxue', '国学频道', '2029360403', '600213139', 'fhd'),
]

# tvg-id 对照（EPG 源：https://epg.112114.xyz/pp.xml.gz，2026-10-04 核对）
# cctv8k / cgtn / cetv1：该源无对应条目，不写 tvg-id
TVG_IDS = {
    'cctv1': 'CCTV1', 'cctv2': 'CCTV2', 'cctv3': 'CCTV3', 'cctv4': 'CCTV4',
    'cctv5': 'CCTV5', 'cctv5p': 'CCTV5+', 'cctv6': 'CCTV6', 'cctv7': 'CCTV7',
    'cctv8': 'CCTV8', 'cctv9': 'CCTV9', 'cctv10': 'CCTV10', 'cctv11': 'CCTV11',
    'cctv12': 'CCTV12', 'cctv13': 'CCTV13', 'cctv14': 'CCTV14', 'cctv15': 'CCTV15',
    'cctv16': 'CCTV16', 'cctv164k': 'CCTV16', 'cctv17': 'CCTV17',
    'cctv4k': 'CCTV4K',
    'cgtnfr': 'CGTN法语', 'cgtnru': 'CGTN俄语', 'cgtnar': 'CGTN阿语',
    'cgtnes': 'CGTN西语', 'cgtndoc': 'CGTN纪录',
    'cctvdyjc': 'CCTV第一剧场', 'cctvfyjc': 'CCTV风云剧场',
    'cctvhjjc': 'CCTV怀旧剧场',
    'bjws': '北京卫视', 'jsws': '江苏卫视',
    'dfws': '东方卫视', 'zjws': '浙江卫视',
    'hnws': '湖南卫视', 'hbws': '湖北卫视',
    'gdws': '广东卫视', 'gxws': '广西卫视',
    'hljws': '黑龙江卫视', 'hainanws': '海南卫视',
    'cqws': '重庆卫视', 'szws': '深圳卫视',
    'scws': '四川卫视', 'henanws': '河南卫视',
    'dnws': '东南卫视', 'gzws': '贵州卫视',
    'jxws': '江西卫视', 'lnws': '辽宁卫视',
    'ahws': '安徽卫视', 'hebws': '河北卫视',
    'sdws': '山东卫视', 'tjws': '天津卫视',
    'jlws': '吉林卫视', 'saxws': '陕西卫视',
    'nxws': '宁夏卫视', 'nmgws': '内蒙古卫视',
    'ynws': '云南卫视', 'shanxiws': '山西卫视',
    'qhws': '青海卫视', 'xizangws': '西藏卫视',
    'xjws': '新疆卫视',
    'gsws': '甘肃卫视',
    'guoxue': '国学',
}
LOGO_BASE = 'https://garysclub.sharewithyou.dpdns.org/logos/ysp-live-logos'


UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'

# ================================================================ 直播状态

REFRESH_INTERVAL = 10  # v2.7: 15->10, 清单勤刷, 分片 URL 保鲜 (借鉴 Rust 版主动刷新)
IDLE_TIMEOUT = 120
WINDOW = 300
MAX_SEGS = 60
BK_URL_TTL = 300  # v2.7: 600->300, cKey 地址提前主动重取, 不等过期失败再换


# ================================================================ 真4K 引擎
# 纯 Python 设备注册引擎 (gsnw 设备注册协议)，跑在进程内线程，零二进制。
# 启动一次即可：63 路 + 真4K 全有。引擎起不来时 4K 频道回落 1080p。

FOUR_K_PORT = 8766
# 有 gsnw live_id 的 26 路走设备协议（码率更高），其中 3 路是真4K
# cctv6 暂无 live_id，走 JCE
BACKEND_CHANNELS = {
    'cctv1', 'cctv2', 'cctv3', 'cctv4', 'cctv5', 'cctv5p',
    'cctv7', 'cctv8', 'cctv9', 'cctv10', 'cctv11', 'cctv12',
    'cctv13', 'cctv14', 'cctv15', 'cctv16', 'cctv17',
    'cctv4k', 'cctv8k', 'cctv164k',
    'cgtn', 'cgtnfr', 'cgtnru', 'cgtnar', 'cgtnes', 'cgtndoc',
}
TRUE_4K_CHANNELS = {'cctv4k', 'cctv8k', 'cctv164k'}
_four_k_url = 'http://127.0.0.1:%d' % FOUR_K_PORT
_four_k_thread = None
_four_k_ready = False
_four_k_lock = threading.Lock()


def four_k_ready():
    with _four_k_lock:
        if not _four_k_ready:
            return False
        # 线程死了也算未就绪（看门狗会负责重启）
        th = _four_k_thread
        return th is not None and th.is_alive()


def _four_k_watchdog():
    """看门狗：引擎线程挂了就重启，保证设备协议不休眠。"""
    global _four_k_ready
    while True:
        time.sleep(60)
        with _four_k_lock:
            th = _four_k_thread
            ready = _four_k_ready
        alive = th is not None and th.is_alive()
        http_ok = False
        if alive:
            try:
                req = urllib.request.Request(_four_k_url + '/4kfifa.m3u', method='HEAD')
                with urllib.request.urlopen(req, timeout=5) as r:
                    http_ok = (r.status == 200)
            except Exception:
                http_ok = False
        if not (alive and http_ok):
            if ready:
                log('4K 引擎异常，正在重启…')
            with _four_k_lock:
                _four_k_ready = False
            # warmup 是阻塞的，开线程做，避免卡住看门狗
            threading.Thread(target=_four_k_warmup, daemon=True).start()
            # 等重启完成再继续下一轮检测
            time.sleep(90)


def _four_k_warmup():
    """启动纯 Python 设备引擎线程并预热，失败则保持 1080p 回落。"""
    global _four_k_thread, _four_k_ready
    with _four_k_lock:
        th = _four_k_thread
        if th is not None and th.is_alive():
            return  # 已在运行
        _four_k_thread = threading.Thread(target=_engine_main,
                                          name='device-engine', daemon=True)
        _four_k_thread.start()
    log('4K 引擎启动中… (纯 Python，零二进制；首次设备注册约半分钟)')
    up = False
    for _ in range(120):
        with _four_k_lock:
            th = _four_k_thread
        if th is None or not th.is_alive():
            log('4K 引擎线程异常退出，4K 频道走 1080p')
            with _four_k_lock:
                _four_k_thread = None
            return
        try:
            req = urllib.request.Request(_four_k_url + '/4kfifa.m3u', method='HEAD')
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if not up:
        log('4K 引擎启动超时，4K 频道走 1080p')
        return
    # 预热一路真4K（触发设备注册），成功才切换
    try:
        req = urllib.request.Request(_four_k_url + '/cctv4k.m3u8',
                                     headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode('utf-8', 'replace')
        if '#EXTM3U' in body and 'proxy.ts' in body:
            with _four_k_lock:
                _four_k_ready = True
            log('设备协议就绪：26 路已切换高码率（4K 三路为真4K）')
        else:
            log('4K 引擎清单异常，4K 频道走 1080p')
    except Exception as e:
        log('4K 预热失败 (%s)，4K 频道走 1080p' % e)


def _stop_four_k():
    # 纯 Python 版：引擎是 daemon 线程，随主进程退出，无需单独停止
    pass


def fetch_four_k_playlist(slug, host_hdr):
    """从 4K 后端取清单；失败返回 None（调用方回落 1080p）。"""
    try:
        req = urllib.request.Request(_four_k_url + '/%s.m3u8' % slug,
                                     headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode('utf-8', 'replace')
        if '#EXTM3U' not in body:
            return None
        # 局域网客户端：分片地址从 127.0.0.1 换成播放器访问的主机
        if host_hdr:
            host = host_hdr.split(':')[0].strip()
            if host and host not in ('127.0.0.1', 'localhost'):
                body = body.replace('http://127.0.0.1:%d/' % FOUR_K_PORT,
                                    'http://%s:%d/' % (host, FOUR_K_PORT))
        return body
    except Exception as e:
        log('4K %s 拉取失败: %s，回落 1080p' % (slug, e))
        return None


class Channel:
    def __init__(self, slug, name, sid, pid, defn):
        self.slug, self.name, self.sid, self.pid, self.defn = slug, name, sid, pid, defn
        self.lock = threading.Lock()
        self.segments = {}
        self.order = deque()
        self.seq = 0
        self.last_access = 0.0
        self.thread = None
        self.last_error = ''
        self.last_ok = 0.0  # v2.7: 上次刷新成功时间, /diag 可见
        self.mode = 'jce'
        self.bk_urls = []
        self.bk_urls_time = 0.0
        self.bk_playlist = ''
        self._starting = False


def seg_key(url, pdt):
    if pdt: return 'pdt:' + pdt
    p = urllib.parse.urlsplit(url)
    return p.scheme + '://' + p.netloc + p.path


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


def jce_fetch(ch):
    now = int(time.time())
    m3u8_url = jce_timeshift_url(ch.pid, ch.sid, now - WINDOW, now, ch.defn)
    req = urllib.request.Request(m3u8_url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode('utf-8', 'replace')
    segs, dur, pdt = [], 6.0, ''
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            try: dur = float(line[len('#EXTINF:'):].split(',')[0])
            except ValueError: dur = 6.0
        elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            pdt = line[len('#EXT-X-PROGRAM-DATE-TIME:'):]
        elif line and not line.startswith('#'):
            segs.append((dur, pdt, urllib.parse.urljoin(m3u8_url, line)))
            pdt = ''
    if not segs: raise RuntimeError('empty playlist')
    return segs


def jce_refresh(ch):
    segs = jce_fetch(ch)
    with ch.lock:
        added = 0
        for dur, pdt, url in segs:
            key = seg_key(url, pdt)
            if key in ch.segments:
                ch.segments[key][3] = url
                continue
            ch.seq += 1
            ch.segments[key] = [ch.seq, dur, pdt, url]
            ch.order.append(key)
            added += 1
        while len(ch.order) > MAX_SEGS:
            ch.segments.pop(ch.order.popleft(), None)
        ch.last_error = ''
    if added: log('%s +%d 片 (共%d)' % (ch.slug, added, len(ch.segments)))
    return True


def bk_refresh(ch):
    now = time.time()
    if now - ch.bk_urls_time > BK_URL_TTL or not ch.bk_urls:
        ch.bk_urls = bk_playurls(ch.sid, ch.pid, ch.defn)
        ch.bk_urls_time = now
        log('%s bkliveinfo 拿到 %d 个地址' % (ch.slug, len(ch.bk_urls)))
    last_err = ''
    for attempt in range(2):
        for u in ch.bk_urls:
            try:
                pl = fetch_abs_playlist(u)
                if '#EXTM3U' not in pl: continue
                with ch.lock:
                    ch.bk_playlist = pl
                    ch.last_error = ''
                return True
            except urllib.error.HTTPError as e:
                last_err = 'HTTPError: HTTP %s' % e.code
                if e.code == 403:
                    time.sleep(2)
                continue
            except Exception as e:
                last_err = '%s: %s' % (type(e).__name__, e)
        if attempt == 0:
            try:
                ch.bk_urls = bk_playurls(ch.sid, ch.pid, ch.defn)
                ch.bk_urls_time = time.time()
                log('%s 地址疑似过期, 已重取' % ch.slug)
            except Exception:
                pass
    ch.bk_urls_time = 0
    raise RuntimeError(last_err[:120] or 'bk playlist failed')


def refresh_once(ch):
    try:
        if ch.mode == 'bk':
            ok = bk_refresh(ch)
        else:
            try:
                ok = jce_refresh(ch)
            except DeadHostError:
                ch.mode = 'bk'
                log('%s JCE 返回坏域名, 切换 bkliveinfo' % ch.slug)
                ok = bk_refresh(ch)
        if ok:
            ch.last_ok = time.time()
        return ok
    except Exception as e:
        ch.last_error = ('%s: %s' % (type(e).__name__, e))[:120]
        log('%s 刷新失败: %s' % (ch.slug, ch.last_error))
        return False


def refresh_loop(ch):
    log('%s 后台刷新启动 [%s]' % (ch.slug, ch.mode))
    fails = 0
    while time.time() - ch.last_access < IDLE_TIMEOUT:
        ok = refresh_once(ch)
        fails = 0 if ok else fails + 1
        time.sleep(REFRESH_INTERVAL if fails < 3 else 30)  # v2.7: 60->30, 失败后恢复更快
    log('%s 无人观看, 停止刷新' % ch.slug)


def ensure_channel(ch):
    ch.last_access = time.time()
    with ch.lock:
        if ch._starting:
            return
        need_fetch = not ch.segments and not ch.bk_playlist
        need_thread = ch.thread is None or not ch.thread.is_alive()
        if need_fetch or need_thread:
            ch._starting = True
        else:
            return
    try:
        if need_fetch:
            refresh_once(ch)
        if need_thread:
            ch.thread = threading.Thread(target=refresh_loop, args=(ch,), daemon=True)
            ch.thread.start()
    finally:
        with ch.lock:
            ch._starting = False


def build_playlist(ch):
    with ch.lock:
        if ch.mode == 'bk':
            return ch.bk_playlist or None
        keys = list(ch.order)[-15:]  # v2.7: 30->15, 窗口收窄贴近直播沿, URL 更保鲜
        segs = [ch.segments[k] for k in keys if k in ch.segments]
    if not segs: return None
    target = max(6, max(int(s[1] + 0.5) for s in segs))
    out = ['#EXTM3U', '#EXT-X-VERSION:3',
           '#EXT-X-TARGETDURATION:%d' % target,
           '#EXT-X-MEDIA-SEQUENCE:%d' % segs[0][0]]
    for _, dur, pdt, url in segs:
        if pdt: out.append('#EXT-X-PROGRAM-DATE-TIME:' + pdt)
        out.append('#EXTINF:%.3f,' % dur)
        out.append(url)
    return '\n'.join(out) + '\n'


CHANNEL_MAP = {c[0]: Channel(*c) for c in CHANNELS}

# 已知 JCE 返回坏域名的频道, 直接走 bkliveinfo, 省掉首次切换等待
FORCE_BK = {'cctv11', 'cctv12', 'cctv14', 'cctv15', 'cctv16', 'cctv164k',
            'cctv17', 'cctv4k', 'cctvfyjc', 'cctvdyjc', 'cctvhjjc'}
for _s in FORCE_BK:
    if _s in CHANNEL_MAP:
        CHANNEL_MAP[_s].mode = 'bk'

# ================================================================ HTTP 服务


class Handler(BaseHTTPRequestHandler):
    server_version = 'ysp-live/5.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='text/plain; charset=utf-8'):
        data = body.encode('utf-8') if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # 播放器提前断开（如切台、超时），静默丢弃
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/', '/index.html'):
            self._send(200, index_page(), 'text/html; charset=utf-8')
            return
        if path == '/health':
            self._send(200, 'ok')
            return
        if path == '/all.m3u':
            host = self.headers.get('Host', 'localhost:8767')
            lines = ['#EXTM3U url-tvg="https://epg.112114.xyz/pp.xml.gz"']
            for slug, name, _s, _p, _d in CHANNELS:
                attrs = ''
                tid = TVG_IDS.get(slug)
                if tid:
                    attrs += ' tvg-id="%s"' % tid
                attrs += ' tvg-logo="%s/%s.png"' % (LOGO_BASE, slug)
                lines.append('#EXTINF:-1%s tvg-name="%s",%s' % (attrs, name, name))
                lines.append('http://%s/%s.m3u8' % (host, slug))
            self._send(200, '\n'.join(lines) + '\n', 'application/vnd.apple.mpegurl')
            return
        if path == '/diag':
            info = []
            now = time.time()
            info.append('4K后端: %s' % ('就绪' if four_k_ready() else '未就绪(1080p回落)'))
            for slug, ch in CHANNEL_MAP.items():
                age = ('%ds前' % int(now - ch.last_ok)) if ch.last_ok else '从未成功'
                info.append('%s mode=%s 最后成功=%s err=%s' % (slug, ch.mode, age, ch.last_error))
            self._send(200, '\n'.join(info) + '\n')
            return
        m = re.match(r'^/([\w]+)\.m3u8$', path)
        if m:
            slug = m.group(1)
            ch = CHANNEL_MAP.get(slug)
            if not ch:
                self._send(404, '未知频道\n')
                return
            # 设备协议优先（26 路有 live_id，码率更高）：只有当设备协议失效时才回落 1080p
            if slug in BACKEND_CHANNELS and four_k_ready():
                pl = fetch_four_k_playlist(slug, self.headers.get('Host', ''))
                if pl:
                    self._send(200, pl, 'application/vnd.apple.mpegurl')
                    return
            ensure_channel(ch)
            pl = build_playlist(ch)
            if not pl:
                self._send(503, '频道 %s 暂无数据 (%s), 请稍后重试\n' % (ch.name, ch.last_error or '拉取中'))
                return
            self._send(200, pl, 'application/vnd.apple.mpegurl')
            return
        self._send(404, 'not found\n')


def index_page():
    items = []
    k4 = four_k_ready()
    for slug, name, _s, _p, _d in CHANNELS:
        tag = ''
        if slug in TRUE_4K_CHANNELS:
            tag = ' <b style="color:#c00">[真4K]</b>' if k4 else ' <span style="color:#888">[4K准备中]</span>'
        elif slug in BACKEND_CHANNELS:
            tag = ' <b style="color:#c00">[高码率]</b>' if k4 else ' <span style="color:#888">[准备中]</span>'
        items.append('<li><a href="/%s.m3u8">%s</a>%s <span>/%s.m3u8</span></li>' % (slug, name, tag, slug))
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>央视频全频道直播</title><style>'
            'body{font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:720px;margin:0 auto;padding:20px;}'
            'li{margin:6px 0;}span{color:#888;font-size:12px;margin-left:8px;}</style></head>'
            '<body><h2>央视频全频道直播 (%d 路%s)</h2>'
            '<p>把链接粘贴到播放器即可观看, 延迟约 20 秒。分片由播放器直连央视 CDN, 本机不跑视频流量（26 路设备协议频道走本地后端中继）。</p>'
            '<p>聚合订阅: <a href="/all.m3u">/all.m3u</a> (64 路一次导入)</p>'
            '<ul>%s</ul></body></html>' % (len(items), '，含真4K' if k4 else '', ''.join(items)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('port', nargs='?', type=int, default=8767)
    ap.add_argument('--no-4k', action='store_true', help='不启动 4K 后端')
    args = ap.parse_args()
    if not args.no_4k:
        threading.Thread(target=_four_k_warmup, daemon=True).start()
        threading.Thread(target=_four_k_watchdog, daemon=True).start()
    atexit.register(_stop_four_k)
    srv = ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
    log('ysp-live v5.1 启动: %d 个频道, 监听端口 %d' % (len(CHANNEL_MAP), args.port))
    log('首页: http://localhost:%d/' % args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
