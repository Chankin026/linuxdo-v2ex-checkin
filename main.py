"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import functools
import hashlib
import json
import os
import random
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from DrissionPage import Chromium, ChromiumOptions
from curl_cffi import requests
from curl_cffi.const import CurlIpResolve, CurlOpt
from loguru import logger
from nodeseek import NodeSeekDailyMission
from notify import NotificationManager
from v2ex import V2EXDailyMission
from xiaoheihe import (
    XIAOHEIHE_REQUEST_MODE_LABELS,
    XiaoHeiHeDailyMission,
    resolve_request_mode_label,
)

try:
    from tabulate import tabulate
except ModuleNotFoundError:
    def tabulate(rows, headers=(), tablefmt="pretty"):
        lines = []
        if headers:
            lines.append(" | ".join(str(item) for item in headers))
        for row in rows:
            lines.append(" | ".join(str(item) for item in row))
        return "\n".join(lines)


def load_env_file(path: str, override: bool = False) -> bool:
    if not path or not os.path.isfile(path):
        return False

    loaded_any = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
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
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ["'", '"']:
                    value = value[1:-1]

                current_value = os.environ.get(key, "")
                if override or not (
                    isinstance(current_value, str) and current_value.strip()
                ):
                    os.environ[key] = value
                    loaded_any = True
    except OSError as e:
        logger.warning(f"Failed to load env file {path}: {e}")
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
    candidates = [
        "/etc/linuxdo-v2ex-checkin.env",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def retry_decorator(retries=3, min_delay=5, max_delay=10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    if attempt < retries - 1:
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(
                            f"将在 {sleep_s:.2f}s 后重试 ({min_delay}-{max_delay}s 随机延迟)"
                        )
                        time.sleep(sleep_s)
            return None

        return wrapper

    return decorator


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name)
    if not value:
        return default
    return value.lower() in ["1", "true", "yes", "on"]


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"环境变量 {name} 不是有效整数: {value!r}，将回退到 {default}")
        return default


def extract_browser_major_version(impersonate: str, default: str = "136") -> str:
    match = re.search(r"(\d{2,3})", impersonate or "")
    if match:
        return match.group(1)
    return default


def build_browser_user_agent(platform_identifier: str, impersonate: str) -> str:
    major = extract_browser_major_version(impersonate)
    lowered = (impersonate or "").strip().lower()
    if lowered.startswith("edge"):
        return (
            "Mozilla/5.0 "
            f"({platform_identifier}) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
        )

    return (
        "Mozilla/5.0 "
        f"({platform_identifier}) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def log_xiaoheihe_mode(mode: str, adb_serial: str = "") -> None:
    lowered = (mode or "").strip().lower()
    label = resolve_request_mode_label(lowered)
    logger.info(f"Xiaoheihe mode: {label}")


PRELOADED_ENV_FILES = preload_env_files()
if PRELOADED_ENV_FILES:
    logger.info("Preloaded env file(s): " + ", ".join(PRELOADED_ENV_FILES))


class YesCaptchaSolverError(Exception):
    pass


class YesCaptchaSolver:
    def __init__(
        self,
        api_base_url: str = "https://api.yescaptcha.com",
        client_key: str = "",
        max_retries: int = 20,
        retry_interval: int = 3,
        timeout: int = 60,
        advanced: bool = False,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.create_task_url = f"{self.api_base_url}/createTask"
        self.get_result_url = f"{self.api_base_url}/getTaskResult"
        self.client_key = client_key
        self.max_retries = max_retries
        self.retry_interval = retry_interval
        self.timeout = timeout
        self.advanced = advanced

    def solve(
        self,
        url: str,
        sitekey: str,
        user_agent: Optional[str] = None,
        verbose: bool = False,
        captcha_type: str = "turnstile",
    ) -> str:
        task_id = self._create_task(url, sitekey, user_agent, verbose, captcha_type)
        if not task_id:
            raise YesCaptchaSolverError("创建 YesCaptcha 任务失败")

        token = self._get_task_result(task_id, verbose, captcha_type)
        if not token:
            raise YesCaptchaSolverError("获取 YesCaptcha 结果失败")
        return token

    def _create_task(
        self,
        url: str,
        sitekey: str,
        user_agent: Optional[str] = None,
        verbose: bool = False,
        captcha_type: str = "turnstile",
    ) -> str:
        if captcha_type == "hcaptcha":
            task_type = "HCaptchaTaskProxyless"
        else:
            task_type = (
                "TurnstileTaskProxylessM1" if self.advanced else "TurnstileTaskProxyless"
            )
        data = {
            "clientKey": self.client_key,
            "task": {
                "type": task_type,
                "websiteURL": url,
                "websiteKey": sitekey,
            },
            "softID": "62709",
        }
        if user_agent:
            data["task"]["userAgent"] = user_agent

        try:
            response = requests.post(
                self.create_task_url,
                json=data,
                timeout=self.timeout,
                impersonate="chrome110",
            )
            result = response.json()
        except Exception as e:
            raise YesCaptchaSolverError(f"创建任务请求失败: {e}") from e

        if result.get("errorId") == 0 and result.get("taskId"):
            if verbose:
                print(f"YesCaptcha task created: {result['taskId']}")
            return result["taskId"]

        raise YesCaptchaSolverError(
            result.get("errorDescription") or "YesCaptcha createTask 返回失败"
        )

    def _get_task_result(
        self,
        task_id: str,
        verbose: bool = False,
        captcha_type: str = "turnstile",
    ) -> str:
        data = {
            "clientKey": self.client_key,
            "taskId": task_id,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    self.get_result_url,
                    json=data,
                    timeout=self.timeout,
                    impersonate="chrome110",
                )
                result = response.json()
            except Exception as e:
                raise YesCaptchaSolverError(f"查询任务结果失败: {e}") from e

            if result.get("errorId", 0) > 0:
                raise YesCaptchaSolverError(
                    result.get("errorDescription")
                    or "YesCaptcha getTaskResult 返回失败"
                )

            if result.get("status") == "ready":
                solution = result.get("solution", {})
                token = solution.get("token") or solution.get("gRecaptchaResponse")
                if token:
                    return token
                raise YesCaptchaSolverError("YesCaptcha 返回 ready 但没有 token")

            if verbose:
                print(f"YesCaptcha processing {attempt}/{self.max_retries}")
            time.sleep(self.retry_interval)

        total_wait = self.max_retries * self.retry_interval
        raise YesCaptchaSolverError(
            "YesCaptcha 获取结果超时 "
            f"(max_retries={self.max_retries}, "
            f"retry_interval={self.retry_interval}s, "
            f"request_timeout={self.timeout}s, "
            f"poll_budget≈{total_wait}s)"
        )


os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = env_str("LINUXDO_USERNAME") or env_str("USERNAME")
PASSWORD = env_str("LINUXDO_PASSWORD") or env_str("PASSWORD")
COOKIES = env_str("LINUXDO_COOKIES")
GH_PAT = env_str("GH_PAT")
ENV_FILE_PATH = env_str("LINUXDO_ENV_FILE", resolve_default_env_file_path())
SOLVER_TYPE = (
    env_str("LINUXDO_SOLVER_TYPE") or env_str("SOLVER_TYPE") or ""
).strip().lower()
YESCAPTCHA_CLIENT_KEY = (
    env_str("CLIENTT_KEY")
    or env_str("LINUXDO_YESCAPTCHA_CLIENT_KEY")
    or env_str("YESCAPTCHA_CLIENT_KEY")
)
YESCAPTCHA_API_BASE_URL = (
    env_str("LINUXDO_YESCAPTCHA_API_BASE_URL")
    or env_str("YESCAPTCHA_API_BASE_URL")
    or env_str("API_BASE_URL", "https://api.yescaptcha.com")
)
YESCAPTCHA_ADVANCED = env_bool(
    "LINUXDO_YESCAPTCHA_ADVANCED",
    env_bool("YESCAPTCHA_ADVANCED", False),
)
YESCAPTCHA_MAX_RETRIES = env_int(
    "LINUXDO_YESCAPTCHA_MAX_RETRIES",
    env_int("YESCAPTCHA_MAX_RETRIES", 20),
)
YESCAPTCHA_RETRY_INTERVAL = env_int(
    "LINUXDO_YESCAPTCHA_RETRY_INTERVAL",
    env_int("YESCAPTCHA_RETRY_INTERVAL", 3),
)
YESCAPTCHA_TIMEOUT = env_int(
    "LINUXDO_YESCAPTCHA_TIMEOUT",
    env_int("YESCAPTCHA_TIMEOUT", 60),
)
YESCAPTCHA_HCAPTCHA_MAX_RETRIES = env_int(
    "LINUXDO_YESCAPTCHA_HCAPTCHA_MAX_RETRIES",
    env_int("YESCAPTCHA_HCAPTCHA_MAX_RETRIES", 15),
)
YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL = env_int(
    "LINUXDO_YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL",
    env_int("YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL", 4),
)
YESCAPTCHA_HCAPTCHA_TIMEOUT = env_int(
    "LINUXDO_YESCAPTCHA_HCAPTCHA_TIMEOUT",
    env_int("YESCAPTCHA_HCAPTCHA_TIMEOUT", 90),
)
# LinuxDo browser now always runs headed, and LinuxDo requests prefer IPv4.
HEADLESS_MODE = False
SKIP_COOKIE_LOGIN = False
FORCE_IPV4 = True
BROWSE_ENABLED = env_str("BROWSE_ENABLED", "true").lower() not in [
    "false",
    "0",
    "off",
]
V2EX_COOKIE = env_str("V2EX_COOKIE") or env_str("V2EX_COOKIES")
V2EX_A2 = env_str("V2EX_A2")
if not V2EX_COOKIE and V2EX_A2:
    V2EX_COOKIE = f"A2={V2EX_A2}"
V2EX_ENABLED = env_bool("V2EX_ENABLED", bool(V2EX_COOKIE))
NODESEEK_NAME = env_str("NODESEEK_NAME")
NODESEEK_COOKIE = env_str("NODESEEK_COOKIE") or env_str("NS_COOKIE")
NODESEEK_USERNAME = env_str("NODESEEK_USERNAME")
NODESEEK_PASSWORD = env_str("NODESEEK_PASSWORD")
NODESEEK_RANDOM = env_bool("NODESEEK_RANDOM", env_bool("NS_RANDOM", True))
NODESEEK_SOLVER_TYPE = (
    env_str("NODESEEK_SOLVER_TYPE") or SOLVER_TYPE or ""
).strip().lower()
NODESEEK_CLIENT_KEY = YESCAPTCHA_CLIENT_KEY
NODESEEK_YESCAPTCHA_API_BASE_URL = (
    env_str("NODESEEK_YESCAPTCHA_API_BASE_URL") or YESCAPTCHA_API_BASE_URL
)
NODESEEK_YESCAPTCHA_ADVANCED = env_bool(
    "NODESEEK_YESCAPTCHA_ADVANCED",
    YESCAPTCHA_ADVANCED,
)
if not SOLVER_TYPE and YESCAPTCHA_CLIENT_KEY:
    SOLVER_TYPE = "yescaptcha"
if not NODESEEK_SOLVER_TYPE and NODESEEK_CLIENT_KEY:
    NODESEEK_SOLVER_TYPE = "yescaptcha"

HOME_URL = "https://linux.do/"
TOPIC_LIST_URL = env_str("LINUXDO_TOPIC_LIST_URL", "https://linux.do/latest")
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"
HCAPTCHA_CREATE_URL = "https://linux.do/hcaptcha/create.json"
CURRENT_SESSION_URL = "https://linux.do/session/current.json"
TOPICS_TIMINGS_URL = "https://linux.do/topics/timings"
ACCOUNT_PREFERENCES_URL = "https://linux.do/my/preferences/account"
CONNECT_URL = "https://connect.linux.do/"
DEFAULT_IMPERSONATE = env_str("IMPERSONATE_VERSION", "chrome136") or "chrome136"
XIAOHEIHE_IMPERSONATE = env_str("XIAOHEIHE_IMPERSONATE", DEFAULT_IMPERSONATE) or DEFAULT_IMPERSONATE
NODESEEK_IMPERSONATE = (
    env_str("NODESEEK_IMPERSONATE") or env_str("NS_IMPERSONATE") or DEFAULT_IMPERSONATE
)
XIAOHEIHE_COOKIE = env_str("XIAOHEIHE_COOKIE") or env_str("XIAOHEIHE_COOKIES")
NODESEEK_ENABLED = env_bool(
    "NODESEEK_ENABLED",
    bool(NODESEEK_COOKIE or (NODESEEK_USERNAME and NODESEEK_PASSWORD)),
)
XIAOHEIHE_ACCOUNT_NAME = env_str("XIAOHEIHE_ACCOUNT_NAME")
XIAOHEIHE_HEADERS_JSON = env_str("XIAOHEIHE_HEADERS_JSON")
XIAOHEIHE_REQUEST_MODE = env_str("XIAOHEIHE_REQUEST_MODE", "signer") or "signer"
XIAOHEIHE_TIMEOUT = env_int("XIAOHEIHE_TIMEOUT", 20)
XIAOHEIHE_RETRY_TIMES = env_int("XIAOHEIHE_RETRY_TIMES", 6)
XIAOHEIHE_RETRY_MIN_DELAY = env_int("XIAOHEIHE_RETRY_MIN_DELAY", 3)
XIAOHEIHE_RETRY_MAX_DELAY = env_int("XIAOHEIHE_RETRY_MAX_DELAY", 12)
XIAOHEIHE_ENABLED = env_bool(
    "XIAOHEIHE_ENABLED",
    bool(XIAOHEIHE_COOKIE),
)

NODESEEK_INDEXED_ENV_PATTERN = re.compile(
    r"^(?:"
    r"NODESEEK_(?:COOKIE|USERNAME|PASSWORD|NAME|RANDOM|IMPERSONATE|SOLVER_TYPE|"
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
        return value.lower() in ["1", "true", "yes", "on"]
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
    cookie = indexed_env_str("NODESEEK_COOKIE", index, aliases=["NS_COOKIE"], default="")
    username = indexed_env_str("NODESEEK_USERNAME", index, default="")
    password = indexed_env_str("NODESEEK_PASSWORD", index, default="")

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


NODESEEK_ENABLED = env_bool(
    "NODESEEK_ENABLED",
    bool(collect_nodeseek_accounts()),
)


def is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def save_cookie_to_github_var(var_name: str, cookie: str) -> bool:
    repo = env_str("GITHUB_REPOSITORY")
    if not cookie:
        logger.warning("Cookie 为空，跳过保存到 GitHub Actions Variables")
        return False
    if not GH_PAT or not repo:
        logger.info("未配置 GH_PAT 或 GITHUB_REPOSITORY，跳过自动保存 Cookie")
        return False

    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "linuxdo-v2ex-checkin",
    }
    data = {"name": var_name, "value": cookie}
    check_url = f"https://api.github.com/repos/{repo}/actions/variables/{var_name}"
    create_url = f"https://api.github.com/repos/{repo}/actions/variables"

    try:
        response = requests.request(
            "PATCH",
            check_url,
            headers=headers,
            json=data,
            impersonate=DEFAULT_IMPERSONATE,
            timeout=30,
        )
        if response.status_code == 204:
            logger.info(f"GitHub Actions Variable {var_name} 更新成功")
            return True
        if response.status_code == 404:
            logger.info(f"GitHub Actions Variable {var_name} 不存在，尝试创建")
            response = requests.request(
                "POST",
                create_url,
                headers=headers,
                json=data,
                impersonate=DEFAULT_IMPERSONATE,
                timeout=30,
            )
            if response.status_code == 201:
                logger.info(f"GitHub Actions Variable {var_name} 创建成功")
                return True

        logger.warning(
            f"保存 Cookie 到 GitHub 失败: {response.status_code} {response.text[:200]}"
        )
        return False
    except Exception as e:
        logger.warning(f"保存 Cookie 到 GitHub 异常: {e}")
        return False


def save_cookie_to_env_file(var_name: str, cookie: str) -> bool:
    if not ENV_FILE_PATH:
        logger.info("未配置 LINUXDO_ENV_FILE，跳过本地环境文件回写")
        return False
    if not cookie:
        logger.warning("Cookie 为空，跳过本地环境文件回写")
        return False

    try:
        lines = []
        if os.path.exists(ENV_FILE_PATH):
            with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()

        updated = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{var_name}="):
                new_lines.append(f"{var_name}={cookie}")
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            new_lines.append(f"{var_name}={cookie}")

        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines).rstrip() + "\n")

        logger.info(f"已将 {var_name} 回写到 {ENV_FILE_PATH}")
        return True
    except Exception as e:
        logger.warning(f"回写 Cookie 到 {ENV_FILE_PATH} 失败: {e}")
        return False


