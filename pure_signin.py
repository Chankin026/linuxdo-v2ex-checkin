#!/usr/bin/env python3
"""
小黑盒 (Xiaoheihe) 纯算法签到脚本
只需提供一个 Cookie 即可完成签到。

用法:
    python pure_signin.py "pkey=...; x_xhh_tokenid=..."

环境变量 (可选):
    XIAOHEIHE_COOKIE    签到 Cookie（命令行参数优先）
"""

import base64
import hashlib
import hmac
import re
import os
import sys
import time
from typing import Dict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from curl_cffi import requests

# ── 常量 ────────────────────────────────────────────────
API_BASE = "https://api.xiaoheihe.cn"
SIGN_STATE_PATH = "/task/sign_v3/get_sign_state"
SIGN_PATH = "/task/sign_v3/sign"

DEFAULT_ANDROID_ID = "493245af067c9e43"
DEFAULT_OS_VERSION = "12"
DEFAULT_VERSION_NAME = "1.3.388"
DEFAULT_BUILD = "837"
UINT32_MASK = (1 << 32) - 1
UINT64_MASK = (1 << 64) - 1

_CIPHER = b"gAAAAABqCWTXgttlWdvxF8DBZA7pQlY_fK1SJPnkExLeuI7IfBM4RkKGNUlZPqarmCbHN7VBdA1c2PQft_yfFEbhRBIp43QfWGNnIoYrkw6sx7Ft6W1EQcj7l5rq-GcEAUmmQpFMNk-_smJATqF70Ilvj6F-uijz6TWpwKBFy8KFXrBkn10248i2SdZzTLjGJZOtFaiN9pbtAdTQ9x6DVPElkFFru-d8SxYRNhZ6fogqyAFbb2ykJSs_pC-4NMQA0bPlo-U63adV9kTFvO5erZwz4ciYoXwl6RLMPfJiHGiWnn5qR2WbdLwu0hY4wVotFIZRX_II36dcipRMLQniVvDXpSruSSArAjO8i_Dk1yFmShWQlxutk8x3v93pwK4JexxsyyYwbmocs2dwoa8yc7DdBr5ixQnYqYkFkJ1iFZlc4GC2PFth9plcNHGkbE9YBiYXUEZrZqXOe58xeDl5auZ1h7mCay6tPfNT63rd6e9nGHVoSJytENSv-ioOD3fqmp35MNdBLsx4-sNdJ_k3u2mQaUWPju1Lzn9pmfCEWCgRkss-I5atww52lxf5ob4J2emlw58OkB9a3eial3nI8SvSL9W0f_otodqtVEDIfo0a1XSCqBi0BpErx4zHtDlegdUhfKH-Ngjm0M5aZd85E0lMQLtfGw-NEdvHCfZNO6HfVOua1rJ36G1KRdZVE-AXpbuD1iWY6Ee-a_d7nayFJBDIz28URloYwhD5_rAzdx_wpCVYcfC5Jz_G793ZyNlCZ3B-AGP6WmLsPGTIyU0vussWjf0Zgr2478xjRsNi1T-fNrPl78URz4WnPDgoTBvdj7d78qYGALaA012GITkzEsPrV6WlqgJXHXthn3VY1ZqFLISUJNFt0YANVdu9lEX-CJQrlR5rxKrj6DvT5KtcSMJ8p6AF-ZK4fSQ78oTUZY6ZgRlMmL0IPuiJumM4zkPA119HB5CczU5vD9B_it5pbHIFSMOSMbVFw7sef9p1Wqhb77ZeubJJKu9GTkHS8M9bKsnnna4qnMW-rVDRRJcwZoZ2rOXQ9gr5ieRgpDlEtGuqhRF1O4-Wig8="


def _decrypt_constants():
    import json
    from cryptography.fernet import Fernet

    password = os.environ.get("XIAOHEIHE_KEY", "").strip()
    if not password:
        raise SystemExit("XIAOHEIHE_KEY is not configured in env file")
    key_bytes = hashlib.sha256(password.encode()).digest()
    f = Fernet(base64.urlsafe_b64encode(key_bytes))
    return json.loads(f.decrypt(_CIPHER))


