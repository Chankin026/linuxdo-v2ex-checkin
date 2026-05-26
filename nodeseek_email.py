import email
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional


IMAP_HOST_BY_DOMAIN = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "qq.com": "imap.qq.com",
    "foxmail.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "yeah.net": "imap.yeah.net",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "msn.com": "outlook.office365.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.com.cn": "imap.mail.yahoo.com",
}


@dataclass
class EmailVerificationMessage:
    received_at: Optional[datetime]
    sender: str
    subject: str
    body: str


def infer_imap_host(email_address: str) -> str:
    if not email_address or "@" not in email_address:
        return ""
    domain = email_address.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return ""
    return IMAP_HOST_BY_DOMAIN.get(domain, f"imap.{domain}")


def decode_mime_header(value: str) -> str:
    if not value:
        return ""

    parts = []
    for raw_part, charset in decode_header(value):
        if isinstance(raw_part, bytes):
            encoding = charset or "utf-8"
            try:
                parts.append(raw_part.decode(encoding, errors="replace"))
            except LookupError:
                parts.append(raw_part.decode("utf-8", errors="replace"))
        else:
            parts.append(raw_part)
    return "".join(parts).strip()


def parse_email_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def extract_plain_text(message: Message) -> str:
    if message.is_multipart():
        parts: List[str] = []
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if content_type != "text/plain" or "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                parts.append(payload.decode(charset, errors="replace"))
            except LookupError:
                parts.append(payload.decode("utf-8", errors="replace"))
        if parts:
            return "\n".join(parts)

        for part in message.walk():
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if content_type != "text/html" or "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                html = payload.decode(charset, errors="replace")
            except LookupError:
                html = payload.decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html)
        return ""

    payload = message.get_payload(decode=True)
    if payload is None:
        text = message.get_payload()
        return text if isinstance(text, str) else ""
    charset = message.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def parse_raw_email(raw: bytes) -> EmailVerificationMessage:
    message = email.message_from_bytes(raw)
    return EmailVerificationMessage(
        received_at=parse_email_datetime(message.get("Date", "")),
        sender=decode_mime_header(message.get("From", "")),
        subject=decode_mime_header(message.get("Subject", "")),
        body=extract_plain_text(message),
    )


def extract_verification_code(text: str) -> Optional[str]:
    if not text:
        return None

    keyword_pattern = re.compile(
        r"(?:验证码|驗證碼|校验码|动态验证|validation\s*code|verification\s*code|"
        r"login\s*code|code)[^A-Za-z0-9]{0,30}([A-Za-z0-9]{4,64})",
        re.IGNORECASE,
    )
    match = keyword_pattern.search(text)
    if match:
        return match.group(1)

    fallback = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if fallback:
        return fallback.group(1)
    return None


def message_looks_like_nodeseek(message: EmailVerificationMessage) -> bool:
    haystack = "\n".join(
        [
            message.sender or "",
            message.subject or "",
            message.body or "",
        ]
    ).lower()
    return "nodeseek" in haystack or "node seek" in haystack


def find_latest_verification_code(
    messages: Iterable[EmailVerificationMessage],
    not_before: Optional[datetime] = None,
) -> Optional[str]:
    threshold = normalize_datetime(not_before)
    candidates = []
    for message in messages:
        received_at = normalize_datetime(message.received_at)
        if threshold and received_at and received_at < threshold:
            continue
        if not message_looks_like_nodeseek(message):
            continue
        code = extract_verification_code("\n".join([message.subject, message.body]))
        if not code:
            continue
        candidates.append((received_at or datetime.min.replace(tzinfo=timezone.utc), code))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


class ImapEmailCodeFetcher:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 993,
        mailbox: str = "INBOX",
        timeout: int = 300,
        poll_interval: int = 10,
    ):
        self.host = host.strip()
        self.username = username.strip()
        self.password = password
        self.port = int(port or 993)
        self.mailbox = mailbox.strip() or "INBOX"
        self.timeout = int(timeout or 300)
        self.poll_interval = int(poll_interval or 10)

    def wait_for_code(
        self,
        *,
        email_address: str,
        not_before: Optional[datetime] = None,
    ) -> Optional[str]:
        deadline = time.monotonic() + max(1, self.timeout)
        while time.monotonic() <= deadline:
            messages = self.fetch_recent_messages(limit=30)
            code = find_latest_verification_code(messages, not_before=not_before)
            if code:
                return code
            time.sleep(max(1, self.poll_interval))
        return None

    def fetch_recent_messages(self, limit: int = 30) -> List[EmailVerificationMessage]:
        with imaplib.IMAP4_SSL(self.host, self.port) as client:
            client.login(self.username, self.password)
            client.select(self.mailbox, readonly=True)
            status, data = client.search(None, "ALL")
            if status != "OK" or not data:
                return []

            message_ids = data[0].split()
            recent_ids = list(reversed(message_ids[-limit:]))
            messages: List[EmailVerificationMessage] = []
            for message_id in recent_ids:
                status, fetched = client.fetch(message_id, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                for item in fetched:
                    if not isinstance(item, tuple) or len(item) < 2:
                        continue
                    messages.append(parse_raw_email(item[1]))
            return messages
