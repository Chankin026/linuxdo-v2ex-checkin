import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from curl_cffi import requests
from loguru import logger

from captcha_solver import YesCaptchaSolver, YesCaptchaSolverError
from notify import NotificationManager

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

NODESEEK_BASE_URL = "https://www.nodeseek.com"
NODESEEK_SIGNIN_PAGE_URL = f"{NODESEEK_BASE_URL}/signIn.html"
NODESEEK_SIGNIN_API_URL = f"{NODESEEK_BASE_URL}/api/account/signIn"
NODESEEK_ATTENDANCE_API_URL = f"{NODESEEK_BASE_URL}/api/attendance"
NODESEEK_CREDIT_PAGE_URL = f"{NODESEEK_BASE_URL}/api/account/credit/page-{{page}}"
NODESEEK_TURNSTILE_SITEKEY = "0x4AAAAAAAaNy7leGjewpVyR"


class NodeSeekDailyMission:
    def __init__(
        self,
        cookie_str: str = "",
        username: str = "",
        password: str = "",
        env_file_path: str = "/etc/linuxdo-v2ex-checkin.env",
        notifier: Optional[NotificationManager] = None,
        solver_type: str = "",
        yescaptcha_client_key: str = "",
        yescaptcha_api_base_url: str = "https://api.yescaptcha.com",
        yescaptcha_advanced: bool = False,
        attendance_random: bool = True,
        impersonate: str = "chrome136",
        cookie_env_var_name: str = "NODESEEK_COOKIE",
        account_name: str = "",
    ):
        self.cookie_str = cookie_str.strip()
        self.username = username.strip()
        self.password = password.strip()
        self.env_file_path = env_file_path
        self.notifier = notifier or NotificationManager()
        self.solver_type = (solver_type or "").strip().lower()
        self.yescaptcha_client_key = yescaptcha_client_key.strip()
        self.yescaptcha_api_base_url = yescaptcha_api_base_url.strip() or "https://api.yescaptcha.com"
        self.yescaptcha_advanced = yescaptcha_advanced
        self.attendance_random = attendance_random
        self.cookie_env_var_name = cookie_env_var_name.strip() or "NODESEEK_COOKIE"
        self.account_name = account_name.strip()
        self.impersonate_candidates = self.build_impersonate_candidates(impersonate)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        if self.cookie_str:
            self.sync_session_from_cookie_string(self.cookie_str)

    @staticmethod
    def build_impersonate_candidates(initial: str) -> List[str]:
        candidates = [
            initial.strip() if isinstance(initial, str) else "",
            "chrome136",
            "chrome133a",
            "chrome131",
            "safari184",
            "edge101",
            "firefox135",
        ]
        result: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in result:
                result.append(candidate)
        return result or ["chrome136"]

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> List[dict]:
        cookies = []
        for part in cookie_str.strip().split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".nodeseek.com",
                    "path": "/",
                }
            )
        return cookies

    def sync_session_from_cookie_string(self, cookie_str: str) -> None:
        for cookie in self.parse_cookie_string(cookie_str):
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
            )

    def get_cookie_string_from_session(self) -> str:
        cookie_parts = []
        try:
            for cookie in self.session.cookies.jar:
                cookie_parts.append(f"{cookie.name}={cookie.value}")
        except Exception:
            return ""
        return "; ".join(cookie_parts)

    def get_account_display_name(self) -> str:
        return self.account_name or self.username or "unknown"

    def get_notify_timezone(self) -> str:
        timezone_name = getattr(self.notifier, "notify_timezone", "") or "Asia/Shanghai"
        return timezone_name

    def get_today_date(self):
        try:
            if ZoneInfo is not None:
                return datetime.now(ZoneInfo(self.get_notify_timezone())).date()
        except Exception:
            pass
        return datetime.now().date()

    def credit_timestamp_to_notify_date(self, timestamp: Optional[datetime]):
        if not isinstance(timestamp, datetime):
            return None
        try:
            if ZoneInfo is not None:
                return timestamp.astimezone(ZoneInfo(self.get_notify_timezone())).date()
        except Exception:
            pass
        try:
            return timestamp.astimezone().date()
        except Exception:
            return timestamp.date()

    @staticmethod
    def format_amount(value) -> str:
        if value is None:
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def parse_numeric_value(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if not match:
            return None
        number_text = match.group(0)
        try:
            if "." in number_text:
                return float(number_text)
            return int(number_text)
        except ValueError:
            return None

    @staticmethod
    def first_not_none(*values):
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def parse_credit_timestamp(value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 1e12:
                timestamp /= 1000
            try:
                return datetime.fromtimestamp(timestamp)
            except Exception:
                return None

        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None

    def normalize_credit_record(self, credit) -> Optional[Dict[str, object]]:
        if isinstance(credit, dict):
            amount = self.first_not_none(
                credit.get("amount"),
                credit.get("value"),
                credit.get("credit"),
                credit.get("delta"),
            )
            balance = self.first_not_none(
                credit.get("balance"),
                credit.get("currentBalance"),
                credit.get("total"),
            )
            description = str(
                self.first_not_none(
                    credit.get("description"),
                    credit.get("desc"),
                    credit.get("title"),
                    "",
                )
            ).strip()
            timestamp = self.first_not_none(
                credit.get("createTime"),
                credit.get("createdAt"),
                credit.get("time"),
                credit.get("date"),
            )
        elif isinstance(credit, (list, tuple)) and len(credit) >= 4:
            amount, balance, description, timestamp = credit[:4]
            description = str(description).strip()
        else:
            return None

        return {
            "amount": self.parse_numeric_value(amount),
            "balance": self.parse_numeric_value(balance),
            "description": description,
            "timestamp": self.parse_credit_timestamp(timestamp),
        }

    def fetch_credit_records(self, max_pages: int = 20) -> List[Dict[str, object]]:
        records: List[Dict[str, object]] = []
        page = 1
        while page <= max_pages:
            response = self.request_with_fallback(
                "GET",
                NODESEEK_CREDIT_PAGE_URL.format(page=page),
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"{NODESEEK_BASE_URL}/account/credit",
                },
            )
            if response.status_code != 200:
                break

            data = self.parse_json_response(response)
            raw_credits = data.get("credits") or data.get("data") or []
            if isinstance(raw_credits, dict):
                raw_credits = raw_credits.get("credits") or raw_credits.get("data") or []
            if not isinstance(raw_credits, list) or not raw_credits:
                break

            for credit in raw_credits:
                normalized = self.normalize_credit_record(credit)
                if normalized:
                    records.append(normalized)

            if len(raw_credits) < 20:
                break
            page += 1

        return records

    def get_credit_summary(self) -> Dict[str, object]:
        records = self.fetch_credit_records()
        if not records:
            logger.warning(
                f"NodeSeek credit summary is empty for {self.get_account_display_name()}"
            )
            return {}

        latest_balance = None
        for record in records:
            if record.get("balance") is not None:
                latest_balance = record["balance"]
                break

        signin_records = []
        today_signin_reward = None
        today_date = self.get_today_date()

        for record in records:
            description = str(record.get("description") or "")
            timestamp = record.get("timestamp")
            is_signin = "签到" in description
            if is_signin:
                signin_records.append(record)
            if (
                today_signin_reward is None
                and is_signin
                and isinstance(timestamp, datetime)
                and self.credit_timestamp_to_notify_date(timestamp) == today_date
                and record.get("amount") is not None
            ):
                today_signin_reward = record["amount"]

        if today_signin_reward is None:
            for record in signin_records:
                if record.get("amount") is not None:
                    today_signin_reward = record["amount"]
                    break

        current_streak = None
        if signin_records:
            description = str(signin_records[0].get("description") or "")
            match = re.search(r"\u8fde\u7eed\u7b7e\u5230\s*(\d+)\s*\u5929", description)
            if match:
                current_streak = int(match.group(1))

        return {
            "today_reward": today_signin_reward,
            "current_balance": latest_balance,
            "total_signins": len(signin_records) if signin_records else None,
            "current_streak": current_streak,
        }

    def extract_reward_from_detail(self, detail: str):
        match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*\u9e21\u817f", detail or "")
        if not match:
            return None
        return self.parse_numeric_value(match.group(1))

    def persist_cookie_if_possible(self, cookie_str: str) -> bool:
        if not cookie_str:
            return False

        try:
            target_env_name = self.cookie_env_var_name or "NODESEEK_COOKIE"
            existing_lines: List[str] = []
            if self.env_file_path:
                try:
                    with open(self.env_file_path, "r", encoding="utf-8") as f:
                        existing_lines = f.read().splitlines()
                except FileNotFoundError:
                    existing_lines = []

            updated = False
            new_lines: List[str] = []
            for line in existing_lines:
                if line.startswith(f"{target_env_name}="):
                    new_lines.append(f"{target_env_name}={cookie_str}")
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                new_lines.append(f"{target_env_name}={cookie_str}")

            with open(self.env_file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines).rstrip() + "\n")
            logger.info(f"Saved {target_env_name} back to {self.env_file_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to persist {target_env_name}: {e}")
            return False

    def should_fallback(self, response) -> bool:
        try:
            text = (response.text or "")[:4000].lower()
        except Exception:
            text = ""
        server = (response.headers.get("server") or "").lower()
        return response.status_code in {403, 429} and (
            "cloudflare" in server
            or "challenge-platform" in text
            or "just a moment" in text
            or "too many requests" in text
        )

    def request_with_fallback(self, method: str, url: str, **kwargs):
        last_response = None
        last_error = None
        for impersonate in self.impersonate_candidates:
            try:
                response = self.session.request(
                    method,
                    url,
                    impersonate=impersonate,
                    timeout=20,
                    allow_redirects=True,
                    **kwargs,
                )
                last_response = response
                if self.should_fallback(response):
                    logger.warning(
                        f"NodeSeek request hit challenge with {impersonate}: "
                        f"{response.status_code}; trying next fingerprint"
                    )
                    continue
                return response
            except Exception as e:
                last_error = e
                logger.warning(
                    f"NodeSeek request failed with {impersonate}: {e}; trying next fingerprint"
                )

        if last_response is not None:
            return last_response
        if last_error is not None:
            raise last_error
        raise RuntimeError("NodeSeek request failed without a response")

    def build_attendance_url(self) -> str:
        random_value = "true" if self.attendance_random else "false"
        return f"{NODESEEK_ATTENDANCE_API_URL}?random={random_value}"

    def parse_json_response(self, response) -> dict:
        try:
            return response.json()
        except Exception:
            try:
                return json.loads(response.text)
            except Exception:
                return {}

    def attendance_with_current_session(self) -> Tuple[bool, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": NODESEEK_BASE_URL,
            "Referer": f"{NODESEEK_BASE_URL}/board",
        }
        response = self.request_with_fallback(
            "POST",
            self.build_attendance_url(),
            headers=headers,
            json={},
        )
        data = self.parse_json_response(response)
        message = str(data.get("message") or data.get("msg") or "").strip()

        if data.get("success") is True:
            return True, message or "Attendance succeeded"

        already_done_markers = [
            "\u4eca\u65e5\u5df2\u7b7e\u5230",
            "\u4eca\u65e5\u5df2\u9886\u53d6",
            "\u4eca\u5929\u5df2\u5b8c\u6210\u7b7e\u5230",
            "\u8bf7\u52ff\u91cd\u590d\u64cd\u4f5c",
            "\u5df2\u5b8c\u6210\u7b7e\u5230",
            "already",
            "claimed",
        ]
        if any(marker.lower() in message.lower() for marker in already_done_markers):
            return True, message or "Attendance already completed today"

        invalid_markers = [
            "\u65e0\u6743\u9650",
            "\u8bf7\u5148\u767b\u5f55",
            "\u672a\u767b\u5f55",
            "\u8ba4\u8bc1",
            "unauthorized",
            "forbidden",
            "cookie",
            "login",
            "auth",
        ]
        if response.status_code in {401, 403} or any(
            marker.lower() in message.lower() for marker in invalid_markers
        ):
            return False, "cookie_invalid"

        if data.get("status") == 404:
            return False, "cookie_invalid"

        if response.status_code == 200 and not data and response.text:
            text = response.text[:200].strip()
            return False, f"Attendance failed with a non-JSON response: {text}"

        return False, message or f"Attendance failed with HTTP {response.status_code}"

    def login_with_yescaptcha(self) -> Tuple[bool, str]:
        if not self.username or not self.password:
            return False, "NodeSeek username/password not configured"
        if self.solver_type != "yescaptcha":
            return False, "NodeSeek requires SOLVER_TYPE=yescaptcha"
        if not self.yescaptcha_client_key:
            return False, "YesCaptcha client key is not configured"

        logger.info("Opening NodeSeek sign-in page...")
        response = self.request_with_fallback(
            "GET",
            NODESEEK_SIGNIN_PAGE_URL,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8"
                ),
                "Referer": NODESEEK_BASE_URL,
            },
        )
        if response.status_code != 200:
            return False, f"Failed to open NodeSeek sign-in page: HTTP {response.status_code}"

        logger.info("Solving NodeSeek Turnstile with YesCaptcha...")
        try:
            solver = YesCaptchaSolver(
                api_base_url=self.yescaptcha_api_base_url,
                client_key=self.yescaptcha_client_key,
                advanced=self.yescaptcha_advanced,
            )
            token = solver.solve(
                url=NODESEEK_SIGNIN_PAGE_URL,
                sitekey=NODESEEK_TURNSTILE_SITEKEY,
                user_agent=self.session.headers.get("User-Agent"),
                verbose=False,
            )
        except YesCaptchaSolverError as e:
            return False, f"YesCaptcha failed for NodeSeek: {e}"

        payload = {
            "username": self.username,
            "password": self.password,
            "token": token,
            "source": "turnstile",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": NODESEEK_BASE_URL,
            "Referer": NODESEEK_SIGNIN_PAGE_URL,
        }
        response = self.request_with_fallback(
            "POST",
            NODESEEK_SIGNIN_API_URL,
            headers=headers,
            json=payload,
        )
        data = self.parse_json_response(response)
        message = str(data.get("message") or data.get("msg") or "").strip()
        if response.status_code != 200:
            return False, message or f"NodeSeek sign-in failed with HTTP {response.status_code}"
        if data.get("success") is not True:
            return False, message or "NodeSeek sign-in did not succeed"

        cookie_str = self.get_cookie_string_from_session()
        if cookie_str:
            self.cookie_str = cookie_str
            self.persist_cookie_if_possible(cookie_str)
        return True, message or "NodeSeek sign-in succeeded"

    def get_signin_stats(self) -> Tuple[Optional[int], Optional[int]]:
        signin_records = [
            record
            for record in self.fetch_credit_records()
            if "\u7b7e\u5230" in str(record.get("description") or "")
        ]
        total_signins = len(signin_records) if signin_records else None
        current_streak = None
        if signin_records:
            description = str(signin_records[0].get("description") or "")
            match = re.search(
                r"\u8fde\u7eed\u7b7e\u5230\s*(\d+)\s*\u5929",
                description,
            )
            if match:
                current_streak = int(match.group(1))
        return total_signins, current_streak

    def send_success_notification(self, detail: str) -> None:
        summary = self.get_credit_summary()
        today_reward = summary.get("today_reward")
        if today_reward is None:
            today_reward = self.extract_reward_from_detail(detail)
        current_balance = summary.get("current_balance")
        current_streak = summary.get("current_streak")
        logger.info(
            "NodeSeek notification summary: "
            f"account={self.get_account_display_name()}, "
            f"today_reward={today_reward}, "
            f"current_balance={current_balance}, "
            f"current_streak={current_streak}"
        )
        lines = [
            "✅ NodeSeek daily mission completed",
            f"Account: {self.get_account_display_name()}",
            f"Result: {detail}",
        ]
        if today_reward is not None:
            lines.append(f"Today's reward: {self.format_amount(today_reward)} 鸡腿")
        if current_balance is not None:
            lines.append(f"Current balance: {self.format_amount(current_balance)} 鸡腿")
        if current_streak is not None:
            lines.append(f"Current streak: {current_streak} days")
        self.notifier.send_all("NodeSeek", "\n".join(lines))

    def send_failure_notification(self, detail: str) -> None:
        lines = [
            "❌ NodeSeek daily mission failed",
            f"Account: {self.get_account_display_name()}",
            f"Reason: {detail}",
        ]
        self.notifier.send_all("NodeSeek", "\n".join(lines))

    def run(self) -> bool:
        logger.info(
            f"Starting NodeSeek daily mission for {self.get_account_display_name()}..."
        )

        if self.cookie_str:
            self.sync_session_from_cookie_string(self.cookie_str)
            ok, detail = self.attendance_with_current_session()
            if ok:
                logger.success(f"NodeSeek attendance succeeded with cookie: {detail}")
                self.send_success_notification(detail)
                return True
            if detail != "cookie_invalid":
                logger.error(f"NodeSeek attendance failed with cookie: {detail}")
                self.send_failure_notification(detail)
                return False
            logger.warning("NodeSeek cookie looks invalid; falling back to username/password")

        ok, detail = self.login_with_yescaptcha()
        if not ok:
            logger.error(f"NodeSeek login failed: {detail}")
            self.send_failure_notification(detail)
            return False

        ok, detail = self.attendance_with_current_session()
        if ok:
            logger.success(f"NodeSeek attendance succeeded after sign-in: {detail}")
            self.send_success_notification(detail)
            return True

        logger.error(f"NodeSeek attendance failed after sign-in: {detail}")
        self.send_failure_notification(detail)
        return False