_c = _decrypt_constants()
HMAC_KEY = _c["HMAC_KEY"].encode()
FALSE_CRC32_POLY_REFLECTED = _c["FALSE_CRC32_POLY_REFLECTED"]
FALSE_CRC32_INIT = _c["FALSE_CRC32_INIT"]
FALSE_CRC32_XOROUT = _c["FALSE_CRC32_XOROUT"]
STATE_BLOCK_LEN = _c["STATE_BLOCK_LEN"]
TRUE_CRC32_POLY_REFLECTED = _c["TRUE_CRC32_POLY_REFLECTED"]
TRUE_CRC32_INIT = _c["TRUE_CRC32_INIT"]
TRUE_CRC32_XOROUT = _c["TRUE_CRC32_XOROUT"]
_NATIVE_ROUND_TABLE = bytes.fromhex(_c["NATIVE_ROUND_TABLE_HEX"])
BASE62 = _c["BASE62"].encode()
IDX_SEED_BASE = _c["IDX_SEED_BASE"]
PURE_IDX_G_TABLE = _c["PURE_IDX_G_TABLE"]
PURE_CHUNK = _c["PURE_CHUNK"]

_RND_K = (
    0x428A2F98D728AE22, 0x7137449123EF65CD, 0xB5C0FBCFEC4D3B2F, 0xE9B5DBA58189DBBC,
    0x3956C25BF348B538, 0x59F111F1B605D019, 0x923F82A4AF194F9B, 0xAB1C5ED5DA6D8118,
    0xD807AA98A3030242, 0x12835B0145706FBE, 0x243185BE4EE4B28C, 0x550C7DC3D5FFB4E2,
    0x72BE5D74F27B896F, 0x80DEB1FE3B1696B1, 0x9BDC06A725C71235, 0xC19BF174CF692694,
    0xE49B69C19EF14AD2, 0xEFBE4786384F25E3, 0x0FC19DC68B8CD5B5, 0x240CA1CC77AC9C65,
    0x2DE92C6F592B0275, 0x4A7484AA6EA6E483, 0x5CB0A9DCBD41FBD4, 0x76F988DA831153B5,
    0x983E5152EE66DFAB, 0xA831C66D2DB43210, 0xB00327C898FB213F, 0xBF597FC7BEEF0EE4,
    0xC6E00BF33DA88FC2, 0xD5A79147930AA725, 0x06CA6351E003826F, 0x142929670A0E6E70,
    0x27B70A8546D22FFC, 0x2E1B21385C26C926, 0x4D2C6DFC5AC42AED, 0x53380D139D95B3DF,
    0x650A73548BAF63DE, 0x766A0ABB3C77B2A8, 0x81C2C92E47EDAEE6, 0x92722C851482353B,
    0xA2BFE8A14CF10364, 0xA81A664BBC423001, 0xC24B8B70D0F89791, 0xC76C51A30654BE30,
    0xD192E819D6EF5218, 0xD69906245565A910, 0xF40E35855771202A, 0x106AA07032BBD1B8,
    0x19A4C116B8D2D0C8, 0x1E376C085141AB53, 0x2748774CDF8EEB99, 0x34B0BCB5E19B48A8,
    0x391C0CB3C5C95A63, 0x4ED8AA4AE3418ACB, 0x5B9CCA4F7763E373, 0x682E6FF3D6B2B8A3,
    0x748F82EE5DEFB2FC, 0x78A5636F43172F60, 0x84C87814A1F0AB72, 0x8CC702081A6439EC,
    0x7BA0EA2D98160007, 0x7EABF2D0C21F964A, 0x8DBE8D038B409545, 0x90BB1721582E8285,
    0x99A2AD45936D4E61, 0x9F86E289FE03E73A, 0xA84C4472FAA9A82F, 0xB3DF34FCE89E0532,
    0xB99BB8D7B173534F, 0xBC76CBAB1AEA1F9C, 0xC226A69A780F3CC3, 0xD304F19AA233957D,
    0xDE1BE20A212129DD, 0xE39BB43755141950, 0xEE84927CEA48DDD2, 0xF3EDD2773C523B67,
    0xFBFDFE53A8D32F2A, 0x0BEE2C7AB77E9E25, 0x0E90181CF1B09E56, 0x25F57204C725BED8,
)

_RND_IV = (
    0x6A09E667F3BCC908,
    0xBB67AE8584CAA73B,
    0x3C6EF372FE94F82B,
    0xA54FF53A5F1D36F1,
    0x510E527FADE682D1,
    0x9B05688C2B3E6C1F,
    0x1F83D9ABFB41BD6B,
    0x5BE0CD19137E2179,
)

# ── 工具函数 ──────────────────────────────────────────────

def u32(value: int) -> int:
    return value & UINT32_MASK


def u64(value: int) -> int:
    return value & UINT64_MASK


def rotr64(value: int, bits: int) -> int:
    return u64((value >> bits) | (value << (64 - bits)))


def pad_base64(value: str) -> str:
    s = value.strip()
    return s + ("=" * ((4 - len(s) % 4) % 4))


