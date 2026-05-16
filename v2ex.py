import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests
from loguru import logger

from notify import NotificationManager

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

V2EX_HOME_URL = "https://www.v2ex.com/"
V2EX_MISSION_URL = urljoin(V2EX_HOME_URL, "mission/daily")
V2EX_BALANCE_URL = urljoin(V2EX_HOME_URL, "balance")
DEFAULT_IMPERSONATE = os.environ.get("IMPERSONATE_VERSION", "chrome136").strip() or "chrome136"


class V2EXDailyMission:
    def __init__(self, cookie_str: str, notifier: Optional[NotificationManager] = None):
        self.cookie_str = cookie_str.strip()
        self.notifier = notifier or NotificationManager()
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
        self.username = ""
        self.last_detail = ""
        self.sync_session_from_cookie_string(self.cookie_str)

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> List[Dict[str, str]]:
        cookies: List[Dict[str, str]] = []
        for part in cookie_str.strip().split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".v2ex.com",
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

    def get(self, url: str, referer: str = ""):
        headers = {}
        if referer:
            headers["Referer"] = referer
        return self.session.get(
            url,
            headers=headers,
            impersonate=DEFAULT_IMPERSONATE,
            timeout=20,
            allow_redirects=True,
        )

    def update_username(self, soup: BeautifulSoup) -> None:
        selectors = [
            "#Top a[href^='/member/']",
            "#Rightbar a[href^='/member/']",
            "a[href^='/member/']",
        ]
        for selector in selectors:
            element = soup.select_one(selector)
            if not element:
                continue
            href = (element.get("href") or "").strip()
            text = element.get_text(strip=True)
            username = text or href.rstrip("/").split("/")[-1]
            if username:
                self.username = username.lstrip("@")
                return

    @staticmethod
    def parse_numeric_amount(text: str) -> Optional[int]:
        matches = re.findall(r"[-+]?\d[\d,]*", text or "")
        if not matches:
            return None
        value_text = matches[-1].replace(",", "")
        try:
            return int(value_text)
        except ValueError:
            return None

    def today_key(self) -> str:
        timezone_name = getattr(self.notifier, "notify_timezone", "") or os.environ.get("TZ") or "Asia/Shanghai"
        try:
            if ZoneInfo is not None:
                return datetime.now(ZoneInfo(timezone_name)).strftime("%Y%m%d")
        except Exception:
            pass
        return datetime.now().strftime("%Y%m%d")

    @staticmethod
    def extract_currency_breakdown(fragment_html: str) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        currency_map = {
            "G": "gold",
            "S": "silver",
            "B": "bronze",
        }
        for alt, key in currency_map.items():
            match = re.search(
                rf"(\d[\d,]*)\s*<img[^>]+alt=[\"']{alt}[\"']",
                fragment_html or "",
                re.IGNORECASE,
            )
            if not match:
                continue
            try:
                breakdown[key] = int(match.group(1).replace(",", ""))
            except ValueError:
                continue
        return breakdown

    @staticmethod
    def format_currency_breakdown(breakdown: Dict[str, int]) -> Optional[str]:
        if not breakdown:
            return None

        labels = [
            ("gold", "金币"),
            ("silver", "银币"),
            ("bronze", "铜币"),
        ]
        parts = []
        for key, label in labels:
            value = breakdown.get(key)
            if value is None:
                continue
            if value > 0:
                parts.append(f"{value} {label}")

        if not parts:
            for key, label in labels:
                value = breakdown.get(key)
                if value is not None:
                    parts.append(f"{value} {label}")

        return " ".join(parts) if parts else None

    def extract_current_balance_text(self, soup: BeautifulSoup) -> Optional[str]:
        candidate_selectors = [
            ".balance_area.bigger",
            "#money .balance_area",
            "a.balance_area[href*='/balance']",
        ]
        seen_html = set()
        for selector in candidate_selectors:
            for element in soup.select(selector):
                fragment_html = str(element)
                if fragment_html in seen_html:
                    continue
                seen_html.add(fragment_html)
                balance_text = self.format_currency_breakdown(
                    self.extract_currency_breakdown(fragment_html)
                )
                if balance_text:
                    return balance_text

        label = soup.find(string=lambda text: text and "当前账户余额" in text)
        if label:
            container = getattr(label, "parent", None)
            if container is not None:
                balance_element = container.find_next(
                    "div",
                    class_=lambda value: value and "balance_area" in value,
                )
                if balance_element is not None:
                    return self.format_currency_breakdown(
                        self.extract_currency_breakdown(str(balance_element))
                    )

        return None

    def parse_balance_page(
        self, html: str
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        soup = BeautifulSoup(html, "html.parser")
        self.update_username(soup)
        current_balance = self.extract_current_balance_text(soup)
        rows = soup.select("table.data tr")
        today_reward = None
        row_snapshots: List[str] = []

        for row in rows[1:]:
            cell_texts = [td.get_text(" ", strip=True) for td in row.select("td")]
            if not cell_texts:
                continue

            row_text = " | ".join(text for text in cell_texts if text)
            if row_text:
                row_snapshots.append(row_text)

            if today_reward is None and self.today_key() in row_text:
                reward_candidate = self.parse_numeric_amount(cell_texts[-1])
                if reward_candidate is None:
                    reward_candidate = self.parse_numeric_amount(row_text)
                if reward_candidate is not None:
                    today_reward = reward_candidate

        if current_balance is None and row_snapshots:
            return (
                today_reward,
                None,
                f"Balance rows were parsed but current balance was not found: {row_snapshots[0][:200]}",
            )

        if current_balance is None and today_reward is None:
            return None, None, "Balance page did not contain usable numeric data."

        return today_reward, current_balance, None

    def get_balance_summary(self) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        try:
            response = self.get(V2EX_BALANCE_URL, referer=V2EX_MISSION_URL)
        except Exception as e:
            return None, None, f"Failed to open the V2EX balance page: {e}"

        if response.status_code != 200 or "/signin" in response.url:
            return None, None, "Balance page is not accessible with the current cookie."

        today_reward, current_balance, error = self.parse_balance_page(response.text)
        if error:
            snippet = " ".join(response.text.split())[:200]
            logger.warning(
                f"V2EX balance parsing issue: {error}; snippet={snippet}"
            )
        else:
            logger.info(
                f"V2EX balance summary: today_reward={today_reward}, current_balance={current_balance}"
            )
        return today_reward, current_balance, error

    def extract_redeem_url(self, content: str) -> Optional[str]:
        patterns = [
            r"location\.href\s*=\s*['\"]([^'\"]*/mission/daily/redeem\?once=[^'\"]+)['\"]",
            r"(/mission/daily/redeem\?once=[^'\"<>\s]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return urljoin(V2EX_HOME_URL, match.group(1))
        return None

    def parse_mission_page(self, html: str, page_url: str) -> Tuple[str, str, Optional[str]]:
        soup = BeautifulSoup(html, "html.parser")
        self.update_username(soup)
        text = soup.get_text("\n", strip=True)
        compact_text = " ".join(text.split())
        self.last_detail = compact_text[:200]

        auth_markers = [
            "\u4f60\u8981\u67e5\u770b\u7684\u9875\u9762\u9700\u8981\u5148\u767b\u5f55",
            "\u8bf7\u5148\u767b\u5f55",
            "signin",
        ]
        if "/signin" in page_url or any(marker in compact_text.lower() for marker in ["signin"]):
            return "auth_required", "Cookie may be invalid; please refresh V2EX cookie.", None
        if any(marker in compact_text for marker in auth_markers[:-1]):
            return "auth_required", "Cookie may be invalid; please refresh V2EX cookie.", None

        blocked_markers = [
            "\u4f60\u662f\u673a\u5668\u4eba\u5417",
            "\u9a8c\u8bc1\u7801",
            "captcha",
        ]
        if any(marker.lower() in compact_text.lower() for marker in blocked_markers):
            return "blocked", "V2EX returned a verification page and needs manual attention.", None

        action_candidates = soup.select(
            "input.super.normal.button, button.super.normal.button, a.super.normal.button"
        )
        for element in action_candidates:
            target = (element.get("onclick") or "").strip()
            if not target:
                target = (element.get("href") or "").strip()
            label = (element.get("value") or element.get_text(strip=True) or "").strip()

            redeem_url = self.extract_redeem_url(target)
            if redeem_url:
                return "claimable", label or "Redeem entry found", redeem_url

            if "/balance" in target:
                return "already_done", label or "Daily reward already claimed", None

        redeem_url = self.extract_redeem_url(html)
        if redeem_url:
            return "claimable", "Redeem entry found", redeem_url

        already_markers = [
            "\u6bcf\u65e5\u767b\u5f55\u5956\u52b1\u5df2\u9886\u53d6",
            "\u4eca\u65e5\u767b\u5f55\u5956\u52b1\u5df2\u9886\u53d6",
            "\u4eca\u65e5\u5956\u52b1\u5df2\u7ecf\u9886\u53d6",
            "\u67e5\u770b\u6211\u7684\u8d26\u6237\u4f59\u989d",
            "balance",
        ]
        if any(marker.lower() in compact_text.lower() for marker in already_markers):
            return "already_done", "Daily reward already claimed", None

        return "unknown", compact_text[:200] or "Mission page could not be parsed.", None

    def verify_after_claim(self) -> Tuple[bool, str]:
        try:
            response = self.get(V2EX_MISSION_URL, referer=V2EX_MISSION_URL)
        except Exception as e:
            return False, f"Failed to re-check mission page: {e}"

        status, detail, _ = self.parse_mission_page(response.text, response.url)
        if status == "already_done":
            return True, detail or "Daily reward already claimed"

        try:
            balance_response = self.get(V2EX_BALANCE_URL, referer=V2EX_MISSION_URL)
            if balance_response.status_code == 200 and "/signin" not in balance_response.url:
                return True, "Reward claimed and balance page is accessible"
        except Exception:
            pass

        return False, detail or "Could not confirm final claim state."

    def send_success_notification(self, detail: str) -> None:
        today_reward, current_balance, _ = self.get_balance_summary()
        logger.info(
            f"V2EX notification summary: account={self.username or 'unknown'}, "
            f"today_reward={today_reward}, current_balance={current_balance}"
        )
        lines = [
            "✅ V2EX daily mission completed",
            f"Account: {self.username or 'unknown'}",
            f"Result: {detail}",
        ]
        if today_reward is not None:
            lines.append(f"Today's reward: {today_reward} 铜币")
        if current_balance is not None:
            lines.append(f"Current balance: {current_balance}")
        self.notifier.send_all("V2EX", "\n".join(lines))

    def send_failure_notification(self, detail: str) -> None:
        lines = [
            "❌ V2EX daily mission failed",
            f"Account: {self.username or 'unknown'}",
            f"Reason: {detail}",
        ]
        self.notifier.send_all("V2EX", "\n".join(lines))

    def run(self) -> bool:
        logger.info("Starting V2EX daily mission...")
        if not self.cookie_str:
            logger.warning("V2EX cookie is not configured; skipping V2EX daily mission.")
            return False

        try:
            response = self.get(V2EX_MISSION_URL, referer=V2EX_HOME_URL)
        except Exception as e:
            detail = f"Failed to open the V2EX mission page: {e}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        if response.status_code != 200:
            detail = f"Failed to open the V2EX mission page: HTTP {response.status_code}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        status, detail, redeem_url = self.parse_mission_page(response.text, response.url)
        logger.info(f"V2EX mission page status: {status} - {detail}")

        if status in {"auth_required", "blocked"}:
            self.send_failure_notification(detail)
            return False

        if status == "already_done":
            logger.info("V2EX daily reward has already been claimed today.")
            self.send_success_notification(detail)
            return True

        if status != "claimable" or not redeem_url:
            detail = f"Redeem entry was not found: {detail}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        logger.info(f"Redeem URL found, requesting: {redeem_url}")
        try:
            claim_response = self.get(redeem_url, referer=V2EX_MISSION_URL)
        except Exception as e:
            detail = f"Failed to open the redeem URL: {e}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        if claim_response.status_code != 200:
            detail = f"Failed to open the redeem URL: HTTP {claim_response.status_code}"
            logger.error(detail)
            self.send_failure_notification(detail)
            return False

        verified, verify_detail = self.verify_after_claim()
        if not verified:
            logger.error(f"Failed to verify the V2EX claim result: {verify_detail}")
            self.send_failure_notification(verify_detail)
            return False

        logger.success(f"V2EX daily mission succeeded: {verify_detail}")
        self.send_success_notification(verify_detail)
        return True
