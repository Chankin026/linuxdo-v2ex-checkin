"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import importlib
import os
import re
from typing import Dict, List, Optional, Set

from loguru import logger
from nodeseek import NodeSeekDailyMission
from notify import NotificationManager
from v2ex import V2EXDailyMission


def load_env_file(path: str, override: bool = False) -> bool:
    if not path or not os.path.isfile(path):
        return False

    loaded_any = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue

                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]

                current_value = os.environ.get(key, "")
                if override or not (
                    isinstance(current_value, str) and current_value.strip()
                ):
                    os.environ[key] = value
                    loaded_any = True
    except OSError as exc:
        logger.warning(f"Failed to load env file {path}: {exc}")
        return False

    return loaded_any


def preload_env_files() -> List[str]:
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    candidates: List[str] = []
    env_file_hint = os.environ.get("LINUXDO_ENV_FILE", "").strip()
    if env_file_hint:
        candidates.append(env_file_hint)

    candidates.extend(
        [
            os.path.join(repo_dir, "linuxdo-v2ex-checkin.env"),
            os.path.join(repo_dir, ".env"),
            "/etc/linuxdo-v2ex-checkin.env",
        ]
    )

    loaded_paths: List[str] = []
    seen_paths: Set[str] = set()
    for candidate in candidates:
        normalized = os.path.abspath(candidate)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        if load_env_file(normalized):
            loaded_paths.append(normalized)

    return loaded_paths


def resolve_default_env_file_path() -> str:
    candidates = ["/etc/linuxdo-v2ex-checkin.env"]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"环境变量 {name} 不是有效整数: {value!r}，将回退到 {default}")
        return default


PRELOADED_ENV_FILES = preload_env_files()
if PRELOADED_ENV_FILES:
    logger.info("Preloaded env file(s): " + ", ".join(PRELOADED_ENV_FILES))


ENV_FILE_PATH = env_str("LINUXDO_ENV_FILE", resolve_default_env_file_path())
USERNAME = env_str("LINUXDO_USERNAME") or env_str("USERNAME")
PASSWORD = env_str("LINUXDO_PASSWORD") or env_str("PASSWORD")
COOKIES = env_str("LINUXDO_COOKIES")
DEFAULT_IMPERSONATE = env_str("IMPERSONATE_VERSION", "chrome136") or "chrome136"

V2EX_COOKIE = env_str("V2EX_COOKIE") or env_str("V2EX_COOKIES")
V2EX_A2 = env_str("V2EX_A2")
if not V2EX_COOKIE and V2EX_A2:
    V2EX_COOKIE = f"A2={V2EX_A2}"
V2EX_ENABLED = env_bool("V2EX_ENABLED", bool(V2EX_COOKIE))

DEFAULT_SOLVER_TYPE = (env_str("SOLVER_TYPE") or "").strip().lower()
DEFAULT_YESCAPTCHA_CLIENT_KEY = (
    env_str("CLIENTT_KEY") or env_str("YESCAPTCHA_CLIENT_KEY")
)
DEFAULT_YESCAPTCHA_API_BASE_URL = (
    env_str("YESCAPTCHA_API_BASE_URL")
    or env_str("API_BASE_URL", "https://api.yescaptcha.com")
)
DEFAULT_YESCAPTCHA_ADVANCED = env_bool("YESCAPTCHA_ADVANCED", False)
if not DEFAULT_SOLVER_TYPE and DEFAULT_YESCAPTCHA_CLIENT_KEY:
    DEFAULT_SOLVER_TYPE = "yescaptcha"