def now_timestamp() -> str:
    return str(int(time.time()))


# ── Cookie 解析 ────────────────────────────────────────────

def parse_cookie(cookie_text: str) -> Dict[str, str]:
    text = cookie_text.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    cookies: Dict[str, str] = {}
    for fragment in text.split(";"):
        item = fragment.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        if k:
            cookies[k] = v.strip()
    if not cookies:
        raise SystemExit("错误: Cookie 格式无效")
    return cookies


def decode_pkey_text(pkey: str) -> str:
    for candidate in (pkey, pkey.replace("-", "+").replace("_", "/")):
        try:
            raw = base64.b64decode(pad_base64(candidate))
        except Exception:
            continue
        decoded = raw.decode("utf-8", errors="ignore").strip()
        if decoded:
            return decoded
    return ""


def derive_heybox_id(pkey: str, cookies: Dict[str, str]) -> str:
    for key in ("heybox_id", "x_heybox_id"):
        value = str(cookies.get(key, "")).strip()
        if value:
            return value
    decoded = decode_pkey_text(pkey)
    for pattern in (r"_(\d{5,})[A-Za-z]+$", r"_(\d{5,})(?:\D|$)", r"\.(\d{5,})[A-Za-z]+$"):
        match = re.search(pattern, decoded)
        if match:
            return match.group(1)
    long_numbers = re.findall(r"\d{5,}", decoded)
    if long_numbers:
        return long_numbers[-1]
    fallback = re.findall(r"\d{5,}", pkey)
    if fallback:
        return fallback[-1]
    raise SystemExit("错误: 无法从 Cookie 中提取 heybox_id")


# ── URL 拼接 ───────────────────────────────────────────────

def merge_query_params(url: str, extra_params: dict) -> str:
    if not extra_params:
        return url
    parts = urlsplit(url)
    current = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in extra_params.items():
        if v is not None:
            current[str(k)] = str(v)
    query = urlencode(list(current.items()))
    suffix = f"?{query}" if query else ""
    return f"{parts.scheme}://{parts.netloc}{parts.path}{suffix}"


def ensure_trailing_slash(path: str) -> str:
    p = path.strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    if not p.endswith("/"):
        p += "/"
    return p


# ── idx / chunk 算法 ────────────────────────────────────────

def compute_idx_seed(request_time: str) -> int:
    """C struct tm semantics: tm_year = years since 1900, tm_mon = 0-11."""
    ts = int(request_time)
    tm = time.gmtime(ts)
    c_year = tm.tm_year - 1900
    c_mon = tm.tm_mon - 1
    return c_year * 10000 + c_mon * 100 + tm.tm_mday + IDX_SEED_BASE


def build_idx(request_time: str) -> str:
    seed = compute_idx_seed(request_time)
    chars = []
    for g in PURE_IDX_G_TABLE:
        chars.append(chr(BASE62[(g + seed) % 62]))
    return "".join(chars)


def build_chunk() -> str:
    return PURE_CHUNK


# ── hkey / _rnd 算法 ────────────────────────────────────────

def build_seed_text(request_path: str, request_time: str, heybox_id: str, android_id: str) -> str:
    return ensure_trailing_slash(request_path) + str(request_time) + android_id + heybox_id


def _ee2(value: str) -> bytes:
    out = []
    for index, ch in enumerate(value.encode("utf-8")):
        a = (ch & 0x7F) ^ 0x37
        b = ((ch & 0x07) << 4) ^ 0x37
        out.append(b if a == 0 or index % 3 == 2 else a)
    return bytes(out)


def _ee3(value: str) -> bytes:
    buf = bytearray(_ee2(value))
    for index, ch in enumerate(list(buf)):
        a = (ch & 0x7F) ^ 0x46
        b = ((ch & 0x1F) << 2) ^ 0x46
        buf[index] = b if a == 0 or index % 5 == 4 else a
    return bytes(buf)


def _crc32(data: bytes, poly: int, init: int, xorout: int) -> int:
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
            crc &= UINT32_MASK
    return u32(crc ^ xorout)


def _rnd_ch(x: int, y: int, z: int) -> int:
    return u64(y ^ (x & (y ^ z)))


def _rnd_maj(x: int, y: int, z: int) -> int:
    return u64((x & (y | z)) | (y & z))


def _rnd_big_s0(x: int) -> int:
    return rotr64(x, 21) ^ rotr64(x, 31) ^ rotr64(x, 39)


def _rnd_big_s1(x: int) -> int:
    return rotr64(x, 14) ^ rotr64(x, 18) ^ rotr64(x, 41)


