"""
Notification helpers.
"""

import os
import random
import re
import time
from datetime import datetime

from curl_cffi import requests
from loguru import logger

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


class NotificationManager:
    def __init__(self):
        self.gotify_url = os.environ.get("GOTIFY_URL")
        self.gotify_token = os.environ.get("GOTIFY_TOKEN")
        self.sc3_push_key = os.environ.get("SC3_PUSH_KEY")
        self.wxpush_url = os.environ.get("WXPUSH_URL")
        self.wxpush_token = os.environ.get("WXPUSH_TOKEN")
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.notify_timezone = (
            os.environ.get("NOTIFY_TIMEZONE")
            or os.environ.get("TZ")
            or "Asia/Shanghai"
        )

    def send_all(self, title: str, message: str):
        title, message = self.attach_date(title, message)
        self.send_gotify(title, message)
        self.send_server_chan(title, message)
        self.send_wxpush(title, message)
        self.send_telegram(title, message)

    def format_now(self) -> str:
        try:
            if ZoneInfo is not None:
                current = datetime.now(ZoneInfo(self.notify_timezone))
            else:
                current = datetime.now().astimezone()
            return current.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def attach_date(self, title: str, message: str):
        now_text = self.format_now()
        dated_title = f"{title} [{now_text[:10]}]"
        if message.startswith("Date: ") or message.startswith("日期: "):
            return dated_title, message
        dated_message = f"日期: {now_text}\n{message}"
        return dated_title, dated_message

    def send_gotify(self, title: str, message: str):
        if not self.gotify_url or not self.gotify_token:
            return False

        try:
            response = requests.post(
                f"{self.gotify_url}/message",
                params={"token": self.gotify_token},
                json={"title": title, "message": message, "priority": 1},
                timeout=10,
            )
            response.raise_for_status()
            logger.success("Gotify 推送成功")
            return True
        except Exception as exc:
            logger.error(f"Gotify 推送失败: {exc}")
            return False

    def send_server_chan(self, title: str, message: str):
        if not self.sc3_push_key:
            return False

        match = re.match(r"sct(\d+)t", self.sc3_push_key, re.I)
        if not match:
            logger.error("SC3_PUSH_KEY 格式错误，无法解析 ServerChan UID")
            return False

        uid = match.group(1)
        url = f"https://{uid}.push.ft07.com/send/{self.sc3_push_key}"
        params = {"title": title, "desp": message}

        attempts = 5
        for attempt in range(attempts):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                logger.success(f"ServerChan 推送成功: {response.text}")
                return True
            except Exception as exc:
                logger.error(f"ServerChan 推送失败: {exc}")
                if attempt < attempts - 1:
                    sleep_time = random.randint(180, 360)
                    logger.info(f"{sleep_time} 秒后重试 ServerChan 推送")
                    time.sleep(sleep_time)

        return False

    def send_wxpush(self, title: str, message: str):
        if not self.wxpush_url or not self.wxpush_token:
            return False

        try:
            response = requests.post(
                f"{self.wxpush_url}/wxsend",
                headers={
                    "Authorization": self.wxpush_token,
                    "Content-Type": "application/json",
                },
                json={"title": title, "content": message},
                timeout=10,
            )
            response.raise_for_status()
            logger.success(f"wxpush 推送成功: {response.text}")
            return True
        except Exception as exc:
            logger.error(f"wxpush 推送失败: {exc}")
            return False

    def send_telegram(self, title: str, message: str):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return False

        try:
            telegram_url = (
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            )
            response = requests.post(
                telegram_url,
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": f"{title}\n\n{message}",
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            response.raise_for_status()
            logger.success("Telegram 推送成功")
            return True
        except Exception as exc:
            logger.error(f"Telegram 推送失败: {exc}")
            return False