NODESEEK_NAME = env_str("NODESEEK_NAME")
NODESEEK_COOKIE = env_str("NODESEEK_COOKIE") or env_str("NS_COOKIE")
NODESEEK_USERNAME = env_str("NODESEEK_USERNAME")
NODESEEK_PASSWORD = env_str("NODESEEK_PASSWORD")
NODESEEK_EMAIL = env_str("NODESEEK_EMAIL")
NODESEEK_EMAIL_IMAP_HOST = env_str("NODESEEK_EMAIL_IMAP_HOST")
NODESEEK_EMAIL_IMAP_PORT = env_int("NODESEEK_EMAIL_IMAP_PORT", 993)
NODESEEK_EMAIL_IMAP_USERNAME = env_str("NODESEEK_EMAIL_IMAP_USERNAME")
NODESEEK_EMAIL_IMAP_PASSWORD = env_str("NODESEEK_EMAIL_IMAP_PASSWORD")
NODESEEK_EMAIL_IMAP_MAILBOX = env_str("NODESEEK_EMAIL_IMAP_MAILBOX", "INBOX")
NODESEEK_EMAIL_CODE_TIMEOUT = env_int("NODESEEK_EMAIL_CODE_TIMEOUT", 300)
NODESEEK_EMAIL_CODE_POLL_INTERVAL = env_int("NODESEEK_EMAIL_CODE_POLL_INTERVAL", 10)
NODESEEK_RANDOM = env_bool("NODESEEK_RANDOM", env_bool("NS_RANDOM", True))
NODESEEK_SOLVER_TYPE = (
    env_str("NODESEEK_SOLVER_TYPE") or DEFAULT_SOLVER_TYPE or ""
).strip().lower()
NODESEEK_CLIENT_KEY = DEFAULT_YESCAPTCHA_CLIENT_KEY
NODESEEK_YESCAPTCHA_API_BASE_URL = (
    env_str("NODESEEK_YESCAPTCHA_API_BASE_URL")
    or DEFAULT_YESCAPTCHA_API_BASE_URL
)
NODESEEK_YESCAPTCHA_ADVANCED = env_bool(
    "NODESEEK_YESCAPTCHA_ADVANCED",
    DEFAULT_YESCAPTCHA_ADVANCED,
)
NODESEEK_IMPERSONATE = (
    env_str("NODESEEK_IMPERSONATE")
    or env_str("NS_IMPERSONATE")
    or DEFAULT_IMPERSONATE
)

XIAOHEIHE_COOKIE = env_str("XIAOHEIHE_COOKIE") or env_str("XIAOHEIHE_COOKIES")
XIAOHEIHE_ENABLED = env_bool("XIAOHEIHE_ENABLED", bool(XIAOHEIHE_COOKIE))
XIAOHEIHE_ACCOUNT_NAME = env_str("XIAOHEIHE_ACCOUNT_NAME")
XIAOHEIHE_HEADERS_JSON = env_str("XIAOHEIHE_HEADERS_JSON")
XIAOHEIHE_REQUEST_MODE = env_str("XIAOHEIHE_REQUEST_MODE", "signer") or "signer"
XIAOHEIHE_TIMEOUT = env_int("XIAOHEIHE_TIMEOUT", 20)
XIAOHEIHE_RETRY_TIMES = env_int("XIAOHEIHE_RETRY_TIMES", 6)
XIAOHEIHE_RETRY_MIN_DELAY = env_int("XIAOHEIHE_RETRY_MIN_DELAY", 3)
XIAOHEIHE_RETRY_MAX_DELAY = env_int("XIAOHEIHE_RETRY_MAX_DELAY", 12)
XIAOHEIHE_IMPERSONATE = (
    env_str("XIAOHEIHE_IMPERSONATE", DEFAULT_IMPERSONATE) or DEFAULT_IMPERSONATE
)

NODESEEK_INDEXED_ENV_PATTERN = re.compile(
    r"^(?:"
    r"NODESEEK_(?:COOKIE|USERNAME|PASSWORD|NAME|EMAIL|EMAIL_IMAP_HOST|"
    r"EMAIL_IMAP_PORT|EMAIL_IMAP_USERNAME|EMAIL_IMAP_PASSWORD|EMAIL_IMAP_MAILBOX|"
    r"EMAIL_CODE_TIMEOUT|EMAIL_CODE_POLL_INTERVAL|RANDOM|IMPERSONATE|SOLVER_TYPE|"
    r"YESCAPTCHA_API_BASE_URL|YESCAPTCHA_ADVANCED)"
    r"|NS_(?:COOKIE|RANDOM|IMPERSONATE)"
    r")_(\d+)$"
)