def _rnd_small_s0(x: int) -> int:
    return rotr64(x, 1) ^ rotr64(x, 8) ^ (x >> 7)


def _rnd_small_s1(x: int) -> int:
    return rotr64(x, 38) ^ rotr64(x, 55) ^ (x >> 6)


def _rnd_sha512(data: bytes) -> bytes:
    msg = bytearray(data)
    bit_len = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 128 != 112:
        msg.append(0)
    msg.extend((0).to_bytes(8, "big"))
    msg.extend(bit_len.to_bytes(8, "big"))

    state = list(_RND_IV)
    for offset in range(0, len(msg), 128):
        block = msg[offset : offset + 128]
        words = [int.from_bytes(block[i * 8 : i * 8 + 8], "big") for i in range(16)]
        for i in range(16, 80):
            words.append(
                u64(
                    _rnd_small_s1(words[i - 2])
                    + words[i - 7]
                    + _rnd_small_s0(words[i - 15])
                    + words[i - 16]
                )
            )

        work = state.copy()
        for i in range(80):
            a_idx = (-i) & 7
            b_idx = (a_idx + 1) & 7
            c_idx = (a_idx + 2) & 7
            d_idx = (a_idx + 3) & 7
            e_idx = (a_idx + 4) & 7
            f_idx = (a_idx + 5) & 7
            g_idx = (a_idx + 6) & 7
            h_idx = (a_idx + 7) & 7

            t1 = u64(
                work[h_idx]
                + _rnd_big_s1(work[e_idx])
                + _rnd_ch(work[e_idx], work[f_idx], work[g_idx])
                + _RND_K[i]
                + words[i]
            )
            t2 = u64(_rnd_big_s0(work[a_idx]) + _rnd_maj(work[a_idx], work[b_idx], work[c_idx]))
            work[d_idx] = u64(work[d_idx] + t1)
            work[h_idx] = u64(t1 + t2)

        state = [u64(current + delta) for current, delta in zip(state, work)]

    return b"".join(value.to_bytes(8, "big") for value in state)


def _rnd_hmac_sha512(data: bytes, key: bytes) -> bytes:
    block_len = 128
    if len(key) > block_len:
        key = _rnd_sha512(key)
    inner_pad = bytes((key[i] ^ 0x52) if i < len(key) else 0x52 for i in range(block_len))
    outer_pad = bytes((key[i] ^ 0x6C) if i < len(key) else 0x6C for i in range(block_len))
    inner = _rnd_sha512(inner_pad + data)
    return _rnd_sha512(outer_pad + inner)


def build_hkey(request_path: str, request_time: str, heybox_id: str, android_id: str) -> str:
    seed_text = build_seed_text(request_path, request_time, heybox_id, android_id).encode("utf-8")
    state_block = hmac.new(HMAC_KEY, seed_text, hashlib.sha512).digest()
    if len(state_block) != STATE_BLOCK_LEN:
        raise SystemExit(f"内部错误: state block 长度异常 ({len(state_block)})")
    crc = _crc32(state_block, FALSE_CRC32_POLY_REFLECTED, FALSE_CRC32_INIT, FALSE_CRC32_XOROUT)
    return f"{crc:X}"


def build_rnd(
    request_path: str,
    request_time: str,
    heybox_id: str,
    android_id: str,
    os_version: str = DEFAULT_OS_VERSION,
    version_name: str = DEFAULT_VERSION_NAME,
) -> str:
    key = _ee2(ensure_trailing_slash(request_path) + str(request_time))
    data = _ee3(f"{android_id}{heybox_id}{os_version}{version_name}")
    intermediate = _rnd_hmac_sha512(data, key).hex()
    crc = _crc32(intermediate.encode("ascii"), TRUE_CRC32_POLY_REFLECTED, TRUE_CRC32_INIT, TRUE_CRC32_XOROUT)
    return f"{crc:X}"


# ── 签名 URL 构建 ──────────────────────────────────────────

def build_signed_url(
    *,
    request_path: str,
    heybox_id: str,
    android_id: str = DEFAULT_ANDROID_ID,
    device_model: str = "SM-S9210",
    os_version: str = DEFAULT_OS_VERSION,
    version_name: str = DEFAULT_VERSION_NAME,
    build: str = DEFAULT_BUILD,
) -> str:
    request_time = now_timestamp()

    hkey = build_hkey(request_path, request_time, heybox_id, android_id)
    rnd = "14:" + build_rnd(request_path, request_time, heybox_id, android_id, os_version, version_name)
    idx = build_idx(request_time)

    url = merge_query_params(
        urljoin(API_BASE, request_path),
        {
            "heybox_id": heybox_id,
            "imei": android_id,
            "device_info": device_model,
            "nonce": idx,
            "hkey": hkey,
            "os_type": "Android",
            "x_os_type": "Android",
            "x_client_type": "mobile",
            "os_version": os_version,
            "version": version_name,
            "build": build,
            "_time": request_time,
            "dw": "617",
            "channel": "heybox",
            "x_app": "heybox",
            "time_zone": "Asia/Shanghai",
        },
    )
    url = merge_query_params(url, {"_rnd": rnd})

    return url, {
        "time": request_time,
        "nonce": idx,
        "hkey": hkey,
        "rnd": rnd,
    }