class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform

        if platform in ("linux", "linux2"):
            platform_identifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platform_identifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platform_identifier = "Windows NT 10.0; Win64; x64"
        else:
            platform_identifier = "X11; Linux x86_64"

        browser_user_agent = build_browser_user_agent(
            platform_identifier, DEFAULT_IMPERSONATE
        )
        co = (
            ChromiumOptions()
            .auto_port()
            .headless(HEADLESS_MODE)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-popup-blocking")
        )
        if FORCE_IPV4:
            co.set_argument("--disable-ipv6")
        co.set_user_agent(browser_user_agent)
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        self._inject_hcaptcha_callback_interceptor()
        self.session = requests.Session()
        if FORCE_IPV4:
            self.session.curl_options = {
                **getattr(self.session, "curl_options", {}),
                CurlOpt.IPRESOLVE: CurlIpResolve.V4,
            }
        self.session.headers.update(
            {
                "User-Agent": browser_user_agent,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        logger.info(
            "浏览器运行参数: "
            f"headless={HEADLESS_MODE} "
            f"display={os.environ.get('DISPLAY') or '<none>'} "
            f"impersonate={DEFAULT_IMPERSONATE} "
            f"address={co.address}"
        )
        if not HEADLESS_MODE and not os.environ.get("DISPLAY"):
            logger.warning(
                "当前已禁用 headless，但 DISPLAY 为空；如果这是 Linux VPS，请使用 xvfb-run 启动脚本"
            )
        if FORCE_IPV4:
            logger.info("已启用 IPv4 优先模式")
        self.notifier = NotificationManager()
        self.login_name = USERNAME or "Cookie 用户"
        self.login_method = ""
        self.login_verified = False
        self.login_verify_source = ""
        self.last_login_validation_blocked = False
        self.connect_summary = ""
        self.browse_stats = {
            "topics_total": 0,
            "topics_planned": 0,
            "topics_completed": 0,
            "likes": 0,
        }

    @staticmethod
    def normalize_cookie_string(cookie_str: str) -> str:
        latest_by_name = {}
        for part in cookie_str.strip().split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            normalized_name = name.strip()
            if not normalized_name:
                continue
            if normalized_name in latest_by_name:
                latest_by_name.pop(normalized_name, None)
            latest_by_name[normalized_name] = value.strip()
        return "; ".join(f"{name}={value}" for name, value in latest_by_name.items())

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> list[dict]:
        cookies = []
        normalized_cookie_str = LinuxDoBrowser.normalize_cookie_string(cookie_str)
        for part in normalized_cookie_str.strip().split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                cookies.append(
                    {
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".linux.do",
                        "path": "/",
                    }
                )
        return cookies

    def reset_browser_context(self, reason: str = "") -> None:
        if reason:
            logger.info(f"重建登录上下文: {reason}")

        try:
            self.session.cookies.clear()
        except Exception:
            pass
        try:
            self.page.close()
        except Exception:
            pass
        try:
            self.browser.quit()
        except Exception:
            pass

        old_notifier = self.notifier
        old_login_name = self.login_name
        old_login_method = self.login_method
        self.__init__()
        self.notifier = old_notifier
        self.login_name = old_login_name
        self.login_method = old_login_method

    def _wait_for_cloudflare(self, max_wait: int = 60) -> bool:
        waited = 0
        while waited < max_wait:
            title = str(getattr(self.page, "title", "") or "").lower()
            url = str(getattr(self.page, "url", "") or "").lower()
            if "just a moment" not in title and "challenge" not in url:
                return True
            time.sleep(2)
            waited += 2
        title = str(getattr(self.page, "title", "") or "").lower()
        url = str(getattr(self.page, "url", "") or "").lower()
        return "just a moment" not in title and "challenge" not in url

    def sync_session_from_cookie_string(self, cookie_str: str) -> None:
        for ck in self.parse_cookie_string(cookie_str):
            self.session.cookies.set(
                ck["name"],
                ck["value"],
                domain=ck["domain"],
                path=ck["path"],
            )

    def sync_browser_from_cookie_string(self, cookie_str: str) -> bool:
        dp_cookies = self.parse_cookie_string(cookie_str)
        if not dp_cookies:
            return False
        self.sync_session_from_cookie_string(cookie_str)
        self.page.set.cookies(dp_cookies)
        return True

    def rebuild_browser_context_with_cookies(
        self, cookie_str: str, reason: str = ""
    ) -> bool:
        if not cookie_str:
            logger.warning("重建浏览器上下文时缺少 Cookie")
            return False

        self.reset_browser_context(reason or "reuse auth cookies")
        if not self.sync_browser_from_cookie_string(cookie_str):
            logger.warning("重建浏览器上下文后写回 Cookie 失败")
            return False

        try:
            self.page.get(HOME_URL)
            time.sleep(5)
        except Exception as e:
            logger.warning(f"重建浏览器上下文后打开首页失败: {e}")
            return False

        self.log_browser_cookie_summary("重建上下文后")
        return True

    def get_browser_cookie_string(self) -> str:
        try:
            cookie_str = self.page.cookies().as_str()
            if isinstance(cookie_str, str) and cookie_str.strip():
                return self.normalize_cookie_string(cookie_str)
        except Exception as e:
            logger.warning(f"读取浏览器 Cookie 失败: {e}")

        try:
            cookie_str = self.page.run_js("return document.cookie")
            if isinstance(cookie_str, str):
                return self.normalize_cookie_string(cookie_str)
        except Exception as e:
            logger.warning(f"通过 document.cookie 读取 Cookie 失败: {e}")
        return ""

    def sync_session_from_browser(self) -> str:
        cookie_str = self.get_browser_cookie_string()
        if cookie_str:
            self.sync_session_from_cookie_string(cookie_str)
        return cookie_str

    def summarize_browser_cookies(self, cookie_str: str = "") -> str:
        cookie_str = cookie_str or self.get_browser_cookie_string()
        cookie_names: Set[str] = set()
        auth_cookie_fingerprints: List[str] = []
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            normalized_name = name.strip()
            if normalized_name:
                cookie_names.add(normalized_name)
                if normalized_name in {"cf_clearance", "_forum_session", "_t"}:
                    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
                    auth_cookie_fingerprints.append(f"{normalized_name}={digest}")

        fields = [
            f"len={len(cookie_str)}",
            f"cf_clearance={'cf_clearance' in cookie_names}",
            f"_forum_session={'_forum_session' in cookie_names}",
            f"_t={'_t' in cookie_names}",
        ]
        if auth_cookie_fingerprints:
            fields.append("ids=" + ",".join(auth_cookie_fingerprints))
        return " ".join(fields)

    def has_auth_session_cookies(self, cookie_str: str = "") -> bool:
        cookie_str = cookie_str or self.get_browser_cookie_string()
        cookie_names: Set[str] = set()
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, _ = part.partition("=")
            if name.strip():
                cookie_names.add(name.strip())
        return "_forum_session" in cookie_names and "_t" in cookie_names

    def log_browser_cookie_summary(self, label: str, cookie_str: str = "") -> str:
        cookie_str = cookie_str or self.sync_session_from_browser()
        logger.info(f"{label} Cookie 摘要: {self.summarize_browser_cookies(cookie_str)}")
        return cookie_str

    def persist_cookie_if_possible(self, cookie_str: str) -> None:
        if not cookie_str:
            logger.warning("未获取到可持久化的 Cookie")
            return
        if is_github_actions():
            save_cookie_to_github_var("LINUXDO_COOKIES", cookie_str)
        else:
            save_cookie_to_env_file("LINUXDO_COOKIES", cookie_str)

    def get_page_csrf_token(self, page=None) -> str:
        page = page or self.page
        script = """
const meta = document.querySelector('meta[name="csrf-token"]');
if (meta && meta.content) return meta.content;
const input = document.querySelector('input[name="authenticity_token"]');
if (input && input.value) return input.value;
try {
    if (window.Discourse && window.Discourse.__container__) {
        const session = window.Discourse.__container__.lookup('service:session');
        if (session) {
            if (session.csrfToken) return session.csrfToken;
            if (typeof session.get === 'function') {
                const token = session.get('csrfToken');
                if (token) return token;
            }
        }
    }
} catch (e) {}
try {
    if (window.Discourse && window.Discourse.Session && typeof window.Discourse.Session.current === 'function') {
        const session = window.Discourse.Session.current();
        if (session && typeof session.get === 'function') {
            const token = session.get('csrfToken');
            if (token) return token;
        }
    }
} catch (e) {}
try {
    if (window.PreloadStore) {
        if (window.PreloadStore.data) {
            const token = window.PreloadStore.data['csrf_token'];
            if (token) return token;
        }
        if (typeof window.PreloadStore.get === 'function') {
            const token = window.PreloadStore.get('csrf_token');
            if (token) return token;
        }
    }
} catch (e) {}
try {
    if (window.__csrf_token) return window.__csrf_token;
    if (window.csrf_token) return window.csrf_token;
} catch (e) {}
return '';
"""
        try:
            token = page.run_js(script)
            return token.strip() if isinstance(token, str) else ""
        except Exception as e:
            logger.warning(f"从页面提取 CSRF token 失败: {e}")
            return ""

    def detect_turnstile_sitekey(self) -> str:
        script = """
const widget = document.querySelector('.cf-turnstile,[data-sitekey]');
if (widget && widget.getAttribute('data-sitekey')) {
    return widget.getAttribute('data-sitekey');
}
const iframe = document.querySelector('iframe[src*="turnstile"]');
if (iframe && iframe.src) {
    const match = iframe.src.match(/[?&]sitekey=([^&]+)/);
    if (match) return decodeURIComponent(match[1]);
}
return '';
"""
        try:
            sitekey = self.page.run_js(script)
            if isinstance(sitekey, str) and sitekey.strip():
                return sitekey.strip()
        except Exception as e:
            logger.warning(f"从页面提取 Turnstile sitekey 失败: {e}")

        try:
            html = self.page.html or ""
        except Exception:
            html = ""

        patterns = [
            r'data-sitekey=["\']([^"\']+)["\']',
            r'sitekey["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'websiteKey["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def inject_turnstile_token(self, token: str) -> bool:
        script = f"""
const token = {json.dumps(token)};
const selectors = [
  'textarea[name="cf-turnstile-response"]',
  'input[name="cf-turnstile-response"]',
  'textarea[name="cf_turnstile_response"]',
  'input[name="cf_turnstile_response"]'
];
let elements = [];
for (const selector of selectors) {{
  elements = elements.concat(Array.from(document.querySelectorAll(selector)));
}}
if (!elements.length) {{
  const form = document.querySelector('form');
  if (form) {{
    const textarea = document.createElement('textarea');
    textarea.name = 'cf-turnstile-response';
    textarea.style.display = 'none';
    form.appendChild(textarea);
    elements.push(textarea);
  }}
}}
for (const el of elements) {{
  el.value = token;
  el.innerHTML = token;
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
}}
const widget = document.querySelector('.cf-turnstile,[data-callback]');
const callbackName = widget ? widget.getAttribute('data-callback') : null;
if (callbackName && typeof window[callbackName] === 'function') {{
  try {{ window[callbackName](token); }} catch (e) {{}}
}}
window.__cfTurnstileResponse = token;
window.__turnstileToken = token;
return elements.length > 0;
"""
        try:
            result = self.page.run_js(script)
            return bool(result)
        except Exception as e:
            logger.warning(f"注入 Turnstile token 失败: {e}")
            return False

    def solve_turnstile_if_needed(self) -> str:
        sitekey = self.detect_turnstile_sitekey()
        if not sitekey:
            logger.info("登录页未检测到 Turnstile sitekey，跳过 YesCaptcha")
            return ""

        logger.info(f"检测到 Turnstile sitekey: {sitekey[:12]}...")
        if SOLVER_TYPE != "yescaptcha":
            logger.warning("检测到 Turnstile，但未启用 YesCaptcha")
            return ""
        if not YESCAPTCHA_CLIENT_KEY:
            logger.warning("检测到 Turnstile，但未配置 YesCaptcha Client Key")
            return ""

        try:
            logger.info("开始使用 YesCaptcha 解决 Turnstile...")
            solver = YesCaptchaSolver(
                api_base_url=YESCAPTCHA_API_BASE_URL,
                client_key=YESCAPTCHA_CLIENT_KEY,
                max_retries=YESCAPTCHA_MAX_RETRIES,
                retry_interval=YESCAPTCHA_RETRY_INTERVAL,
                timeout=YESCAPTCHA_TIMEOUT,
                advanced=YESCAPTCHA_ADVANCED,
            )
            token = solver.solve(
                url=LOGIN_URL,
                sitekey=sitekey,
                user_agent=self.session.headers.get("User-Agent"),
                verbose=False,
            )
            if not token:
                logger.error("YesCaptcha 未返回有效 token")
                return ""
            logger.info("YesCaptcha 验证完成，准备注入 token")
            self.inject_turnstile_token(token)
            return token
        except YesCaptchaSolverError as e:
            logger.error(f"YesCaptcha 解决失败: {e}")
            return ""
        except Exception as e:
            logger.error(f"YesCaptcha 处理异常: {e}")
            return ""

    def detect_hcaptcha_sitekey(self) -> str:
        script = """
const widget = document.querySelector('.h-captcha,[data-hcaptcha-sitekey]');
if (widget) {
    const key = widget.getAttribute('data-sitekey') || widget.getAttribute('data-hcaptcha-sitekey');
    if (key) return key;
}
const iframe = document.querySelector('iframe[src*="hcaptcha.com"]');
if (iframe && iframe.src) {
    const match = iframe.src.match(/[?&]sitekey=([^&]+)/);
    if (match) return decodeURIComponent(match[1]);
}
if ((widget || iframe) && window.Discourse && window.Discourse.SiteSettings) {
    return window.Discourse.SiteSettings.hcaptcha_site_key || '';
}
return '';
"""
        try:
            sitekey = self.page.run_js(script)
            if isinstance(sitekey, str) and sitekey.strip():
                return sitekey.strip()
        except Exception as e:
            logger.warning(f"提取 hCaptcha sitekey 失败: {e}")

        try:
            html = self.page.run_js(
                "return document.documentElement ? (document.documentElement.innerHTML || '') : '';"
            )
        except Exception:
            html = ""

        if not isinstance(html, str):
            html = ""

        patterns = [
            r'data-hcaptcha-sitekey=["\']([^"\']+)["\']',
            r'hcaptcha\\.com[^"\']*[?&]sitekey=([^&"\']+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _inject_hcaptcha_callback_interceptor(self) -> None:
        """Inject a CDP script that captures the inline hCaptcha callback.

        Runs on every new document BEFORE any page JS, so we can intercept
        hcaptcha.render() and stash the callback for later invocation with
        our YesCaptcha token.  This lets /hcaptcha/create originate from
        Discourse's own code path instead of our XHR.
        """
        script = """
(function() {
    let _hcaptcha = undefined;
    Object.defineProperty(window, 'hcaptcha', {
        configurable: true,
        enumerable: true,
        get: function() { return _hcaptcha; },
        set: function(val) {
            _hcaptcha = val;
            if (val && typeof val.render === 'function') {
                const origRender = val.render;
                val.render = function() {
                    const opts = arguments[1] || (arguments[0] && typeof arguments[0] === 'object' && !(arguments[0] instanceof Element) ? arguments[0] : {});
                    if (typeof opts.callback === 'function') {
                        window.__hcaptchaCallback = opts.callback;
                    }
                    if (opts['data-callback']) {
                        window.__hcaptchaCallbackName = opts['data-callback'];
                    }
                    return origRender.apply(this, arguments);
                };
            }
        }
    });
})();
"""
        try:
            self.page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source=script)
            logger.info("已注入 hCaptcha 回调拦截器 (CDP)")
        except Exception as e:
            logger.warning(f"注入 hCaptcha 回调拦截器失败 (CDP): {e}")

    def inject_hcaptcha_token(self, token: str) -> bool:
        script = f"""
const token = {json.dumps(token)};
const selectors = [
  'textarea[name="h-captcha-response"]',
  'input[name="h-captcha-response"]',
  'textarea[name="g-recaptcha-response"]',
  'input[name="g-recaptcha-response"]'
];
let elements = [];
for (const selector of selectors) {{
  elements = elements.concat(Array.from(document.querySelectorAll(selector)));
}}
if (!elements.length) {{
  const form = document.querySelector('form');
  if (form) {{
    const textarea = document.createElement('textarea');
    textarea.name = 'h-captcha-response';
    textarea.style.display = 'none';
    form.appendChild(textarea);
    elements.push(textarea);
  }}
}}
for (const el of elements) {{
  el.value = token;
  el.innerHTML = token;
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
}}
window.__hcaptchaToken = token;
window.__hcaptchaResponse = token;
return elements.length > 0;
"""
        try:
            result = self.page.run_js(script)
            return bool(result)
        except Exception as e:
            logger.warning(f"注入 hCaptcha token 失败: {e}")
            return False

    def _register_hcaptcha_token(self, token: str) -> bool:
        """Register hCaptcha token via the native Discourse callback.

        We intercept hcaptcha.render() via CDP to capture Discourse's inline
        callback.  Invoking it directly triggers the same XHR code path that a
        real user interaction would — this may bypass Cloudflare where our own
        XHR/fetch gets blocked.
        """
        # ── diagnostic: what does the page know about hCaptcha? ──────────
        diag_script = """
(function() {
    var info = {has_hcaptcha: !!window.hcaptcha};
    if (window.hcaptcha) {
        info.hcaptcha_type = typeof window.hcaptcha;
        info.hcaptcha_keys = Object.keys(window.hcaptcha).slice(0, 6);
        info.has_render = typeof window.hcaptcha.render === 'function';
    }
    info.cb_type = typeof window.__hcaptchaCallback;
    info.cb_name = window.__hcaptchaCallbackName || null;
    info.cb_is_fn = typeof window.__hcaptchaCallback === 'function';
    // also check for hCaptcha iframe on the page
    var iframe = document.querySelector('iframe[src*=\"hcaptcha\"]');
    info.has_hcaptcha_iframe = !!iframe;
    return JSON.stringify(info);
})();
"""
        try:
            raw = self.page.run_js(diag_script)
            if isinstance(raw, str):
                import json as _json
                diag = _json.loads(raw)
                logger.info(
                    f"hCaptcha 页面诊断: has_hcaptcha={diag.get('has_hcaptcha')} "
                    f"cb_type={diag.get('cb_type')} "
                    f"cb_name={diag.get('cb_name')} "
                    f"has_iframe={diag.get('has_hcaptcha_iframe')} "
                    f"hcaptcha_keys={diag.get('hcaptcha_keys')}"
                )
        except Exception:
            pass

        # Try the captured native callback first.
        callback_script = f"""
(function() {{
    var cb = window.__hcaptchaCallback;
    if (cb && typeof cb === 'function') {{
        try {{
            cb({json.dumps(token)});
            return 'callback_invoked';
        }} catch(e) {{
            return 'callback_error:' + String(e);
        }}
    }}
    return 'no_callback';
}})();
"""
        try:
            result = self.page.run_js(callback_script)
            if isinstance(result, str):
                if result == 'callback_invoked':
                    logger.info("hCaptcha/create 注册成功 (原生 Discourse 回调)")
                    return True
                if result.startswith('callback_error:'):
                    logger.warning(f"hCaptcha 原生回调抛出异常: {result}")
                elif result == 'no_callback':
                    logger.info("未捕获到 hCaptcha 原生回调，回退到浏览器 XHR")
        except Exception as e:
            logger.warning(f"hCaptcha 原生回调调用失败: {e}")

        # Fallback: browser XHR
        csrf_token = self.fetch_csrf_token()
        if not csrf_token:
            logger.error("hCaptcha 注册缺少 CSRF token")
            return False

        logger.info("正在通过浏览器 XHR 注册 hCaptcha 验证结果...")
        result = self.browser_request(
            "POST",
            HCAPTCHA_CREATE_URL,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Discourse-Present": "true",
                "Origin": "https://linux.do",
                "Referer": LOGIN_URL,
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
            },
            body=urlencode({"token": token}),
        )
        status = result.get("status", 0)
        text = result.get("text", "")
        if status != 200:
            logger.error(f"hCaptcha/create 失败: status={status}")
            if text:
                snippet = text[:300].replace("\n", " ").strip()
                logger.error(snippet)
            if "just a moment" in text.lower():
                logger.error("浏览器 XHR /hcaptcha/create 被 CF 拦截")
            return False
        logger.info("hCaptcha/create 注册成功 (browser XHR)")
        return True

    def solve_hcaptcha_if_needed(self) -> Optional[str]:
        sitekey = self.detect_hcaptcha_sitekey()
        if not sitekey:
            logger.info("登录页未检测到 hCaptcha sitekey，跳过 YesCaptcha")
            return ""

        logger.info(f"检测到 hCaptcha sitekey: {sitekey[:12]}...")
        if SOLVER_TYPE != "yescaptcha":
            logger.warning("检测到 hCaptcha，但未启用 YesCaptcha")
            return None
        if not YESCAPTCHA_CLIENT_KEY:
            logger.warning("检测到 hCaptcha，但未配置 YesCaptcha Client Key")
            return None

        try:
            total_wait = (
                YESCAPTCHA_HCAPTCHA_MAX_RETRIES
                * YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL
            )
            logger.info(
                "开始使用 YesCaptcha 解决 hCaptcha..."
                f" (max_retries={YESCAPTCHA_HCAPTCHA_MAX_RETRIES}, "
                f"retry_interval={YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL}s, "
                f"request_timeout={YESCAPTCHA_HCAPTCHA_TIMEOUT}s, "
                f"poll_budget≈{total_wait}s)"
            )
            solver = YesCaptchaSolver(
                api_base_url=YESCAPTCHA_API_BASE_URL,
                client_key=YESCAPTCHA_CLIENT_KEY,
                max_retries=YESCAPTCHA_HCAPTCHA_MAX_RETRIES,
                retry_interval=YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL,
                timeout=YESCAPTCHA_HCAPTCHA_TIMEOUT,
                advanced=YESCAPTCHA_ADVANCED,
            )
            token = solver.solve(
                url=LOGIN_URL,
                sitekey=sitekey,
                user_agent=self.session.headers.get("User-Agent"),
                verbose=False,
                captcha_type="hcaptcha",
            )
            if not token:
                logger.error("YesCaptcha 未返回有效 hCaptcha token")
                return None
            logger.info("YesCaptcha hCaptcha 验证完成，准备注入 token")
            self.inject_hcaptcha_token(token)
            return token
        except YesCaptchaSolverError as e:
            logger.error(f"YesCaptcha hCaptcha 解决失败: {e}")
            return None
        except Exception as e:
            logger.error(f"YesCaptcha hCaptcha 处理异常: {e}")
            return None

    def get_login_page_state(self) -> dict:
        script = """
const url = location.href || '';
const title = document.title || '';
const text = document.body ? document.body.innerText || '' : '';
const html = document.documentElement ? document.documentElement.innerHTML || '' : '';
const loginInput = document.querySelector('#login-account-name, input[name="login"], input[type="email"], input[autocomplete="username"]');
const passwordInput = document.querySelector('#login-account-password, input[name="password"], input[type="password"]');
const submitButton = document.querySelector('#login-button, button.login-button, form button[type="submit"], .login-button');
const hasCsrf = !!document.querySelector('meta[name="csrf-token"], input[name="authenticity_token"]');
const hasTurnstile = !!document.querySelector('.cf-turnstile,[data-sitekey],iframe[src*="turnstile"],script[src*="turnstile"]');
const hasHCaptcha = !!document.querySelector(
  '.h-captcha,[data-hcaptcha-sitekey],iframe[src*="hcaptcha.com"]'
);
const challengeText = `${title}\n${text}\n${html.slice(0, 3000)}`.toLowerCase();
const isChallenge = (
  challengeText.includes('too many requests') ||
  challengeText.includes('just a moment') ||
  challengeText.includes('cloudflare') ||
  challengeText.includes('cf-chl') ||
  challengeText.includes('challenge-platform') ||
  challengeText.includes('please wait while your request is being verified')
);
return JSON.stringify({
  url,
  title,
  has_login_form: !!(loginInput && passwordInput && submitButton),
  has_csrf: hasCsrf,
  has_turnstile: hasTurnstile,
  has_hcaptcha: hasHCaptcha,
  is_challenge: isChallenge,
  text_snippet: text.slice(0, 200).replace(/\\s+/g, ' ').trim(),
});
"""
        try:
            raw = self.page.run_js(script)
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"读取登录页状态失败: {e}")
        return {
            "url": "",
            "title": "",
            "has_login_form": False,
            "has_csrf": False,
            "has_turnstile": False,
            "has_hcaptcha": False,
            "is_challenge": False,
            "text_snippet": "",
        }

    def wait_for_login_page_ready(self, timeout: int = 25, interval: int = 2) -> dict:
        last_state = self.get_login_page_state()
        attempts = max(1, timeout // interval)
        for attempt in range(attempts):
            state = self.get_login_page_state()
            last_state = state
            if state["has_login_form"] or state["has_csrf"]:
                return state
            if state["has_turnstile"]:
                logger.info(
                    f"登录页已出现 Turnstile，但真实表单尚未就绪，继续等待... ({attempt + 1}/{attempts})"
                )
            if state["is_challenge"]:
                logger.info(
                    f"登录页仍处于 Cloudflare/验证页，继续等待... ({attempt + 1}/{attempts})"
                )
            time.sleep(interval)
        return last_state

    def open_login_modal_from_home(self) -> bool:
        try:
            logger.info("尝试从首页点击登录按钮...")
            self.page.get(HOME_URL)
            time.sleep(5)
            selectors = [
                "#login-button",
                "button.login-button",
                ".login-button",
                "button[class*='login']",
            ]
            for selector in selectors:
                try:
                    ele = self.page.ele(f"css:{selector}")
                    if ele:
                        ele.click()
                        time.sleep(5)
                        return True
                except Exception:
                    continue

            clicked = self.page.run_js(
                """
const selectors = ['#login-button', 'button.login-button', '.login-button', 'button[class*="login"]'];
for (const selector of selectors) {
  const el = document.querySelector(selector);
  if (el) {
    el.click();
    return true;
  }
}
return false;
"""
            )
            if clicked:
                time.sleep(5)
                return True
        except Exception as e:
            logger.warning(f"从首页打开登录弹窗失败: {e}")
        return False

    def submit_login_form(self, turnstile_token: str = "") -> bool:
        if turnstile_token:
            self.inject_turnstile_token(turnstile_token)

        script = f"""
const username = {json.dumps(USERNAME)};
const password = {json.dumps(PASSWORD)};
const loginInput = document.querySelector('#login-account-name, input[name="login"], input[type="email"], input[autocomplete="username"]');
const passwordInput = document.querySelector('#login-account-password, input[name="password"], input[type="password"]');
const submitButton = document.querySelector('#login-button, button.login-button, form button[type="submit"], .login-button');
if (!loginInput || !passwordInput || !submitButton) {{
  return JSON.stringify({{ ok: false, reason: 'missing-form-elements' }});
}}
const setValue = (el, value) => {{
  el.focus();
  el.value = '';
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.value = value;
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
}};
setValue(loginInput, username);
setValue(passwordInput, password);
submitButton.click();
return JSON.stringify({{ ok: true }});
"""
        try:
            raw = self.page.run_js(script)
            if isinstance(raw, str):
                result = json.loads(raw)
                return bool(result.get("ok"))
        except Exception as e:
            logger.warning(f"浏览器表单提交失败: {e}")
        return False

    def get_login_error_text(self) -> str:
        script = """
const selectors = [
  '.alert-error',
  '.alert.alert-error',
  '.flash',
  '.flash-text',
  '.errors',
  '.login-error',
  '.reason',
];
for (const selector of selectors) {
  const el = document.querySelector(selector);
  if (el && el.innerText && el.innerText.trim()) {
    return el.innerText.trim();
  }
}
return document.body ? (document.body.innerText || '').slice(0, 300).replace(/\\s+/g, ' ').trim() : '';
"""
        try:
            text = self.page.run_js(script)
            return text.strip() if isinstance(text, str) else ""
        except Exception:
            return ""

    def get_account_preferences_state(self) -> dict:
        script = """
const url = location.href || '';
const title = document.title || '';
const text = document.body ? document.body.innerText || '' : '';
const html = document.documentElement ? document.documentElement.innerHTML || '' : '';
const hasCurrentUser = !!document.querySelector('#current-user, #toggle-current-user');
const hasPrefUsername = !!document.querySelector('.pref-username, .username-preference__current-username');
const hasPrefEmail = !!document.querySelector('.pref-email, .emails .email');
const hasAccountNav = !!document.querySelector('.user-nav__preferences-account, a[href*="/preferences/account"]');
const hasAssociatedAccounts = !!document.querySelector('.associated-accounts');
const lowerText = `${title}\n${text}\n${html.slice(0, 3000)}`.toLowerCase();
const isChallenge = (
  lowerText.includes('too many requests') ||
  lowerText.includes('just a moment') ||
  lowerText.includes('cloudflare') ||
  lowerText.includes('cf-chl') ||
  lowerText.includes('challenge-platform') ||
  lowerText.includes('please wait while your request is being verified')
);
return JSON.stringify({
  url,
  title,
  has_current_user: hasCurrentUser,
  has_pref_username: hasPrefUsername,
  has_pref_email: hasPrefEmail,
  has_account_nav: hasAccountNav,
  has_associated_accounts: hasAssociatedAccounts,
  is_challenge: isChallenge,
  text_snippet: text.slice(0, 200).replace(/\\s+/g, ' ').trim(),
});
"""
        try:
            raw = self.page.run_js(script)
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"读取账号设置页状态失败: {e}")
        return {
            "url": "",
            "title": "",
            "has_current_user": False,
            "has_pref_username": False,
            "has_pref_email": False,
            "has_account_nav": False,
            "has_associated_accounts": False,
            "is_challenge": False,
            "text_snippet": "",
        }

    def page_looks_like_account_preferences(self, state: Optional[dict] = None) -> bool:
        state = state or self.get_account_preferences_state()
        url = state.get("url") or ""
        if "/preferences/account" not in url:
            return False

        if not state.get("has_current_user"):
            return False

        if "/u/" in url:
            return True

        strong_marker_count = sum(
            1
            for key in [
                "has_pref_username",
                "has_pref_email",
                "has_account_nav",
                "has_associated_accounts",
            ]
            if state.get(key)
        )
        return strong_marker_count >= 1

    def update_login_name_from_page(self) -> None:
        script = """
const candidates = [];
const currentUser = document.querySelector('#current-user');
if (currentUser) {
  const href = currentUser.getAttribute('href') || '';
  const title = currentUser.getAttribute('title') || '';
  const ariaLabel = currentUser.getAttribute('aria-label') || '';
  const dataUserCard = currentUser.getAttribute('data-user-card') || '';
  candidates.push(href.split('/').filter(Boolean).pop() || '');
  candidates.push(title);
  candidates.push(ariaLabel);
  candidates.push(dataUserCard);
}
const userCard = document.querySelector('[data-user-card]');
if (userCard) {
  candidates.push(userCard.getAttribute('data-user-card') || '');
}
const body = document.body;
if (body) {
  candidates.push(body.getAttribute('data-current-username') || '');
  if (body.dataset && body.dataset.currentUsername) {
    candidates.push(body.dataset.currentUsername);
  }
}
const meta = document.querySelector('meta[name="discourse_current_username"]');
if (meta) {
  candidates.push(meta.getAttribute('content') || '');
}
const pathParts = (location.pathname || '').split('/').filter(Boolean);
const userIndex = pathParts.indexOf('u');
if (userIndex >= 0 && pathParts[userIndex + 1]) {
  candidates.push(pathParts[userIndex + 1] || '');
}
const staticUsername = document.querySelector('.username-preference__current-username');
if (staticUsername) {
  candidates.push(staticUsername.innerText || staticUsername.textContent || '');
}
for (const candidate of candidates) {
  const value = String(candidate || '').trim().replace(/^@+/, '');
  if (value) {
    return value;
  }
}
return '';
"""
        try:
            username = self.page.run_js(script)
        except Exception:
            return

        if isinstance(username, str) and username.strip():
            self.login_name = username.strip()

    def mark_login_verified(self, source: str) -> None:
        self.login_verified = True
        self.login_verify_source = source

    def get_login_verify_label(self) -> str:
        if self.login_verify_source == "api":
            return "API"
        if self.login_verify_source == "account_page":
            return "账号设置页"
        return "未知"

    def get_login_method_label(self) -> str:
        if self.login_method == "cookie":
            return "Cookie"
        if self.login_method == "password":
            return "账号密码"
        return "未知"

    @staticmethod
    def summarize_json_payload(data: dict, limit: int = 300) -> str:
        if not isinstance(data, dict):
            return str(data)[:limit]

        preferred_keys = [
            "success",
            "error",
            "errors",
            "message",
            "action",
            "reason",
            "current_user",
            "user",
            "authenticated",
            "logged_in",
            "requires_second_factor",
        ]
        summary = {key: data.get(key) for key in preferred_keys if key in data}
        target = summary or data
        try:
            return json.dumps(target, ensure_ascii=False)[:limit]
        except Exception:
            return str(target)[:limit]

    def validate_login_via_api(self) -> bool:
        self.sync_session_from_browser()
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": HOME_URL,
        }
        try:
            resp = self.session.get(
                CURRENT_SESSION_URL,
                headers=headers,
                impersonate=DEFAULT_IMPERSONATE,
                timeout=20,
            )
        except Exception as e:
            logger.info(f"通过 API 校验登录状态异常，继续回退到页面校验: {e}")
            return False

        if resp.status_code != 200:
            logger.info(
                f"通过 API 未直接确认登录态，继续回退到页面校验: "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
            return False

        try:
            data = resp.json()
        except Exception as e:
            logger.info(f"解析 current session 响应失败，继续回退到页面校验: {e}")
            return False

        current_user = data.get("current_user")
        if current_user and current_user.get("username"):
            self.login_name = current_user.get("username") or self.login_name
            self.mark_login_verified("api")
            logger.info(f"通过 API 校验登录成功: {self.login_name}")
            return True

        logger.info(f"API 响应中未识别到 current_user，继续回退到页面校验: {str(data)[:200]}")
        return False

    def validate_login(self) -> bool:
        logger.info("验证登录状态，优先通过 current session API，再回退到账号设置页...")
        self.last_login_validation_blocked = False
        if self.validate_login_via_api():
            return True

        logger.info("API 未直接确认登录，继续使用账号设置页校验...")
        for attempt in range(1, 3):
            try:
                self.page.get(ACCOUNT_PREFERENCES_URL)
                time.sleep(5 if attempt == 1 else 4)
            except Exception as e:
                logger.warning(f"打开账号设置页验证登录状态失败: {e}")
                break

            account_state = self.get_account_preferences_state()
            logger.info(
                f"账号设置页状态({attempt}/2): "
                f"url={account_state.get('url')} "
                f"title={account_state.get('title')} "
                f"current_user={account_state.get('has_current_user')} "
                f"pref_username={account_state.get('has_pref_username')} "
                f"pref_email={account_state.get('has_pref_email')} "
                f"account_nav={account_state.get('has_account_nav')} "
                f"challenge={account_state.get('is_challenge')}"
            )

            if self.page_looks_like_account_preferences(account_state):
                self.update_login_name_from_page()
                self.mark_login_verified("account_page")
                logger.info("通过账号设置页验证登录成功")
                self.sync_session_from_browser()
                return True

            self.log_browser_cookie_summary(f"账号设置页校验({attempt}/2)后")
            if account_state.get("is_challenge"):
                self.last_login_validation_blocked = True
                logger.warning("账号设置页可能被限流或挑战页拦截，回退到首页校验")
                break

            logger.warning(
                "账号设置页未识别到登录态: "
                f"url={account_state.get('url')} "
                f"title={account_state.get('title')} "
                f"snippet={account_state.get('text_snippet')[:120]}"
            )
            if attempt < 2:
                logger.info("账号设置页仍未确认登录，等待后再次校验...")
                time.sleep(3)

        self.log_browser_cookie_summary("账号设置页最终校验失败后")
        logger.error("登录验证失败，未识别到登录态")
        return False

    def fetch_csrf_token(self, page=None) -> str:
        page = page or self.page
        page_url = str(getattr(page, "url", "") or LOGIN_URL)
        csrf_token = self.get_page_csrf_token(page=page)
        if csrf_token:
            logger.info(f"从页面 DOM/JS 获取到 CSRF token: {csrf_token[:8]}...")
            return csrf_token

        logger.info("页面中未直接拿到 CSRF token，回退到接口请求...")
        csrf_resp = self.browser_request(
            "GET",
            CSRF_URL,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Discourse-Present": "true",
                "X-Requested-With": "XMLHttpRequest",
            },
            page=page,
        )
        if csrf_resp.get("status") == 200:
            try:
                token = json.loads(csrf_resp.get("text") or "{}").get("csrf", "")
                if token:
                    logger.info(f"通过浏览器 XHR 获取到 CSRF token: {token[:8]}...")
                    return token
            except json.JSONDecodeError:
                logger.warning(f"CSRF 响应解析失败: {csrf_resp.get('text', '')[:200]}")
        else:
            logger.warning(
                "浏览器 XHR 请求 CSRF 失败: "
                f"status={csrf_resp.get('status')} "
                f"error={csrf_resp.get('error', '')}"
            )

        logger.info("回退到 requests.Session 直接请求 CSRF token...")
        self.sync_session_from_browser()
        try:
            resp = self.session.get(
                CSRF_URL,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Discourse-Present": "true",
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
                impersonate=DEFAULT_IMPERSONATE,
                timeout=15,
            )
            if resp.status_code == 200:
                try:
                    token = resp.json().get("csrf", "")
                except Exception:
                    token = ""
                if token:
                    logger.info(f"通过 requests.Session 获取到 CSRF token: {token[:8]}...")
                    return token
                logger.warning(
                    "requests.Session 请求 CSRF 返回 200，但未拿到 token: "
                    f"{resp.text[:200]}"
                )
            else:
                logger.warning(
                    "requests.Session 请求 CSRF 失败: "
                    f"status={resp.status_code} body={resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"requests.Session 请求 CSRF 异常: {e}")

        logger.error("所有路径均未获取到 CSRF token")
        return ""

    def prepare_hcaptcha_session(self, hcaptcha_token: str, csrf_token: str) -> bool:
        if not hcaptcha_token:
            return True
        if not csrf_token:
            logger.error("hCaptcha 验证缺少 CSRF token")
            return False

        logger.info("正在向站点注册 hCaptcha 验证结果...")
        # Use form-in-iframe POST instead of XHR — Cloudflare blocks XHR/fetch
        # but allows real page navigations.  The iframe loads the hcaptcha/create
        # response as a same-origin document so we can read its body.
        result = self.browser_form_post(
            HCAPTCHA_CREATE_URL,
            {"token": hcaptcha_token},
            csrf_token=csrf_token,
        )
        status = result.get("status", 0)
        text = result.get("text", "")
        if status != 200:
            logger.error(f"hCaptcha/create 失败: {status}")
            if text:
                logger.error(text[:500])
            if result.get("error"):
                logger.error(result["error"])
            return False
        return True

    def browser_form_post(self, url: str, fields: dict, csrf_token: str = "") -> dict:
        """POST via a hidden iframe form — passes Cloudflare unlike XHR."""
        field_js_parts = []
        for name, value in fields.items():
            field_js_parts.append(
                "f.appendChild(Object.assign(document.createElement('input'),"
                f"{{type:'hidden',name:{json.dumps(name)},value:{json.dumps(str(value))}}}));"
            )
        if csrf_token:
            field_js_parts.append(
                "f.appendChild(Object.assign(document.createElement('input'),"
                f"{{type:'hidden',name:'authenticity_token',value:{json.dumps(csrf_token)}}}));"
            )
        add_fields_js = "\n".join(field_js_parts)

        script = (
            "return new Promise((resolve) => {\n"
            "    const iframe = document.createElement('iframe');\n"
            "    iframe.style.display = 'none';\n"
            "    document.body.appendChild(iframe);\n"
            "    const doc = iframe.contentDocument || iframe.contentWindow.document;\n"
            "    doc.open();\n"
            "    doc.write('<html><body><form id=\"f\" action="
            + json.dumps(url)
            + " method=\"POST\"></form></body></html>');\n"
            "    doc.close();\n"
            "    const f = doc.getElementById('f');\n"
            + add_fields_js
            + "\n"
            "    const deadline = Date.now() + 15000;\n"
            "    const timer = setInterval(() => {\n"
            "        try {\n"
            "            const bodyText = (iframe.contentDocument || iframe.contentWindow.document).body.innerText || '';\n"
            "            if (bodyText) {\n"
            "                clearInterval(timer);\n"
            "                const lowered = bodyText.toLowerCase();\n"
            "                const isChallenge = (\n"
            "                    lowered.includes('just a moment') ||\n"
            "                    lowered.includes('enable javascript') ||\n"
            "                    (lowered.includes('challenge') && lowered.includes('platform'))\n"
            "                );\n"
            "                // CF managed challenge auto-resolves — keep waiting\n"
            "                if (!isChallenge) {\n"
            "                    document.body.removeChild(iframe);\n"
            "                    resolve(JSON.stringify({status: 200, text: bodyText, url: "
            + json.dumps(url)
            + "}));\n"
            "                }\n"
            "            }\n"
            "        } catch(e) {}\n"
            "        if (Date.now() > deadline) {\n"
            "            clearInterval(timer);\n"
            "            try {\n"
            "                const finalText = (iframe.contentDocument || iframe.contentWindow.document).body.innerText || '';\n"
            "                document.body.removeChild(iframe);\n"
            "                resolve(JSON.stringify({status: 403, error: 'blocked', text: finalText, url: "
            + json.dumps(url)
            + "}));\n"
            "            } catch(e) {\n"
            "                resolve(JSON.stringify({status: 0, error: 'timeout', url: "
            + json.dumps(url)
            + "}));\n"
            "            }\n"
            "        }\n"
            "    }, 500);\n"
            "    f.submit();\n"
            "});"
        )
        try:
            raw = self.page.run_js(script)
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception as e:
            return {"status": 0, "error": str(e), "url": url}
        return {"status": 0, "error": "unknown", "url": url}

    def browser_request(self, method: str, url: str, headers=None, body=None, page=None) -> dict:
        page = page or self.page
        headers = headers or {}
        header_lines = "\n".join(
            [
                f"xhr.setRequestHeader({json.dumps(k)}, {json.dumps(v)});"
                for k, v in headers.items()
            ]
        )
        body_js = "null" if body is None else json.dumps(body)
        script = f"""
return new Promise((resolve) => {{
    const xhr = new XMLHttpRequest();
    xhr.open({json.dumps(method)}, {json.dumps(url)}, true);
    xhr.withCredentials = true;
    xhr.timeout = 15000;
{header_lines}
    xhr.onload = function() {{
        resolve(JSON.stringify({{
            "status": xhr.status,
            "text": xhr.responseText,
            "url": xhr.responseURL || {json.dumps(url)}
        }}));
    }};
    xhr.onerror = function() {{
        resolve(JSON.stringify({{
            "status": 0,
            "error": "network error",
            "url": {json.dumps(url)}
        }}));
    }};
    xhr.ontimeout = function() {{
        resolve(JSON.stringify({{
            "status": 0,
            "error": "timeout",
            "url": {json.dumps(url)}
        }}));
    }};
    try {{
        xhr.send({body_js});
    }} catch (err) {{
        resolve(JSON.stringify({{
            "status": 0,
            "error": String(err),
            "url": {json.dumps(url)}
        }}));
    }}
}});
"""
        try:
            raw = page.run_js(script)
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception as e:
            return {"status": 0, "error": str(e), "url": url}
        return {"status": 0, "error": "unknown browser request error", "url": url}

    def open_login_page(self) -> bool:
        try:
            logger.info("打开登录页，准备在浏览器上下文中登录...")
            self.page.get(LOGIN_URL)
            time.sleep(5)
            if not self._wait_for_cloudflare():
                logger.warning("登录页可能被 Cloudflare 拦截，继续尝试")
            return True
        except Exception as e:
            logger.error(f"打开登录页失败: {e}")
            return False

    def login_via_session_flow(self, page_state: Optional[dict] = None) -> bool:
        page_state = page_state or self.get_login_page_state()
        return self.login_via_api_captcha_flow(page_state)

    def login_via_api_captcha_flow(self, page_state: Optional[dict] = None) -> bool:
        page_state = page_state or self.get_login_page_state()
        last_login_resp: dict = {}
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.warning(
                    f"自动登录重试 {attempt}/{max_attempts}，重新刷新登录页和验证上下文"
                )
                if not self.open_login_page():
                    return False
                page_state = self.wait_for_login_page_ready()
                if not (page_state.get("has_login_form") or page_state.get("has_csrf")):
                    logger.info("直接打开 /login 未拿到真实登录页，尝试从首页重新打开登录弹窗...")
                    if self.open_login_modal_from_home():
                        page_state = self.wait_for_login_page_ready()

            if not (page_state.get("has_login_form") or page_state.get("has_csrf")):
                logger.warning(
                    "当前页还不是可提交的真实登录页: "
                    f"url={page_state.get('url')} "
                    f"form={page_state.get('has_login_form')} "
                    f"csrf={page_state.get('has_csrf')} "
                    f"turnstile={page_state.get('has_turnstile')} "
                    f"challenge={page_state.get('is_challenge')}"
                )
                if attempt < max_attempts:
                    continue
                return False

            turnstile_token = self.solve_turnstile_if_needed()
            page_state = self.get_login_page_state()

            if page_state.get("has_login_form") and not page_state.get("has_hcaptcha"):
                logger.info("登录页当前仅显示 Turnstile，先通过浏览器表单触发站内验证流程...")
                if self.submit_login_form(turnstile_token):
                    time.sleep(8)
                    page_state = self.get_login_page_state()
                    logger.info(
                        "表单触发后的登录页状态: "
                        f"url={page_state.get('url')} "
                        f"form={page_state.get('has_login_form')} "
                        f"csrf={page_state.get('has_csrf')} "
                        f"turnstile={page_state.get('has_turnstile')} "
                        f"hcaptcha={page_state.get('has_hcaptcha')} "
                        f"challenge={page_state.get('is_challenge')}"
                    )
                    if page_state.get("text_snippet"):
                        logger.info(f"表单触发后的页面摘要: {page_state['text_snippet'][:200]}")

                    if page_state.get("has_hcaptcha"):
                        logger.info("检测到站内 hCaptcha，继续完成二次验证...")
                    elif self.validate_login():
                        cookie_str = self.sync_session_from_browser()
                        if cookie_str:
                            logger.info("表单登录成功，已提取新的 Cookie")
                            self.persist_cookie_if_possible(cookie_str)
                        else:
                            logger.warning("表单登录成功，但未能从浏览器提取完整 Cookie")
                        return True
                    else:
                        error_text = self.get_login_error_text()
                        if error_text:
                            logger.warning(f"表单提交后页面提示: {error_text[:300]}")
                        logger.info("表单提交后未直接登录成功，继续走接口登录补全流程")
                else:
                    logger.warning("未能通过浏览器表单触发站内验证流程，继续尝试接口登录")

            csrf_token = self.fetch_csrf_token()
            if not csrf_token:
                logger.error("未从响应中获取到 CSRF token")
                return False

            hcaptcha_token = self.solve_hcaptcha_if_needed()
            if hcaptcha_token is None:
                logger.warning(
                    "hCaptcha 解决失败，降级尝试不带 hCaptcha token 提交登录..."
                )
                hcaptcha_token = ""
            if hcaptcha_token:
                # A new Turnstile appears when hCaptcha is triggered.  Solve it
                # FIRST to get a fresh cf_clearance before calling /hcaptcha/create.
                page_state = self.get_login_page_state()
                if page_state.get("has_turnstile"):
                    logger.info("检测到新 Turnstile (Cloudflare)，先解决以获取新鲜 cf_clearance...")
                    self.solve_turnstile_if_needed()
                    time.sleep(3)

                # Register hCaptcha token via dedicated tab form POST.
                # All XHR/fetch/curl_cffi approaches are blocked by Cloudflare.
                # The fresh cf_clearance from the Turnstile solve above may help.
                if not self._register_hcaptcha_token(hcaptcha_token):
                    logger.warning("hCaptcha token 注册失败，尝试继续表单登录...")

                self._wait_for_cloudflare()
                logger.info("通过浏览器表单提交登录...")
                self.inject_hcaptcha_token(hcaptcha_token)
                if not self.submit_login_form():
                    logger.warning("表单提交失败，尝试继续接口登录")
                else:
                    time.sleep(8)
                    if self.validate_login():
                        cookie_str = self.sync_session_from_browser()
                        if cookie_str:
                            logger.info("表单登录成功，已提取新的 Cookie")
                            self.persist_cookie_if_possible(cookie_str)
                        else:
                            logger.warning("表单登录成功，但未能从浏览器提取完整 Cookie")
                        return True
                    error_text = self.get_login_error_text()
                    if error_text:
                        logger.warning(f"表单登录后页面提示: {error_text[:300]}")
                if attempt < max_attempts:
                    continue
                return False

            self._wait_for_cloudflare()
            logger.info("正在提交登录请求...")
            payload_data = {
                "login": USERNAME,
                "password": PASSWORD,
                "second_factor_method": "1",
                "timezone": "Asia/Shanghai",
            }
            if turnstile_token:
                payload_data["cf-turnstile-response"] = turnstile_token
                payload_data["cf_turnstile_response"] = turnstile_token
            if hcaptcha_token:
                payload_data["h-captcha-response"] = hcaptcha_token
                payload_data["g-recaptcha-response"] = hcaptcha_token

            last_login_resp = self.browser_request(
                "POST",
                SESSION_URL,
                headers={
                    "Accept": "*/*",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Discourse-Present": "true",
                    "Origin": "https://linux.do",
                    "Referer": LOGIN_URL,
                    "X-CSRF-Token": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                },
                body=urlencode(payload_data),
            )
            if last_login_resp.get("status") != 200:
                logger.error(f"登录失败，状态码: {last_login_resp.get('status')}")
                if last_login_resp.get("text"):
                    logger.error(last_login_resp["text"][:500])
                if last_login_resp.get("error"):
                    logger.error(last_login_resp["error"])
                if attempt < max_attempts:
                    continue
                return False

            try:
                response_json = json.loads(last_login_resp.get("text") or "{}")
            except json.JSONDecodeError:
                response_json = {}

            if response_json:
                logger.info(
                    "登录接口返回 200: "
                    f"keys={','.join(list(response_json.keys())[:12])}"
                )
                logger.info(
                    "登录接口摘要: "
                    f"{self.summarize_json_payload(response_json)}"
                )
            elif last_login_resp.get("text"):
                logger.info(
                    "登录接口返回 200，但响应不是 JSON: "
                    f"{(last_login_resp.get('text') or '')[:200]}"
                )

            if response_json.get("error"):
                logger.error(f"登录失败: {response_json.get('error')}")
                if attempt < max_attempts:
                    continue
                return False

            cookie_str = self.log_browser_cookie_summary("接口登录提交后")
            if cookie_str and self.has_auth_session_cookies(cookie_str):
                logger.info("登录接口成功，正在重建浏览器上下文并注入最新认证 Cookie...")
                if self.rebuild_browser_context_with_cookies(
                    cookie_str, "sync freshly issued auth cookies after session login"
                ):
                    cookie_str = self.log_browser_cookie_summary("接口登录提交后(重建上下文后)")
            else:
                logger.info("登录接口成功，正在导航首页以同步浏览器 Cookie...")
                try:
                    self.page.get(HOME_URL)
                    time.sleep(6)
                except Exception as nav_err:
                    logger.warning(f"登录后导航首页失败: {nav_err}")
                cookie_str = self.log_browser_cookie_summary("接口登录提交后(导航首页后)")

            if self.validate_login():
                if cookie_str:
                    logger.info("登录成功，已提取新的 Cookie")
                    self.persist_cookie_if_possible(cookie_str)
                else:
                    logger.warning("登录成功，但未能从浏览器提取完整 Cookie")
                return True

            logger.info("首次登录校验未通过，等待后再做一次页面校验...")
            time.sleep(5)
            cookie_str = self.log_browser_cookie_summary("二次校验前")
            if self.validate_login():
                if cookie_str:
                    logger.info("登录成功，已提取新的 Cookie")
                    self.persist_cookie_if_possible(cookie_str)
                else:
                    logger.warning("登录成功，但未能从浏览器提取完整 Cookie")
                return True

            error_text = self.get_login_error_text()
            if error_text:
                logger.warning(f"接口登录后页面提示: {error_text[:300]}")
            logger.warning(
                "接口登录后仍未确认登录态: "
                f"response_url={last_login_resp.get('url')} "
                f"cookie_summary={self.summarize_browser_cookies(cookie_str)}"
            )
            if self.has_auth_session_cookies(cookie_str):
                logger.warning("已检测到认证 Cookie，尝试重建浏览器上下文后再次校验登录态...")
                if self.rebuild_browser_context_with_cookies(
                    cookie_str, "session API login returned cookies but page stayed logged out"
                ):
                    refreshed_cookie_str = self.sync_session_from_browser()
                    if self.validate_login():
                        if refreshed_cookie_str:
                            logger.info("重建浏览器上下文后确认登录成功，已提取新的 Cookie")
                            self.persist_cookie_if_possible(refreshed_cookie_str)
                        else:
                            logger.warning("重建浏览器上下文后确认登录成功，但未能提取完整 Cookie")
                        return True
                    logger.warning(
                        "重建浏览器上下文后仍未确认登录态: "
                        f"cookie_summary={self.summarize_browser_cookies(refreshed_cookie_str)}"
                    )
                logger.error(
                    "站点已下发会话 Cookie，但账号设置页仍然不是已登录页面；"
                    "这通常说明当前 VPS/IP 环境下该会话未被站点接受，继续自动重试意义不大"
                )
                return False
            if attempt < max_attempts:
                continue

        return False

    def login_with_cookies(self, cookie_str: str) -> bool:
        logger.info("检测到手动 Cookie，尝试 Cookie 登录...")

        # Navigate clean first to pass Cloudflare, same as NodeSeek fix
        try:
            self.page.get(HOME_URL)
            time.sleep(5)
        except Exception as e:
            logger.warning(f"Cookie 登录前打开首页失败: {e}")
        if not self._wait_for_cloudflare():
            logger.warning("Cookie 登录前首页仍被 Cloudflare 拦截，继续尝试")

        if not self.sync_browser_from_cookie_string(cookie_str):
            logger.error("Cookie 解析失败或为空，无法使用 Cookie 登录")
            return False

        # Re-navigate after setting cookies — stale cf_clearance may trigger
        # another challenge; let the browser pass it before validating
        try:
            self.page.get(HOME_URL)
            time.sleep(5)
        except Exception as e:
            logger.warning(f"Cookie 注入后打开首页失败: {e}")
        self._wait_for_cloudflare()

        if self.validate_login():
            cookie_string = self.sync_session_from_browser()
            self.persist_cookie_if_possible(cookie_string or cookie_str)
            return True

        logger.error("Cookie 登录验证失败，Cookie 可能已过期")
        return False

    def login(self) -> bool:
        logger.info("开始账号密码登录")
        if not self.open_login_page():
            return False

        page_state = self.wait_for_login_page_ready()
        logger.info(
            "登录页状态: "
            f"url={page_state.get('url')} "
            f"title={page_state.get('title')} "
            f"form={page_state.get('has_login_form')} "
            f"csrf={page_state.get('has_csrf')} "
            f"turnstile={page_state.get('has_turnstile')} "
            f"challenge={page_state.get('is_challenge')}"
        )
        if page_state.get("text_snippet"):
            logger.info(f"登录页摘要: {page_state['text_snippet'][:200]}")

        if not (page_state.get("has_login_form") or page_state.get("has_csrf")):
            if self.open_login_modal_from_home():
                page_state = self.wait_for_login_page_ready()
                logger.info(
                    "首页登录弹窗状态: "
                    f"url={page_state.get('url')} "
                    f"title={page_state.get('title')} "
                    f"form={page_state.get('has_login_form')} "
                    f"csrf={page_state.get('has_csrf')} "
                    f"turnstile={page_state.get('has_turnstile')} "
                    f"challenge={page_state.get('is_challenge')}"
                )
                if page_state.get("text_snippet"):
                    logger.info(f"首页登录弹窗摘要: {page_state['text_snippet'][:200]}")

        if not (page_state.get("has_login_form") or page_state.get("has_csrf")):
            logger.error(
                "当前仍未进入真实登录页，停止提交登录请求: "
                f"url={page_state.get('url')} "
                f"turnstile={page_state.get('has_turnstile')} "
                f"challenge={page_state.get('is_challenge')}"
            )
            return False

        return self.login_via_api_captcha_flow(page_state)

    def click_topic(self):
        topic_urls = self.collect_topic_urls()
        if not topic_urls:
            logger.error("未找到主题帖")
            return False

        sample_count = min(10, len(topic_urls))
        self.browse_stats["topics_total"] = len(topic_urls)
        self.browse_stats["topics_planned"] = sample_count
        self.browse_stats["topics_completed"] = 0
        self.browse_stats["likes"] = 0

        logger.info(
            f"开始浏览任务：共发现 {len(topic_urls)} 个主题帖，计划浏览 {sample_count} 个"
        )
        for index, topic_url in enumerate(random.sample(topic_urls, sample_count), start=1):
            result = self.click_one_topic(topic_url)
            if not result:
                logger.warning(f"主题浏览失败：{index}/{sample_count}")
                continue

            self.browse_stats["topics_completed"] += 1
            if result.get("liked"):
                self.browse_stats["likes"] += 1

            logger.info(
                "主题浏览完成: "
                f"{index}/{sample_count}, "
                f"点赞={'是' if result.get('liked') else '否'}, "
                f"滚动={result.get('scrolls', 0)}次, "
                f"结束原因={self.get_browse_exit_reason_label(result.get('exit_reason'))}"
            )

        logger.info(
            "浏览任务摘要: "
            f"已完成 {self.browse_stats['topics_completed']}/{sample_count} 个主题, "
            f"点赞 {self.browse_stats['likes']} 次"
        )
        return True

    def extract_topic_urls_from_current_page(self) -> List[str]:
        selectors = [
            "css:#list-area .title a",
            "css:#list-area a.title",
            "css:table.topic-list a.title",
            "css:.topic-list a.title",
            "css:.latest-topic-list-item a.title",
            "css:a.title[href*='/t/topic/']",
            "css:a.title[href*='/t/']",
            "css:a[href*='/t/topic/']",
        ]

        topic_urls: List[str] = []
        seen = set()
        for selector in selectors:
            try:
                elements = self.page.eles(selector, timeout=3)
            except Exception:
                continue

            for element in elements or []:
                try:
                    href = (element.attr("href") or "").strip()
                except Exception:
                    continue
                if not href or href in seen:
                    continue
                if "/t/" not in href:
                    continue
                seen.add(href)
                topic_urls.append(href)

            if topic_urls:
                logger.info(
                    f"主题列表已就绪：使用选择器 {selector} 获取到 {len(topic_urls)} 个链接"
                )
                return topic_urls

        return topic_urls

    def collect_topic_urls(self) -> List[str]:
        current_url = ""
        try:
            current_url = self.page.url or ""
        except Exception:
            current_url = ""

        topic_urls = self.extract_topic_urls_from_current_page()
        if topic_urls:
            return topic_urls

        for target_url in [TOPIC_LIST_URL, HOME_URL]:
            if target_url and current_url.startswith(target_url):
                continue

            logger.info(
                f"当前页面未发现主题列表，尝试打开主题页: {target_url}"
            )
            try:
                self.page.get(target_url)
                time.sleep(5)
            except Exception as e:
                logger.warning(f"打开主题页失败: {target_url} ({e})")
                continue

            topic_urls = self.extract_topic_urls_from_current_page()
            if topic_urls:
                return topic_urls

        logger.warning(
            "已尝试当前页、主题列表页和首页，但仍未发现可浏览的主题链接"
        )
        return []

    @staticmethod
    def get_browse_exit_reason_label(reason: Optional[str]) -> str:
        labels = {
            "max_scrolls": "达到滚动上限",
            "random_exit": "随机结束",
            "bottom": "到达页面底部",
        }
        return labels.get(reason or "", "未知")

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_page = self.browser.new_tab()
        liked = False
        try:
            new_page.get(topic_url)
            result = self.browse_post(new_page)
            if random.random() < 0.3:
                liked = self.click_like_via_toggle(new_page, browse_result=result)
            result["liked"] = liked
            return result
        except Exception as e:
            logger.warning(f"打开或浏览主题失败: {topic_url} ({e})")
            return None
        finally:
            try:
                new_page.close()
            except Exception:
                pass

    @staticmethod
    def extract_topic_id_from_url(url: str) -> str:
        match = re.search(r"/t/(?:[^/?#]+/)?(\d+)(?:[/?#]|$)", url or "")
        return match.group(1) if match else ""

    def get_topic_timing_context(self, page) -> dict:
        script = """
const direct = Array.from(document.querySelectorAll('[data-post-number]'))
  .map((el) => parseInt((el.getAttribute('data-post-number') || '').trim(), 10))
  .filter(Number.isFinite);
const fromArticleId = Array.from(document.querySelectorAll('article[id^="post_"]'))
  .map((el) => {
    const match = String(el.id || '').match(/^post_(\\d+)$/);
    return match ? parseInt(match[1], 10) : NaN;
  })
  .filter(Number.isFinite);
const unique = Array.from(new Set(direct.concat(fromArticleId))).slice(0, 6);
return JSON.stringify({
  url: location.href,
  post_numbers: unique
});
"""
        page_url = str(getattr(page, "url", "") or "")
        post_numbers: List[int] = []
        try:
            raw = page.run_js(script)
            data = json.loads(raw) if isinstance(raw, str) else {}
            page_url = str(data.get("url") or page_url)
            for item in data.get("post_numbers") or []:
                try:
                    number = int(item)
                except (TypeError, ValueError):
                    continue
                if number > 0 and number not in post_numbers:
                    post_numbers.append(number)
        except Exception as e:
            logger.info(f"获取主题浏览打点上下文失败，继续使用 URL 回退: {e}")

        if not post_numbers:
            post_numbers = [1]

        return {
            "url": page_url,
            "topic_id": self.extract_topic_id_from_url(page_url),
            "post_numbers": post_numbers,
        }

    def report_topic_timings(self, page, csrf_token: str, browse_result: Optional[dict] = None) -> bool:
        if not csrf_token:
            logger.info("主题浏览打点缺少 CSRF token，跳过 topics/timings")
            return False

        context = self.get_topic_timing_context(page)
        topic_id = str(context.get("topic_id") or "").strip()
        page_url = str(context.get("url") or getattr(page, "url", "") or LOGIN_URL)
        if not topic_id:
            logger.info(f"未从当前页面 URL 中识别到 topic_id，跳过 topics/timings: url={page_url}")
            return False

        duration_ms = 1000
        if isinstance(browse_result, dict):
            try:
                duration_ms = int(browse_result.get("duration_ms") or duration_ms)
            except (TypeError, ValueError):
                duration_ms = 1000
        duration_ms = max(1000, duration_ms)

        payload = {
            f"timings[{post_number}]": duration_ms
            for post_number in (context.get("post_numbers") or [1])[:6]
        }
        payload["topic_time"] = duration_ms
        payload["topic_id"] = topic_id
        body = urlencode(payload)

        resp = self.browser_request(
            "POST",
            TOPICS_TIMINGS_URL,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Discourse-Background": "true",
                "Discourse-Logged-In": "true",
                "Discourse-Present": "true",
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "X-Silence-Logger": "true",
            },
            body=body,
            page=page,
        )
        if resp.get("status") == 200:
            logger.info(
                "topics/timings 打点成功: "
                f"topic_id={topic_id} topic_time={duration_ms}ms "
                f"post_numbers={context.get('post_numbers')}"
            )
            return True

        logger.warning(
            "topics/timings 打点失败: "
            f"topic_id={topic_id} status={resp.get('status')} "
            f"error={resp.get('error', '')} body={(resp.get('text') or '')[:200]}"
        )
        return False

    def get_like_target_state(self, page) -> dict:
        target_script = """
function clean(value) {
  return String(value || '').trim();
}

function extractPostIdFromReactionId(value) {
  const text = clean(value);
  if (!text) return '';
  const match = text.match(
    /^discourse-reactions-(?:actions|counter|list-emoji)-(\\d+)(?:-.+)?$/
  );
  return match ? match[1] : '';
}

function getLikeState(button) {
  if (!button) return { exists: false, liked: false, can_like: false };
  const title = clean(button.getAttribute('title'));
  const aria = clean(button.getAttribute('aria-label'));
  const cls = clean(button.className);
  const use = button.querySelector('use');
  const href = clean(use ? use.getAttribute('href') : '');
  const summary = `${title} ${aria} ${cls} ${href}`;
  const liked = (
    summary.includes('移除') ||
    summary.includes('取消') ||
    summary.includes('撤销') ||
    summary.includes('已赞') ||
    href.includes('#heart')
  ) && !href.includes('#far-heart');
  const canLike = !liked && (
    summary.includes('点赞') ||
    href.includes('#far-heart') ||
    summary.includes('d-unliked')
  );
  return { exists: true, liked, can_like: canLike, title, aria, cls, href };
}

function resolvePostId(button) {
  if (!button) return '';

  const article = button.closest('article[data-post-id], [data-post-id]');
  if (article) {
    const articlePostId = clean(
      article.getAttribute('data-post-id') ||
      (article.dataset ? article.dataset.postId : '')
    );
    if (articlePostId) return articlePostId;
  }

  const ancestors = [];
  let node = button;
  while (node && node !== document.documentElement) {
    ancestors.push(node);
    node = node.parentElement;
  }

  for (const item of ancestors) {
    const directPostId = clean(
      item.getAttribute && item.getAttribute('data-post-id')
    );
    if (directPostId) return directPostId;

    const datasetPostId = clean(item.dataset && item.dataset.postId);
    if (datasetPostId) return datasetPostId;

    const reactionPostId = extractPostIdFromReactionId(item.id);
    if (reactionPostId) return reactionPostId;
  }

  const scope = button.closest('.actions, .topic-post, article, [data-post-number]');
  if (!scope) return '';

  const scopedArticle = scope.querySelector('article[data-post-id], [data-post-id]');
  if (scopedArticle) {
    const scopedPostId = clean(
      scopedArticle.getAttribute('data-post-id') ||
      (scopedArticle.dataset ? scopedArticle.dataset.postId : '')
    );
    if (scopedPostId) return scopedPostId;
  }

  const reactionNode = scope.querySelector(
    '[id^="discourse-reactions-actions-"], [id^="discourse-reactions-counter-"], [id^="discourse-reactions-list-emoji-"]'
  );
  return reactionNode ? extractPostIdFromReactionId(reactionNode.id) : '';
}

const buttons = Array.from(
  document.querySelectorAll(
    'button.btn-toggle-reaction-like.reaction-button, .discourse-reactions-actions button.btn-toggle-reaction-like.reaction-button'
  )
);

let fallback = null;
for (const button of buttons) {
  const state = getLikeState(button);
  if (!state.exists) continue;

  const reactionRoot = button.closest(
    '[id^="discourse-reactions-actions-"], [id^="discourse-reactions-counter-"], [id^="discourse-reactions-list-emoji-"], .discourse-reactions-actions'
  );
  const candidate = {
    post_id: resolvePostId(button),
    url: location.href,
    reaction_id: clean(reactionRoot ? reactionRoot.id : ''),
    ...state
  };

  if (!candidate.post_id) {
    if (!fallback) fallback = candidate;
    continue;
  }

  if (candidate.can_like) {
    return JSON.stringify(candidate);
  }

  if (!fallback || !fallback.post_id) {
    fallback = candidate;
  }
}

return JSON.stringify(
  fallback || {
    post_id: '',
    url: location.href,
    reaction_id: '',
    exists: false,
    liked: false,
    can_like: false,
    button_count: buttons.length
  }
);
"""
        try:
            raw = page.run_js(target_script)
            return json.loads(raw) if isinstance(raw, str) else {}
        except Exception as e:
            logger.warning(f"读取点赞目标状态失败: {e}")
            return {}

    def page_has_challenge(self, page) -> bool:
        script = """
const title = (document.title || '').toLowerCase();
const text = (document.body ? (document.body.innerText || '') : '').slice(0, 3000).toLowerCase();
const html = (document.documentElement ? (document.documentElement.outerHTML || '') : '').slice(0, 4000).toLowerCase();
const summary = `${title}\n${text}\n${html}`;
return (
  summary.includes('just a moment') ||
  summary.includes('cloudflare') ||
  summary.includes('cf-chl') ||
  summary.includes('challenge-platform') ||
  summary.includes('please wait while your request is being verified')
);
"""
        try:
            return bool(page.run_js(script))
        except Exception:
            return False

    def click_like_button_in_page(self, page, post_id: str) -> bool:
        script = f"""
const targetPostId = {json.dumps(post_id)};

function clean(value) {{
  return String(value || '').trim();
}}

function extractPostIdFromReactionId(value) {{
  const text = clean(value);
  if (!text) return '';
  const match = text.match(
    /^discourse-reactions-(?:actions|counter|list-emoji)-(\\d+)(?:-.+)?$/
  );
  return match ? match[1] : '';
}}

function resolvePostId(button) {{
  if (!button) return '';

  const article = button.closest('article[data-post-id], [data-post-id]');
  if (article) {{
    const articlePostId = clean(
      article.getAttribute('data-post-id') ||
      (article.dataset ? article.dataset.postId : '')
    );
    if (articlePostId) return articlePostId;
  }}

  let node = button;
  while (node && node !== document.documentElement) {{
    const directPostId = clean(
      node.getAttribute && node.getAttribute('data-post-id')
    );
    if (directPostId) return directPostId;

    const datasetPostId = clean(node.dataset && node.dataset.postId);
    if (datasetPostId) return datasetPostId;

    const reactionPostId = extractPostIdFromReactionId(node.id);
    if (reactionPostId) return reactionPostId;
    node = node.parentElement;
  }}

  return '';
}}

const buttons = Array.from(
  document.querySelectorAll(
    'button.btn-toggle-reaction-like.reaction-button, .discourse-reactions-actions button.btn-toggle-reaction-like.reaction-button'
  )
);
for (const button of buttons) {{
  if (resolvePostId(button) !== targetPostId) continue;
  try {{
    button.scrollIntoView({{ behavior: 'instant', block: 'center', inline: 'center' }});
  }} catch (e) {{}}
  button.focus();
  button.click();
  return true;
}}
return false;
"""
        try:
            clicked = page.run_js(script)
            return bool(clicked)
        except Exception as e:
            logger.warning(f"页面内点击爱心按钮失败: post_id={post_id} error={e}")
            return False

    def click_like_via_toggle(self, page, browse_result: Optional[dict] = None):
        try:
            target = self.get_like_target_state(page)
            post_id = str(target.get("post_id") or "").strip()
            page_url = str(target.get("url") or page.url or "").strip()

            if not post_id:
                logger.info(
                    "当前帖子页未找到可点赞的 post_id: "
                    f"url={page_url} state={target}"
                )
                return False
            if target.get("liked"):
                logger.info(f"当前帖子已是点赞状态，跳过 toggle: post_id={post_id}")
                return False
            if not target.get("can_like"):
                logger.info(
                    "当前帖子页未识别到可直接点赞的状态: "
                    f"post_id={post_id} state={target}"
                )
                return False

            wait_before_click = random.uniform(1.2, 2.8)
            logger.info(
                f"准备点击页面爱心按钮: post_id={post_id} wait={wait_before_click:.1f}s"
            )
            time.sleep(wait_before_click)

            if not self.click_like_button_in_page(page, post_id):
                logger.warning(f"未能在页面中点击爱心按钮: post_id={post_id}")
                return False

            for attempt in range(1, 11):
                time.sleep(1)
                current = self.get_like_target_state(page)
                if str(current.get("post_id") or "").strip() == post_id and current.get("liked"):
                    logger.info(f"页面爱心按钮点赞成功: post_id={post_id}")
                    return True
                if self.page_has_challenge(page):
                    logger.warning(
                        "点击爱心按钮后页面进入 Cloudflare/挑战态: "
                        f"post_id={post_id} url={page_url}"
                    )
                    return False
                if attempt in (3, 6, 10):
                    logger.info(
                        "等待页面爱心按钮状态更新中: "
                        f"post_id={post_id} attempt={attempt}/10 state={current}"
                    )

            logger.warning(
                "点击爱心按钮后页面状态未更新为已赞: "
                f"post_id={post_id} url={page_url}"
            )
            return False
        except Exception as e:
            logger.warning(f"页面爱心按钮点赞失败: {str(e)}")
            return False

    def browse_post(self, page):
        prev_url = None
        scrolls = 0
        exit_reason = "max_scrolls"
        start_time = time.monotonic()

        for _ in range(10):
            scroll_distance = random.randint(550, 650)
            page.run_js(f"window.scrollBy(0, {scroll_distance})")
            scrolls += 1

            if random.random() < 0.03:
                exit_reason = "random_exit"
                break

            at_bottom = page.run_js(
                "window.scrollY + window.innerHeight >= document.body.scrollHeight"
            )
            current_url = page.url
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                exit_reason = "bottom"
                break

            wait_time = random.uniform(2, 4)
            time.sleep(wait_time)

        return {
            "scrolls": scrolls,
            "exit_reason": exit_reason,
            "final_url": page.url,
            "duration_ms": max(1000, int((time.monotonic() - start_time) * 1000)),
        }

    def run(self):
        try:
            login_res = False
            cookie_login_attempted = False
            if COOKIES and not SKIP_COOKIE_LOGIN:
                self.login_method = "cookie"
                cookie_login_attempted = True
                login_res = self.login_with_cookies(COOKIES)
                if not login_res:
                    if self.last_login_validation_blocked:
                        logger.warning(
                            "Cookie 登录校验被限流或挑战页拦截，跳过账号密码回退"
                        )
                    else:
                        logger.warning("Cookie 登录失败")
            elif COOKIES and SKIP_COOKIE_LOGIN:
                logger.info("已按配置跳过 Cookie 登录，直接进入账号密码登录流程")

            if not login_res:
                if cookie_login_attempted and self.last_login_validation_blocked:
                    self.send_failure_notification(
                        "Cookie 登录校验被限流或挑战页拦截，本次未回退账号密码登录"
                    )
                    return
                if USERNAME and PASSWORD:
                    self.login_method = "password"
                    if is_github_actions():
                        logger.warning("GitHub Actions 环境下已禁用账号密码登录回退，请改用 LINUXDO_COOKIES")
                        self.send_failure_notification("GitHub Actions 环境未启用账号密码登录")
                        return
                    logger.info("尝试使用账号密码登录，并在成功后自动刷新 Cookie...")
                    login_res = self.login()
                else:
                    logger.warning("未配置可用的登录凭据")
                    self.send_failure_notification("未配置可用的登录凭据")
                    return

            if not login_res:
                logger.warning("登录验证失败")
                self.send_failure_notification("登录验证失败")
                return

            logger.success(
                f"已确认登录: {self.login_name} "
                f"({self.get_login_verify_label()} 校验, {self.get_login_method_label()} 登录)"
            )

            if BROWSE_ENABLED:
                click_topic_res = self.click_topic()
                if not click_topic_res:
                    logger.error("点击主题失败，程序终止")
                    self.send_failure_notification("浏览任务失败")
                    return
                logger.info("完成浏览任务")

            try:
                self.print_connect_info()
            except Exception as e:
                self.connect_summary = f"获取失败: {e}"
                logger.warning(f"获取 Connect 信息失败: {e}")
            self.send_notifications(BROWSE_ENABLED)
        finally:
            try:
                self.page.close()
            except Exception:
                pass
            try:
                self.browser.quit()
            except Exception:
                pass

    def click_like(self, page):
        return self.click_like_via_toggle(page)

    def print_connect_info(self):
        logger.info("获取连接信息")
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
        }
        resp = self.session.get(
            CONNECT_URL, headers=headers, impersonate=DEFAULT_IMPERSONATE
        )
        if resp.status_code != 200:
            self.connect_summary = f"获取失败 (HTTP {resp.status_code})"
            logger.warning(f"获取 Connect 信息失败: {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tr")
        info = []

        for row in rows:
            cells = row.select("td")
            if len(cells) >= 3:
                project = cells[0].text.strip()
                current = cells[1].text.strip() if cells[1].text.strip() else "0"
                requirement = cells[2].text.strip() if cells[2].text.strip() else "0"
                info.append([project, current, requirement])

        self.connect_summary = f"已获取 {len(info)} 项" if info else "未获取到有效数据"
        logger.info("--------------Connect Info-----------------")
        logger.info("\n" + tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        return info

    def send_failure_notification(self, reason: str):
        status_msg = "\n".join(
            [
                "❌ LINUX DO 任务失败",
                f"账号: {self.login_name}",
                f"登录方式: {self.get_login_method_label()}",
                (
                    f"登录状态: 已确认 ({self.get_login_verify_label()} 校验)"
                    if self.login_verified
                    else "登录状态: 未确认"
                ),
                f"原因: {reason}",
            ]
        )
        self.notifier.send_all("LINUX DO", status_msg)

    def send_notifications(self, browse_enabled):
        status_lines = [
            "✅ LINUX DO 任务完成",
            f"账号: {self.login_name}",
            f"登录确认: 已登录 ({self.get_login_verify_label()} 校验)",
            f"登录方式: {self.get_login_method_label()}",
            "浏览任务: 已完成" if browse_enabled else "浏览任务: 已关闭",
        ]
        if self.connect_summary:
            status_lines.append(f"Connect 信息: {self.connect_summary}")
        status_msg = "\n".join(status_lines)
        self.notifier.send_all("LINUX DO", status_msg)


def run_configured_tasks() -> None:
    has_linuxdo_credentials = bool(COOKIES or (USERNAME and PASSWORD))
    has_v2ex_credentials = bool(V2EX_ENABLED and V2EX_COOKIE)
    nodeseek_accounts = collect_nodeseek_accounts() if NODESEEK_ENABLED else []
    has_nodeseek_credentials = bool(NODESEEK_ENABLED and nodeseek_accounts)
    has_xiaoheihe_credentials = bool(XIAOHEIHE_COOKIE)

    logger.info(
        "Runtime task summary: "
        f"linuxdo={has_linuxdo_credentials}, "
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
        not has_linuxdo_credentials
        and not has_v2ex_credentials
        and not has_xiaoheihe_credentials
        and not has_nodeseek_credentials
    ):
        print(
            "请设置 LINUXDO_COOKIES（推荐），或在 VPS 上设置 "
            "LINUXDO_USERNAME 和 LINUXDO_PASSWORD；"
            "如需启用 V2EX，请设置 V2EX_COOKIE 或 V2EX_A2；"
            "如需启用小黑盒，请设置 XIAOHEIHE_COOKIE；"
            "如需启用 NodeSeek，请设置 NODESEEK_COOKIE 或 NODESEEK_USERNAME / NODESEEK_PASSWORD"
        )
        exit(1)

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
            ).run()
    elif NODESEEK_ENABLED:
        logger.info("未配置 NodeSeek 登录信息，跳过 NodeSeek 每日签到")

    if has_xiaoheihe_credentials:
        log_xiaoheihe_mode(XIAOHEIHE_REQUEST_MODE)
        XiaoHeiHeDailyMission(
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

    if has_linuxdo_credentials:
        browser = LinuxDoBrowser()
        browser.run()
    else:
        logger.info("未配置 LinuxDo 登录信息，跳过 LinuxDo 任务")


if __name__ == "__main__":
    run_configured_tasks()
