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
UINT32_MASK = (1 << 32) - 1

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

# ── 工具函数 ──────────────────────────────────────────────

def u32(value: int) -> int:
    return value & UINT32_MASK


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


def build_hkey(request_path: str, request_time: str, heybox_id: str, android_id: str) -> str:
    seed_text = build_seed_text(request_path, request_time, heybox_id, android_id).encode("utf-8")
    state_block = hmac.new(HMAC_KEY, seed_text, hashlib.sha512).digest()
    if len(state_block) != STATE_BLOCK_LEN:
        raise SystemExit(f"内部错误: state block 长度异常 ({len(state_block)})")
    crc = _crc32(state_block, FALSE_CRC32_POLY_REFLECTED, FALSE_CRC32_INIT, FALSE_CRC32_XOROUT)
    return f"{crc:X}"


def build_rnd(request_path: str, request_time: str, heybox_id: str, android_id: str) -> str:
    seed_text = build_seed_text(request_path, request_time, heybox_id, android_id)
    data = seed_text.encode("utf-8") if isinstance(seed_text, str) else bytes(seed_text)
    crc = _crc32(data, TRUE_CRC32_POLY_REFLECTED, TRUE_CRC32_INIT, TRUE_CRC32_XOROUT)
    return f"{crc:X}"


# ── 签名 URL 构建 ──────────────────────────────────────────

def build_signed_url(
    *,
    request_path: str,
    heybox_id: str,
    android_id: str = DEFAULT_ANDROID_ID,
    device_model: str = "SM-S9210",
) -> str:
    request_time = now_timestamp()

    hkey = build_hkey(request_path, request_time, heybox_id, android_id)
    rnd = "14:" + build_rnd(request_path, request_time, heybox_id, android_id)
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
            "os_version": "12",
            "version": "1.3.385",
            "build": "834",
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