# ── HTTP 请求 ──────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; SM-S9210) AppleWebKit/537.36"
        " (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 Mobile Safari/537.36"
    ),
    "Referer": "https://api.xiaoheihe.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def api_get(url: str, cookies: Dict[str, str]) -> dict:
    resp = requests.get(url, headers=HEADERS, cookies=cookies, timeout=30, impersonate="chrome120")
    resp.raise_for_status()
    return resp.json()


# ── 主流程 ─────────────────────────────────────────────────

def main():
    # 1. 获取 Cookie
    cookie_text = ""
    if len(sys.argv) > 1:
        cookie_text = " ".join(sys.argv[1:])
    if not cookie_text:
        cookie_text = os.environ.get("XIAOHEIHE_COOKIE", "")
    if not cookie_text:
        raise SystemExit(
            "用法: python pure_signin.py 'pkey=...; x_xhh_tokenid=...'\n"
            "  或设置环境变量: XIAOHEIHE_COOKIE"
        )

    cookies = parse_cookie(cookie_text)
    pkey = cookies.get("pkey", "")
    token_id = cookies.get("x_xhh_tokenid", "")
    if not pkey:
        raise SystemExit("错误: Cookie 中缺少 pkey")

    heybox_id = derive_heybox_id(pkey, cookies)
    android_id = os.environ.get("XIAOHEIHE_ANDROID_ID", DEFAULT_ANDROID_ID)

    print(f"heybox_id : {heybox_id}")
    print(f"android_id: {android_id}")
    print(f"token_id  : {token_id[:30]}..." if len(token_id) > 30 else f"token_id  : {token_id}")
    print()

    # 2. 查询签到状态
    print("─ 查询签到状态 ─")
    state_url, state_info = build_signed_url(
        request_path=SIGN_STATE_PATH,
        heybox_id=heybox_id,
        android_id=android_id,
    )
    state_resp = api_get(state_url, cookies)
    status = state_resp.get("status", "")
    result = state_resp.get("result", {})
    msg = state_resp.get("msg", "")
    print(f"  status: {status}")
    if msg:
        print(f"  msg:    {msg}")
    if result:
        state = result.get("state", "")
        print(f"  state:  {state}")
        if state == "ok":
            streak = result.get("sign_in_streak", 0)
            coin = result.get("sign_in_coin", 0)
            exp = result.get("sign_in_exp", 0)
            print(f"  已签到! 连续 {streak} 天, +{coin}H币 +{exp}exp")
        elif state == "ignore":
            print("  今天已经签到过了 (ignore)")
        else:
            print(f"  {state}")

    print()

    # 3. 执行签到
    print("─ 执行签到 ─")
    sign_url, sign_info = build_signed_url(
        request_path=SIGN_PATH,
        heybox_id=heybox_id,
        android_id=android_id,
    )
    print(f"  time:  {sign_info['time']}")
    print(f"  nonce: {sign_info['nonce']}")
    print(f"  hkey:  {sign_info['hkey']}")
    print(f"  _rnd:  {sign_info['rnd']}")

    sign_resp = api_get(sign_url, cookies)
    status = sign_resp.get("status", "")
    result = sign_resp.get("result", {})
    msg = sign_resp.get("msg", "")
    print(f"  status: {status}")
    if msg:
        print(f"  msg:    {msg}")
    if result:
        state = result.get("state", "")
        print(f"  state:  {state}")
        if state == "ok":
            streak = result.get("sign_in_streak", 0)
            coin = result.get("sign_in_coin", 0)
            exp = result.get("sign_in_exp", 0)
            print(f"  ✓ 签到成功! 连续 {streak} 天, +{coin}H币 +{exp}exp")
        elif state == "ignore":
            print("  (今天已签到，无法重复)")

    print()
    print("纯算法签到完成。")

    if sign_resp.get("status") == "ok" and result.get("state") in ("ok", "ignore"):
        return 0
    if sign_resp.get("status") == "failed":
        print(f"签到失败: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
