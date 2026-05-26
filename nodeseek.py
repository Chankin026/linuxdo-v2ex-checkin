import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

from curl_cffi import requests
from loguru import logger

from captcha_solver import YesCaptchaSolver, YesCaptchaSolverError
from nodeseek_email import ImapEmailCodeFetcher, infer_imap_host
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
        email_address: str = "",
        email_imap_host: str = "",
        email_imap_port: int = 993,
        email_imap_username: str = "",
        email_imap_password: str = "",
        email_imap_mailbox: str = "INBOX",
        email_code_timeout: int = 300,
        email_code_poll_interval: int = 10,
        email_code_fetcher=None,
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
        self.email_address = email_address.strip()
        self.email_imap_host = email_imap_host.strip()
        self.email_imap_port = int(email_imap_port or 993)
        self.email_imap_username = email_imap_username.strip()
        self.email_imap_password = email_imap_password
        self.email_imap_mailbox = email_imap_mailbox.strip() or "INBOX"
        self.email_code_timeout = int(email_code_timeout or 300)
        self.email_code_poll_interval = int(email_code_poll_interval or 10)
        self.email_code_fetcher = email_code_fetcher
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

    def send_success_notification(self, detail: str) -> None:
        summary = self.get_credit_summary()
        lines = [
            "✅ NodeSeek daily attendance completed",
            f"Account: {self.get_account_display_name()}",
        ]
        today_reward = summary.get("today_reward")
        current_balance = summary.get("current_balance")
        current_streak = summary.get("current_streak")
        if today_reward is not None:
            lines.append(f"Today reward: {today_reward} chicken legs")
        if current_balance is not None:
            lines.append(f"Current balance: {current_balance}")
        if current_streak is not None:
            lines.append(f"Current streak: {current_streak} days")
        lines.append(f"Detail: {detail}")
        logger.info(
            "NodeSeek notification summary: "
            f"account={self.get_account_display_name()}, "
            f"today_reward={today_reward}, "
            f"current_balance={current_balance}, "
            f"current_streak={current_streak}"
        )
        self.notifier.send_all("NodeSeek", "\n".join(lines))

    def send_failure_notification(self, detail: str) -> None:
        lines = [
            "❌ NodeSeek daily attendance failed",
            f"Account: {self.get_account_display_name()}",
            f"Reason: {detail}",
        ]
        self.notifier.send_all("NodeSeek", "\n".join(lines))

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
            match = re.search(r"连续签到\s*(\d+)\s*天", description)
            if match:
                current_streak = int(match.group(1))

        return {
            "today_reward": today_signin_reward,
            "current_balance": latest_balance,
            "total_signins": len(signin_records) if signin_records else None,
            "current_streak": current_streak,
        }

    def extract_reward_from_detail(self, detail: str):
        match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*鸡腿", detail or "")
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

    def build_email_code_fetcher(self, email_address: str = ""):
        if self.email_code_fetcher is not None:
            return self.email_code_fetcher
        email_address = (email_address or self.email_address).strip()
        host = self.email_imap_host or infer_imap_host(email_address)
        username = self.email_imap_username or email_address
        if not (
            host
            and username
            and self.email_imap_password
        ):
            return None
        self.email_code_fetcher = ImapEmailCodeFetcher(
            host=host,
            port=self.email_imap_port,
            username=username,
            password=self.email_imap_password,
            mailbox=self.email_imap_mailbox,
            timeout=self.email_code_timeout,
            poll_interval=self.email_code_poll_interval,
        )
        return self.email_code_fetcher

    @staticmethod
    def _json_for_js(value: str) -> str:
        return json.dumps(value or "", ensure_ascii=False)

    @staticmethod
    def _extract_email_from_login_response(data: dict) -> str:
        candidates = []
        if isinstance(data, dict):
            candidates.extend(
                [
                    data.get("email"),
                    data.get("mail"),
                    data.get("redirect"),
                    data.get("url"),
                    data.get("location"),
                    data.get("message"),
                    data.get("msg"),
                ]
            )
            nested = data.get("data")
            if isinstance(nested, dict):
                candidates.extend(
                    [
                        nested.get("email"),
                        nested.get("mail"),
                        nested.get("redirect"),
                        nested.get("url"),
                        nested.get("location"),
                    ]
                )
            elif isinstance(nested, str):
                candidates.append(nested)

        for candidate in candidates:
            if not candidate:
                continue
            text = unquote(str(candidate))
            parsed = urlparse(text)
            query_email = parse_qs(parsed.query).get("email", [""])[0]
            if query_email:
                return query_email.strip()
            match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _login_response_requires_email_verification(data: dict) -> bool:
        text = json.dumps(data, ensure_ascii=False).lower()
        return (
            bool(NodeSeekDailyMission._extract_email_from_login_response(data))
            or "emailsignin" in text
            or "邮箱" in text
            or ("email" in text and ("verify" in text or "verification" in text))
        )

    @staticmethod
    def _wait_for_cloudflare(browser, max_wait: int = 60) -> bool:
        import time as _time
        waited = 0
        while waited < max_wait:
            title = str(getattr(browser, "title", "") or "").lower()
            url = str(getattr(browser, "url", "") or "").lower()
            if "just a moment" not in title and "challenge" not in url:
                return True
            _time.sleep(2)
            waited += 2
        return False

    @staticmethod
    def _read_browser_turnstile_token(browser) -> str:
        js_code = (
            "return (() => {"
            "  const values = [];"
            "  const selectors = ["
            "    'input[name=\"cf-turnstile-response\"]',"
            "    'textarea[name=\"cf-turnstile-response\"]',"
            "    '[name=\"cf-turnstile-response\"]',"
            "    '#captcha-container input[type=\"hidden\"]',"
            "    '#captcha-container textarea',"
            "  ];"
            "  for (const selector of selectors) {"
            "    for (const el of document.querySelectorAll(selector)) {"
            "      if (el && el.value) values.push(el.value);"
            "    }"
            "  }"
            "  if (window.turnstile && typeof window.turnstile.getResponse === 'function') {"
            "    try { values.push(window.turnstile.getResponse()); } catch (e) {}"
            "    for (const el of document.querySelectorAll('[id^=\"cf-chl-widget-\"], [data-sitekey], .cf-turnstile')) {"
            "      try { if (el.id) values.push(window.turnstile.getResponse(el.id)); } catch (e) {}"
            "    }"
            "  }"
            "  for (const value of values) {"
            "    if (typeof value === 'string' && value.trim().length > 10) return value.trim();"
            "  }"
            "  return '';"
            "})()"
        )
        try:
            return str(browser.run_js(js_code) or "").strip()
        except Exception as e:
            logger.debug(f"Unable to read browser Turnstile token: {e}")
            return ""

    @classmethod
    def _wait_for_browser_turnstile_token(cls, browser, max_wait: int = 20) -> str:
        import time as _time

        waited = 0
        while waited <= max_wait:
            token = cls._read_browser_turnstile_token(browser)
            if token:
                return token
            _time.sleep(1)
            waited += 1
        return ""

    def _sync_browser_user_agent(self, browser) -> str:
        try:
            user_agent = str(
                browser.run_js("return navigator.userAgent || ''") or ""
            ).strip()
        except Exception as e:
            logger.debug(f"Unable to read browser user agent: {e}")
            return self.session.headers.get("User-Agent", "")

        if user_agent:
            self.session.headers["User-Agent"] = user_agent
        return user_agent or self.session.headers.get("User-Agent", "")

    # ── Browser-based attendance (DrissionPage + real Chromium) ──────────

    def _attendance_via_browser(self) -> Tuple[bool, str]:
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except ImportError:
            return False, "DrissionPage not available for browser fallback"

        logger.info(
            f"NodeSeek using browser for {self.get_account_display_name()}"
        )

        browser = None
        try:
            co = (
                ChromiumOptions()
                .auto_port()
                .headless(False)
                .incognito(True)
                .set_argument("--no-sandbox")
                .set_argument("--disable-blink-features=AutomationControlled")
                .set_argument("--disable-dev-shm-usage")
                .set_user_agent(self.session.headers.get("User-Agent", ""))
            )
            browser = ChromiumPage(co)

            # Step 1: Navigate homepage with NO cookies — let browser pass CF naturally
            browser.get(NODESEEK_BASE_URL, timeout=30)
            if not self._wait_for_cloudflare(browser):
                return False, "Browser stuck on Cloudflare challenge at homepage"
            logger.info(
                f"Browser homepage: title={str(browser.title)[:60]} url={str(browser.url)[:80]}"
            )

            # Step 2: If we have credentials, always login to get fresh cookies
            if self.username and self.password:
                ok, detail = self._browser_login(browser)
                if not ok:
                    return False, detail
                # After login, do attendance
                browser.get(f"{NODESEEK_BASE_URL}/board", timeout=30)
                if not self._wait_for_cloudflare(browser):
                    return False, "Browser stuck on Cloudflare challenge after login"
                ok, detail = self._browser_fetch_attendance(browser)
                if ok:
                    self._save_browser_cookies(browser)
                    return True, detail
                return False, f"Login OK but attendance failed: {detail}"

            # Step 3: Cookie-only mode — set cookies AFTER passing CF
            if self.cookie_str:
                browser.set.cookies(self.parse_cookie_string(self.cookie_str))
                browser.get(f"{NODESEEK_BASE_URL}/board", timeout=30)
                if not self._wait_for_cloudflare(browser):
                    return False, "Browser stuck on Cloudflare challenge with cookies"
                ok, detail = self._browser_fetch_attendance(browser)
                if ok:
                    self._save_browser_cookies(browser)
                    return True, detail
                return False, detail

            return False, "No credentials or cookies configured"

        except Exception as e:
            return False, f"Browser error: {e}"
        finally:
            if browser is not None:
                try:
                    browser.quit()
                except Exception:
                    pass

    def _browser_fetch_attendance(self, browser) -> Tuple[bool, str]:
        import json as _json

        random_val = "true" if self.attendance_random else "false"
        js_code = (
            "return (async () => {"
            "  const resp = await fetch('/api/attendance?random=" + random_val + "', {"
            "    method: 'POST',"
            "    headers: { 'Content-Type': 'application/json' },"
            "    body: JSON.stringify({}),"
            "    credentials: 'include',"
            "  });"
            "  const text = await resp.text();"
            "  return JSON.stringify({ status: resp.status, body: text });"
            "})();"
        )
        result_json = browser.run_js(js_code)
        if not result_json:
            return False, "Browser fetch returned no result"

        result = _json.loads(result_json)
        status_code = result.get("status", 0)
        body_text = result.get("body", "")

        if status_code != 200:
            return False, f"Browser attendance HTTP {status_code}: {body_text[:200]}"

        data = _json.loads(body_text) if body_text else {}
        message = str(data.get("message") or data.get("msg") or "").strip()

        if data.get("success") is True:
            return True, message or "Attendance succeeded via browser"

        already_markers = [
            "今日已签到", "今日已领取", "今天已完成签到",
            "请勿重复操作", "已完成签到", "already", "claimed",
        ]
        if any(m.lower() in message.lower() for m in already_markers):
            return True, message or "Attendance already completed"

        return False, message or f"Browser attendance failed: {body_text[:200]}"

    def _browser_login(self, browser) -> Tuple[bool, str]:
        if not self.username or not self.password:
            return False, "No username/password for browser login"

        import json as _json

        # Navigate to sign-in page
        browser.get(NODESEEK_SIGNIN_PAGE_URL, timeout=30)
        if not self._wait_for_cloudflare(browser):
            return False, "Browser stuck on Cloudflare at sign-in page"
        browser.wait(3)
        browser_user_agent = self._sync_browser_user_agent(browser)

        # Extract turnstile sitekey from page
        sitekey = browser.run_js(
            "return document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey') || ''"
        ) or NODESEEK_TURNSTILE_SITEKEY

        token = self._wait_for_browser_turnstile_token(browser)
        if token:
            logger.info(
                "Using browser-generated NodeSeek login Turnstile token "
                f"for {self.get_account_display_name()}"
            )
        else:
            if self.solver_type != "yescaptcha" or not self.yescaptcha_client_key:
                return False, "YesCaptcha not configured for browser login"

            logger.info(f"Solving NodeSeek Turnstile (sitekey={sitekey}) via YesCaptcha...")
            try:
                solver = YesCaptchaSolver(
                    api_base_url=self.yescaptcha_api_base_url,
                    client_key=self.yescaptcha_client_key,
                    advanced=self.yescaptcha_advanced,
                )
                token = solver.solve(
                    url=NODESEEK_SIGNIN_PAGE_URL,
                    sitekey=sitekey,
                    user_agent=browser_user_agent,
                    verbose=False,
                )
            except YesCaptchaSolverError as e:
                return False, f"Browser login YesCaptcha failed: {e}"

        if not token:
            return False, "NodeSeek login Turnstile token is empty"

        login_started_at = datetime.now(timezone.utc)

        # Submit login via browser JS fetch
        username_js = self._json_for_js(self.username)
        password_js = self._json_for_js(self.password)
        token_js = self._json_for_js(token)
        js_code = (
            "return (async () => {"
            "  const payload = JSON.stringify({"
            "    username: " + username_js + ","
            "    password: " + password_js + ","
            "    token: " + token_js + ","
            "    source: 'turnstile',"
            "  });"
            "  const resp = await fetch('/api/account/signIn', {"
            "    method: 'POST',"
            "    headers: { 'Content-Type': 'application/json;charset=UTF-8' },"
            "    body: payload,"
            "    credentials: 'include',"
            "  });"
            "  const text = await resp.text();"
            "  return JSON.stringify({ status: resp.status, body: text });"
            "})();"
        )
        result_json = browser.run_js(js_code)
        if not result_json:
            return False, "Browser login fetch returned no result"

        result = _json.loads(result_json)
        body_text = result.get("body", "")
        data = _json.loads(body_text) if body_text else {}
        message = str(data.get("message") or data.get("msg") or "").strip()

        if (
            result.get("status") == 200
            and data.get("success") is True
            and self._login_response_requires_email_verification(data)
        ):
            return self._browser_complete_email_signin(
                browser=browser,
                login_data=data,
                not_before=login_started_at,
            )

        if result.get("status") != 200 or data.get("success") is not True:
            return False, message or f"Browser login HTTP {result.get('status')}"

        logger.success(f"NodeSeek browser login succeeded for {self.get_account_display_name()}")
        return True, message or "Login succeeded via browser"

    def _browser_complete_email_signin(
        self,
        browser,
        login_data: dict,
        not_before: datetime,
    ) -> Tuple[bool, str]:
        import json as _json

        email_address = (
            self.email_address
            or self._extract_email_from_login_response(login_data)
        ).strip()
        if not email_address:
            return False, "NodeSeek email verification required but email is unknown"

        fetcher = self.build_email_code_fetcher(email_address)
        if fetcher is None:
            return (
                False,
                "NodeSeek email verification required but IMAP is not configured",
            )

        logger.info(
            "NodeSeek email verification required for "
            f"{self.get_account_display_name()} ({email_address})"
        )

        email_page_url = (
            f"{NODESEEK_BASE_URL}/emailSignIn.html?email={quote(email_address)}"
        )
        browser.get(email_page_url, timeout=30)
        if not self._wait_for_cloudflare(browser):
            return False, "Browser stuck on Cloudflare at email sign-in page"
        browser.wait(3)
        browser_user_agent = self._sync_browser_user_agent(browser)

        sitekey = browser.run_js(
            "return document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey') || ''"
        ) or NODESEEK_TURNSTILE_SITEKEY

        email_token = self._wait_for_browser_turnstile_token(browser)
        if email_token:
            logger.info(
                "Using browser-generated NodeSeek email Turnstile token "
                f"for {self.get_account_display_name()}"
            )
        else:
            logger.info(
                "Solving NodeSeek email Turnstile "
                f"(sitekey={sitekey}) via YesCaptcha..."
            )
            try:
                solver = YesCaptchaSolver(
                    api_base_url=self.yescaptcha_api_base_url,
                    client_key=self.yescaptcha_client_key,
                    advanced=self.yescaptcha_advanced,
                )
                email_token = solver.solve(
                    url=email_page_url,
                    sitekey=sitekey,
                    user_agent=browser_user_agent,
                    verbose=False,
                )
            except YesCaptchaSolverError as e:
                return False, f"Browser email verification YesCaptcha failed: {e}"

        email_js = self._json_for_js(email_address)
        token_js = self._json_for_js(email_token)
        send_code_js = (
            "return (async () => {"
            "  const payload = JSON.stringify({"
            "    email: " + email_js + ","
            "    mode: 'totp',"
            "    token: " + token_js + ","
            "    source: 'turnstile',"
            "    version: 'v3',"
            "  });"
            "  const resp = await fetch('/api/email', {"
            "    method: 'POST',"
            "    headers: { 'Content-Type': 'application/json;charset=UTF-8' },"
            "    body: payload,"
            "    credentials: 'include',"
            "  });"
            "  const text = await resp.text();"
            "  return JSON.stringify({ status: resp.status, body: text });"
            "})();"
        )
        result_json = browser.run_js(send_code_js)
        if not result_json:
            return False, "NodeSeek email code request returned no result"

        result = _json.loads(result_json)
        body_text = result.get("body", "")
        data = _json.loads(body_text) if body_text else {}
        message = str(data.get("message") or data.get("msg") or "").strip()
        if result.get("status") != 200 or data.get("success") is not True:
            return False, message or f"NodeSeek email code HTTP {result.get('status')}"

        code = fetcher.wait_for_code(
            email_address=email_address,
            not_before=not_before,
        )
        if not code:
            return False, "Timed out waiting for NodeSeek email verification code"

        code_js = self._json_for_js(code)
        verify_js = (
            "return (async () => {"
            "  const payload = JSON.stringify({"
            "    email: " + email_js + ","
            "    code: " + code_js + ","
            "  });"
            "  const resp = await fetch('/api/account/emailSignIn', {"
            "    method: 'POST',"
            "    headers: { 'Content-Type': 'application/json;charset=UTF-8' },"
            "    body: payload,"
            "    credentials: 'include',"
            "  });"
            "  const text = await resp.text();"
            "  return JSON.stringify({ status: resp.status, body: text });"
            "})();"
        )
        result_json = browser.run_js(verify_js)
        if not result_json:
            return False, "NodeSeek email sign-in returned no result"

        result = _json.loads(result_json)
        body_text = result.get("body", "")
        data = _json.loads(body_text) if body_text else {}
        message = str(data.get("message") or data.get("msg") or "").strip()
        if result.get("status") != 200 or data.get("success") is not True:
            return False, message or f"NodeSeek email sign-in HTTP {result.get('status')}"

        logger.success(
            f"NodeSeek email verification succeeded for {self.get_account_display_name()}"
        )
        return True, message or "Login succeeded via email verification"

    def _save_browser_cookies(self, browser) -> None:
        try:
            cookie_str = browser.cookies().as_str()
            if cookie_str and isinstance(cookie_str, str) and cookie_str.strip():
                self.cookie_str = cookie_str
                self.sync_session_from_cookie_string(cookie_str)
                self.persist_cookie_if_possible(cookie_str)
                logger.info(f"Saved browser cookies for {self.get_account_display_name()}")
        except Exception as e:
            logger.warning(f"Failed to save browser cookies: {e}")

    # ── Main run ────────────────────────────────────────────────────────

    def run(self) -> bool:
        logger.info(
            f"Starting NodeSeek daily mission for {self.get_account_display_name()}..."
        )

        if self.cookie_str or (self.username and self.password):
            ok, detail = self._attendance_via_browser()
            if ok:
                logger.success(f"NodeSeek attendance succeeded via browser: {detail}")
                self.send_success_notification(detail)
                return True
            logger.error(f"NodeSeek browser flow failed: {detail}")
            self.send_failure_notification(detail)
            return False

        logger.error(
            f"NodeSeek: no cookie or username/password configured "
            f"for {self.get_account_display_name()}"
        )
        return False