def indexed_env_name(base_name: str, index: Optional[int] = None) -> str:
    if index is None:
        return base_name
    return f"{base_name}_{index}"


def indexed_env_str(
    base_name: str,
    index: Optional[int] = None,
    aliases: Optional[List[str]] = None,
    default: str = "",
) -> str:
    for name in [base_name] + (aliases or []):
        value = env_str(indexed_env_name(name, index))
        if value:
            return value
    return default


def indexed_env_bool(
    base_name: str,
    index: Optional[int] = None,
    default: bool = False,
    aliases: Optional[List[str]] = None,
) -> bool:
    for name in [base_name] + (aliases or []):
        raw = os.environ.get(indexed_env_name(name, index))
        if raw is None:
            continue
        if not isinstance(raw, str):
            return bool(raw)
        value = raw.strip()
        if not value:
            continue
        return value.lower() in {"1", "true", "yes", "on"}
    return default


def indexed_env_int(
    base_name: str,
    index: Optional[int] = None,
    default: int = 0,
    aliases: Optional[List[str]] = None,
) -> int:
    for name in [base_name] + (aliases or []):
        value = env_str(indexed_env_name(name, index))
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            logger.warning(
                f"环境变量 {indexed_env_name(name, index)} 不是有效整数: "
                f"{value!r}，将回退到 {default}"
            )
            return default
    return default


def indexed_env_name_with_value(
    base_name: str,
    index: Optional[int] = None,
    aliases: Optional[List[str]] = None,
) -> str:
    for name in [base_name] + (aliases or []):
        candidate = indexed_env_name(name, index)
        if env_str(candidate):
            return candidate
    return indexed_env_name(base_name, index)


def collect_nodeseek_account_indexes() -> List[int]:
    indexes: Set[int] = set()
    for key, value in os.environ.items():
        if not isinstance(value, str) or not value.strip():
            continue
        match = NODESEEK_INDEXED_ENV_PATTERN.match(key)
        if match:
            indexes.add(int(match.group(1)))
    return sorted(indexes)


