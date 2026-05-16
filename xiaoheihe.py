import json
import os
import random
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from curl_cffi import requests
from loguru import logger

from notify import NotificationManager
from pure_signin import (
    API_BASE as XIAOHEIHE_API_BASE_URL,
    DEFAULT_ANDROID_ID as DEFAULT_XIAOHEIHE_ANDROID_ID,
    SIGN_PATH as XIAOHEIHE_SIGN_PATH,
    SIGN_STATE_PATH as XIAOHEIHE_SIGN_STATE_PATH,
    build_signed_url,
    derive_heybox_id as derive_heybox_id_from_cookie,
    parse_cookie as parse_cookie_text,
)

DEFAULT_IMPERSONATE = (
    os.environ.get("IMPERSONATE_VERSION", "chrome136").strip() or "chrome136"
)
DEFAULT_XIAOHEIHE_DEVICE_MODEL = "SM-S9210"

XIAOHEIHE_REQUEST_MODE_LABELS = {
    "signer": "python local signer",
}


def resolve_request_mode_label(mode: str) -> str:
    return XIAOHEIHE_REQUEST_MODE_LABELS["signer"]


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def parse_cookie_string(cookie_text: str) -> Dict[str, str]:
    try:
        return parse_cookie_text(cookie_text)
    except SystemExit as exc:
        raise XiaoHeiHeSignerError(str(exc)) from exc


def derive_heybox_id(
    pkey: str,
    cookies: Mapping[str, str],
    explicit: str = "",
) -> str:
    explicit_value = str(explicit or "").strip()
    if explicit_value:
        return explicit_value
    try:
        return derive_heybox_id_from_cookie(pkey, dict(cookies))
    except SystemExit as exc:
        raise XiaoHeiHeSignerError(str(exc)) from exc


class XiaoHeiHeSignerError(Exception):
    pass


class XiaoHeiHeDailyMission:
    def __init__(
        self,
        notifier: Optional[NotificationManager] = None,
        account_name: str = "",
        cookie: str = "",
        headers_json: str = "",
        timeout: int = 20,
        max_retries: int = 6,
        retry_min_delay: int = 3,
        retry_max_delay: int = 12,
        impersonate: str = DEFAULT_IMPERSONATE,
    ):
        self.notifier = notifier or NotificationManager()
        self.account_name = account_name.strip()
        self.cookie_text = (
            (cookie or "").strip()
            or env_first("XIAOHEIHE_COOKIE", "XIAOHEIHE_COOKIES")
        )
        self.cookies = parse_cookie_string(self.cookie_text)
        self.pkey = self.cookies.get("pkey") or env_first(
            "XIAOHEIHE_PKEY",
            "HEYBOX_PKEY",
        )
        self.token_id = self.cookies.get("x_xhh_tokenid") or env_first(
            "XIAOHEIHE_TOKEN_ID",
            "HEYBOX_TOKEN_ID",
        )
        if not self.pkey:
            raise XiaoHeiHeSignerError("XIAOHEIHE_COOKIE is missing pkey")
        if not self.token_id:
            raise XiaoHeiHeSignerError("XIAOHEIHE_COOKIE is missing x_xhh_tokenid")

        self.heybox_id = derive_heybox_id(
            self.pkey,
            self.cookies,
            explicit=env_first("XIAOHEIHE_HEYBOX_ID", "HEYBOX_HEYBOX_ID"),
        )
        if not self.account_name:
            self.account_name = self.heybox_id

        self.timeout = max(5, int(timeout))
        self.max_retries = max(1, int(max_retries))
        self.retry_min_delay = max(1, int(retry_min_delay))
        self.retry_max_delay = max(self.retry_min_delay, int(retry_max_delay))
        self.impersonate = (impersonate or DEFAULT_IMPERSONATE).strip() or DEFAULT_IMPERSONATE
        self.retry_status_codes = {429, 500, 502, 503, 504}
        self.request_mode_raw = (
            env_first("XIAOHEIHE_REQUEST_MODE", default="signer").strip().lower()
            or "signer"
        )
        self.request_mode = "signer"
        self.android_id = (
            env_first("XIAOHEIHE_ANDROID_ID", default=DEFAULT_XIAOHEIHE_ANDROID_ID)
            or DEFAULT_XIAOHEIHE_ANDROID_ID
        )
        self.device_model = (
            env_first(
                "XIAOHEIHE_DEVICE_MODEL",
                default=DEFAULT_XIAOHEIHE_DEVICE_MODEL,
            )
            or DEFAULT_XIAOHEIHE_DEVICE_MODEL
        )
        self.base_headers = self.parse_headers_json(headers_json)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14; SM-S9210) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 "
                    "Mobile Safari/537.36"
                ),
                "Referer": f"{XIAOHEIHE_API_BASE_URL}/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        if self.base_headers:
            self.session.headers.update(self.base_headers)

    @staticmethod
    def parse_headers_json(headers_json: str) -> Dict[str, str]:
        text = (headers_json or "").strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise XiaoHeiHeSignerError(
                f"XIAOHEIHE_HEADERS_JSON is not valid JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise XiaoHeiHeSignerError("XIAOHEIHE_HEADERS_JSON must be a JSON object")
        return {str(key): str(item) for key, item in value.items()}

    @staticmethod
    def parse_json_response(response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def normalize_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def flatten_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return " ".join(
                fragment
                for fragment in (
                    XiaoHeiHeDailyMission.flatten_text(item)
                    for item in value.values()
                )
                if fragment
            )
        if isinstance(value, (list, tuple, set)):
            return " ".join(
                fragment
                for fragment in (
                    XiaoHeiHeDailyMission.flatten_text(item)
                    for item in value
                )
                if fragment
            )
        return str(value).strip()

    def get_request_mode_label(self) -> str:
        return resolve_request_mode_label(self.request_mode)

    def build_request(self, action: str) -> Tuple[str, Dict[str, str]]:
        action_name = (action or "").strip().lower()
        if action_name == "state":
            request_path = XIAOHEIHE_SIGN_STATE_PATH
        elif action_name == "sign":
            request_path = XIAOHEIHE_SIGN_PATH
        else:
            raise XiaoHeiHeSignerError(f"unsupported Xiaoheihe action: {action}")

        signed_url, meta = build_signed_url(
            request_path=request_path,
            heybox_id=self.heybox_id,
            android_id=self.android_id,
            device_model=self.device_model,
        )
        return signed_url, meta

    def request_with_retry(self, url: str, action: str):
        last_error: Optional[Exception] = None
        response = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    cookies=self.cookies,
                    timeout=self.timeout,
                    impersonate=self.impersonate,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = random.uniform(self.retry_min_delay, self.retry_max_delay)
                logger.warning(
                    f"Xiaoheihe {action} request failed on attempt "
                    f"{attempt}/{self.max_retries}: {exc}; retrying in {delay:.2f}s"
                )
                time.sleep(delay)
                continue

            if (
                response.status_code in self.retry_status_codes
                and attempt < self.max_retries
            ):
                delay = random.uniform(self.retry_min_delay, self.retry_max_delay)
                logger.warning(
                    f"Xiaoheihe {action} returned HTTP {response.status_code} on "
                    f"attempt {attempt}/{self.max_retries}; retrying in {delay:.2f}s"
                )
                time.sleep(delay)
                continue

            return response

        if response is not None:
            return response
        raise XiaoHeiHeSignerError(
            f"Xiaoheihe {action} request failed after "
            f"{self.max_retries} attempts: {last_error}"
        )

    def execute_action(self, action: str):
        request_url, request_meta = self.build_request(action)
        logger.info(
            f"Dispatching Xiaoheihe action={action} request to {request_url}; "
            f"meta={request_meta}"
        )
        return self.request_with_retry(request_url, action)

    @staticmethod
    def describe_payload(payload: Dict[str, Any], fallback: str) -> str:
        result = XiaoHeiHeDailyMission.normalize_result(payload)
        status = str(payload.get("status") or "").strip()
        msg = str(payload.get("msg") or "").strip()
        state = str(result.get("state") or "").strip()
        streak = result.get("sign_in_streak")
        coin = result.get("sign_in_coin")
        exp = result.get("sign_in_exp")

        parts = []
        if msg:
            parts.append(msg)
        if status:
            parts.append(f"status={status}")
        if state:
            parts.append(f"state={state}")
        if streak not in [None, ""]:
            parts.append(f"streak={streak}")
        if coin not in [None, ""]:
            parts.append(f"H币+{coin}")
        if exp not in [None, ""]:
            parts.append(f"exp+{exp}")

        detail = " | ".join(str(part) for part in parts if str(part).strip())
        if detail:
            return detail

        flattened = XiaoHeiHeDailyMission.flatten_text(payload)
        if flattened:
            return flattened[:200]
        return fallback

    def detect_api_error(self, response, payload: Dict[str, Any]) -> Optional[str]:
        text_blob = self.flatten_text(payload) or " ".join(response.text.split())[:200]
        if response.status_code in {401, 403}:
            return "authentication failed"
        if "未登录" in text_blob or "请先登录" in text_blob:
            return "authentication failed"
        if response.status_code >= 400 and response.status_code not in self.retry_status_codes:
            return f"HTTP {response.status_code}"
        if "invalid" in text_blob.lower() and response.status_code >= 400:
            return text_blob[:200] or f"HTTP {response.status_code}"
        return None

    def classify_sign_state(self, response) -> Tuple[str, str]:
        payload = self.parse_json_response(response)
        api_error = self.detect_api_error(response, payload)
        if api_error:
            return "failed", api_error

        result = self.normalize_result(payload)
        status = str(payload.get("status") or "").strip().lower()
        state = str(result.get("state") or "").strip().lower()
        detail = self.describe_payload(payload, "sign state pending")

        if state in {"ok", "ignore"}:
            return "already_done", detail
        if status == "failed":
            return "failed", detail

        lowered = detail.lower()
        if any(marker in lowered for marker in ["已签到", "已经签到", "签到成功"]):
            return "already_done", detail

        return "pending", detail

    def classify_sign_result(self, response) -> Tuple[bool, str]:
        payload = self.parse_json_response(response)
        api_error = self.detect_api_error(response, payload)
        if api_error:
            return False, api_error

        result = self.normalize_result(payload)
        status = str(payload.get("status") or "").strip().lower()
        state = str(result.get("state") or "").strip().lower()
        detail = self.describe_payload(payload, "sign request finished")
        lowered = detail.lower()

        if state in {"ok", "ignore"}:
            return True, detail
        if status == "ok" and any(
            marker in lowered
            for marker in ["签到成功", "已签到", "已经签到", "ignore", "success"]
        ):
            return True, detail
        if status == "ok":
            return True, detail

        if status == "failed":
            return False, detail

        return False, detail

    def get_display_name(self) -> str:
        return self.account_name or "unknown"

    def send_success_notification(self, detail: str) -> None:
        lines = [
            "✅ Xiaoheihe daily mission completed",
            f"Account: {self.get_display_name()}",
            f"Result: {detail}",
            f"Request mode: {self.get_request_mode_label()}",
        ]
        self.notifier.send_all("Xiaoheihe", "\n".join(lines))

    def send_failure_notification(self, detail: str) -> None:
        lines = [
            "❌ Xiaoheihe daily mission failed",
            f"Account: {self.get_display_name()}",
            f"Reason: {detail}",
            f"Request mode: {self.get_request_mode_label()}",
        ]
        self.notifier.send_all("Xiaoheihe", "\n".join(lines))

    def run(self) -> bool:
        logger.info("Starting Xiaoheihe daily mission...")
        if self.request_mode_raw != "signer":
            logger.info(
                f"XIAOHEIHE_REQUEST_MODE={self.request_mode_raw!r} is no longer "
                "supported; falling back to pure Python signer mode"
            )

        try:
            state_response = self.execute_action("state")
        except Exception as exc:
            detail = f"Failed to query Xiaoheihe sign state: {exc}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        state, detail = self.classify_sign_state(state_response)
        logger.info(f"Xiaoheihe sign state: {state} - {detail}")
        if state == "already_done":
            self.send_success_notification(detail)
            return True
        if state == "failed":
            self.send_failure_notification(detail)
            return False

        try:
            sign_response = self.execute_action("sign")
        except Exception as exc:
            detail = f"Failed to submit Xiaoheihe sign request: {exc}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        ok, detail = self.classify_sign_result(sign_response)
        if not ok:
            logger.error(f"Xiaoheihe sign failed: {detail}")
            self.send_failure_notification(detail)
            return False

        try:
            verify_response = self.execute_action("state")
            verify_state, verify_detail = self.classify_sign_state(verify_response)
            if verify_state == "already_done" and verify_detail:
                detail = verify_detail
        except Exception as exc:
            logger.warning(f"Xiaoheihe post-sign verification failed: {exc}")

        logger.success(f"Xiaoheihe sign succeeded: {detail}")
        self.send_success_notification(detail)
        return True


if __name__ == "__main__":
    timeout = max(5, int(env_first("XIAOHEIHE_TIMEOUT", default="20") or "20"))
    max_retries = max(
        1,
        int(env_first("XIAOHEIHE_RETRY_TIMES", default="6") or "6"),
    )
    retry_min_delay = max(
        1,
        int(env_first("XIAOHEIHE_RETRY_MIN_DELAY", default="3") or "3"),
    )
    retry_max_delay = max(
        retry_min_delay,
        int(env_first("XIAOHEIHE_RETRY_MAX_DELAY", default="12") or "12"),
    )
    mission = XiaoHeiHeDailyMission(
        notifier=NotificationManager(),
        account_name=env_first("XIAOHEIHE_ACCOUNT_NAME"),
        cookie=env_first("XIAOHEIHE_COOKIE", "XIAOHEIHE_COOKIES"),
        headers_json=env_first("XIAOHEIHE_HEADERS_JSON"),
        timeout=timeout,
        max_retries=max_retries,
        retry_min_delay=retry_min_delay,
        retry_max_delay=retry_max_delay,
        impersonate=env_first(
            "XIAOHEIHE_IMPERSONATE",
            "IMPERSONATE_VERSION",
            default=DEFAULT_IMPERSONATE,
        ),
    )
    logger.info(
        "Standalone Xiaoheihe entrypoint: "
        f"request_mode={mission.get_request_mode_label()}, "
        f"account_name={mission.get_display_name()}"
    )
    raise SystemExit(0 if mission.run() else 1)