def build_nodeseek_account_config(index: Optional[int] = None) -> Optional[Dict[str, object]]:
    cookie = indexed_env_str(
        "NODESEEK_COOKIE",
        index,
        aliases=["NS_COOKIE"],
        default=NODESEEK_COOKIE if index is None else "",
    )
    username = indexed_env_str(
        "NODESEEK_USERNAME",
        index,
        default=NODESEEK_USERNAME if index is None else "",
    )
    password = indexed_env_str(
        "NODESEEK_PASSWORD",
        index,
        default=NODESEEK_PASSWORD if index is None else "",
    )

    if not cookie and not (username and password):
        return None

    solver_type = indexed_env_str(
        "NODESEEK_SOLVER_TYPE",
        index,
        default=NODESEEK_SOLVER_TYPE,
    ).strip().lower()
    yescaptcha_client_key = NODESEEK_CLIENT_KEY
    if not solver_type and yescaptcha_client_key:
        solver_type = "yescaptcha"

    account_name = indexed_env_str("NODESEEK_NAME", index, default="")
    if not account_name:
        if username:
            account_name = username
        elif index is not None:
            account_name = f"Account #{index}"
        else:
            account_name = "Default account"

    return {
        "index": index,
        "account_name": account_name,
        "cookie_str": cookie,
        "cookie_env_var_name": indexed_env_name_with_value(
            "NODESEEK_COOKIE",
            index,
            aliases=["NS_COOKIE"],
        ),
        "username": username,
        "password": password,
        "email_address": indexed_env_str(
            "NODESEEK_EMAIL",
            index,
            default=NODESEEK_EMAIL,
        ),
        "email_imap_host": indexed_env_str(
            "NODESEEK_EMAIL_IMAP_HOST",
            index,
            default=NODESEEK_EMAIL_IMAP_HOST,
        ),
        "email_imap_port": indexed_env_int(
            "NODESEEK_EMAIL_IMAP_PORT",
            index,
            default=NODESEEK_EMAIL_IMAP_PORT,
        ),
        "email_imap_username": indexed_env_str(
            "NODESEEK_EMAIL_IMAP_USERNAME",
            index,
            default=NODESEEK_EMAIL_IMAP_USERNAME,
        ),
        "email_imap_password": indexed_env_str(
            "NODESEEK_EMAIL_IMAP_PASSWORD",
            index,
            default=NODESEEK_EMAIL_IMAP_PASSWORD,
        ),
        "email_imap_mailbox": indexed_env_str(
            "NODESEEK_EMAIL_IMAP_MAILBOX",
            index,
            default=NODESEEK_EMAIL_IMAP_MAILBOX,
        ),
        "email_code_timeout": indexed_env_int(
            "NODESEEK_EMAIL_CODE_TIMEOUT",
            index,
            default=NODESEEK_EMAIL_CODE_TIMEOUT,
        ),
        "email_code_poll_interval": indexed_env_int(
            "NODESEEK_EMAIL_CODE_POLL_INTERVAL",
            index,
            default=NODESEEK_EMAIL_CODE_POLL_INTERVAL,
        ),
        "solver_type": solver_type,
        "yescaptcha_client_key": yescaptcha_client_key,
        "yescaptcha_api_base_url": indexed_env_str(
            "NODESEEK_YESCAPTCHA_API_BASE_URL",
            index,
            default=NODESEEK_YESCAPTCHA_API_BASE_URL,
        ),
        "yescaptcha_advanced": indexed_env_bool(
            "NODESEEK_YESCAPTCHA_ADVANCED",
            index,
            default=NODESEEK_YESCAPTCHA_ADVANCED,
        ),
        "attendance_random": indexed_env_bool(
            "NODESEEK_RANDOM",
            index,
            default=NODESEEK_RANDOM,
            aliases=["NS_RANDOM"],
        ),
        "impersonate": indexed_env_str(
            "NODESEEK_IMPERSONATE",
            index,
            aliases=["NS_IMPERSONATE"],
            default=NODESEEK_IMPERSONATE,
        ),
    }


def collect_nodeseek_accounts() -> List[Dict[str, object]]:
    indexed_accounts = collect_nodeseek_account_indexes()
    accounts: List[Dict[str, object]] = []

    if indexed_accounts:
        for index in indexed_accounts:
            config = build_nodeseek_account_config(index)
            if config:
                accounts.append(config)
            else:
                logger.warning(
                    f"Skipping NodeSeek account #{index}: missing cookie or username/password"
                )
        return accounts

    single_account = build_nodeseek_account_config()
    if single_account:
        accounts.append(single_account)
    return accounts


NODESEEK_ENABLED = env_bool("NODESEEK_ENABLED", bool(collect_nodeseek_accounts()))


def has_linuxdo_credentials() -> bool:
    return bool(COOKIES or (USERNAME and PASSWORD))


def load_linuxdo_cloak_module():
    return importlib.import_module("linuxdo_cloak")


def load_xiaoheihe_module():
    return importlib.import_module("xiaoheihe")


def log_xiaoheihe_mode(mode: str, adb_serial: str = "") -> None:
    lowered = (mode or "").strip().lower()
    try:
        label = load_xiaoheihe_module().resolve_request_mode_label(lowered)
    except Exception:
        label = lowered or "signer"
    logger.info(f"Xiaoheihe mode: {label}")


def run_configured_tasks() -> None:
    linuxdo_enabled = has_linuxdo_credentials()
    has_v2ex_credentials = bool(V2EX_ENABLED and V2EX_COOKIE)
    nodeseek_accounts = collect_nodeseek_accounts() if NODESEEK_ENABLED else []
    has_nodeseek_credentials = bool(NODESEEK_ENABLED and nodeseek_accounts)
    has_xiaoheihe_credentials = bool(XIAOHEIHE_ENABLED and XIAOHEIHE_COOKIE)

    logger.info(
        "Runtime task summary: "
        f"linuxdo={linuxdo_enabled}, "
        f"v2ex={has_v2ex_credentials}, "
        f"xiaoheihe={has_xiaoheihe_credentials}, "
        f"xiaoheihe_mode={XIAOHEIHE_REQUEST_MODE}, "
        f"nodeseek_enabled={NODESEEK_ENABLED}, "
        f"nodeseek_accounts={len(nodeseek_accounts)}"
    )
    if nodeseek_accounts:
        logger.info(
            "NodeSeek accounts: "
            + ", ".join(str(account["account_name"]) for account in nodeseek_accounts)
        )

    if (
        not linuxdo_enabled
        and not has_v2ex_credentials
        and not has_xiaoheihe_credentials
        and not has_nodeseek_credentials
    ):
        print(
            "请设置 LINUXDO_COOKIES 或 LINUXDO_USERNAME / LINUXDO_PASSWORD；"
            "如需启用 V2EX，请设置 V2EX_COOKIE 或 V2EX_A2；"
            "如需启用小黑盒，请设置 XIAOHEIHE_COOKIE；"
            "如需启用 NodeSeek，请设置 NODESEEK_COOKIE 或 NODESEEK_USERNAME / NODESEEK_PASSWORD"
        )
        raise SystemExit(1)

    if has_v2ex_credentials:
        V2EXDailyMission(V2EX_COOKIE).run()
    elif V2EX_ENABLED:
        logger.info("未配置 V2EX Cookie，跳过 V2EX 每日签到")

    if has_nodeseek_credentials:
        logger.info(f"Configured {len(nodeseek_accounts)} NodeSeek account(s)")
        for account in nodeseek_accounts:
            NodeSeekDailyMission(
                cookie_str=account["cookie_str"],
                username=account["username"],
                password=account["password"],
                env_file_path=ENV_FILE_PATH,
                notifier=NotificationManager(),
                solver_type=account["solver_type"],
                yescaptcha_client_key=account["yescaptcha_client_key"],
                yescaptcha_api_base_url=account["yescaptcha_api_base_url"],
                yescaptcha_advanced=account["yescaptcha_advanced"],
                attendance_random=account["attendance_random"],
                impersonate=account["impersonate"],
                cookie_env_var_name=account["cookie_env_var_name"],
                account_name=account["account_name"],
                email_address=account["email_address"],
                email_imap_host=account["email_imap_host"],
                email_imap_port=account["email_imap_port"],
                email_imap_username=account["email_imap_username"],
                email_imap_password=account["email_imap_password"],
                email_imap_mailbox=account["email_imap_mailbox"],
                email_code_timeout=account["email_code_timeout"],
                email_code_poll_interval=account["email_code_poll_interval"],
            ).run()
    elif NODESEEK_ENABLED:
        logger.info("未配置 NodeSeek 登录信息，跳过 NodeSeek 每日签到")

    if has_xiaoheihe_credentials:
        log_xiaoheihe_mode(XIAOHEIHE_REQUEST_MODE)
        load_xiaoheihe_module().XiaoHeiHeDailyMission(
            notifier=NotificationManager(),
            account_name=XIAOHEIHE_ACCOUNT_NAME,
            cookie=XIAOHEIHE_COOKIE,
            headers_json=XIAOHEIHE_HEADERS_JSON,
            timeout=XIAOHEIHE_TIMEOUT,
            max_retries=XIAOHEIHE_RETRY_TIMES,
            retry_min_delay=XIAOHEIHE_RETRY_MIN_DELAY,
            retry_max_delay=XIAOHEIHE_RETRY_MAX_DELAY,
            impersonate=XIAOHEIHE_IMPERSONATE,
        ).run()
    else:
        logger.info("未配置 Xiaoheihe Cookie，跳过小黑盒每日签到")

    if linuxdo_enabled:
        linuxdo_cloak = load_linuxdo_cloak_module()
        linuxdo_cloak.run_linuxdo_task(headless=False)
    else:
        logger.info("未配置 LinuxDo 登录信息，跳过 LinuxDo 任务")


if __name__ == "__main__":
    run_configured_tasks()
