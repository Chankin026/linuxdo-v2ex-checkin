#!/usr/bin/env python3
import argparse
import functools
import hashlib
import importlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.const import CurlIpResolve, CurlOpt
from loguru import logger
from notify import NotificationManager

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
    repo_dir = Path(__file__).resolve().parent
    candidates: List[str] = []
    env_file_hint = os.environ.get("LINUXDO_ENV_FILE", "").strip()
    if env_file_hint:
        candidates.append(env_file_hint)

    candidates.extend(
        [
            str(repo_dir / "linuxdo-v2ex-checkin.env"),
            str(repo_dir / ".env"),
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


def retry_decorator(retries: int = 3, min_delay: int = 5, max_delay: int = 10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {exc}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {exc}"
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


def load_cloakbrowser():
    try:
        module = importlib.import_module("cloakbrowser")
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "cloakbrowser is not installed. Run: pip install cloakbrowser"
        ) from exc

    return module.launch_context, module.launch_persistent_context


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
    ) -> None:
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
        user_agent: str = "",
        captcha_type: str = "turnstile",
    ) -> str:
        task_id = self._create_task(url, sitekey, user_agent, captcha_type)
        if not task_id:
            raise YesCaptchaSolverError("创建 YesCaptcha 任务失败")
        token = self._get_task_result(task_id)
        if not token:
            raise YesCaptchaSolverError("获取 YesCaptcha 结果失败")
        return token

    def _create_task(
        self,
        url: str,
        sitekey: str,
        user_agent: str,
        captcha_type: str,
    ) -> str:
        if captcha_type == "hcaptcha":
            task_type = "HCaptchaTaskProxyless"
        else:
            task_type = (
                "TurnstileTaskProxylessM1" if self.advanced else "TurnstileTaskProxyless"
            )
        payload = {
            "clientKey": self.client_key,
            "task": {
                "type": task_type,
                "websiteURL": url,
                "websiteKey": sitekey,
            },
            "softID": "62709",
        }
        if user_agent:
            payload["task"]["userAgent"] = user_agent

        try:
            response = requests.post(
                self.create_task_url,
                json=payload,
                timeout=self.timeout,
                impersonate="chrome110",
            )
            result = response.json()
        except Exception as exc:
            raise YesCaptchaSolverError(f"创建任务请求失败: {exc}") from exc

        if result.get("errorId") == 0 and result.get("taskId"):
            return str(result["taskId"])
        raise YesCaptchaSolverError(
            result.get("errorDescription") or "YesCaptcha createTask 返回失败"
        )

    def _get_task_result(self, task_id: str) -> str:
        payload = {"clientKey": self.client_key, "taskId": task_id}
        for _ in range(self.max_retries):
            try:
                response = requests.post(
                    self.get_result_url,
                    json=payload,
                    timeout=self.timeout,
                    impersonate="chrome110",
                )
                result = response.json()
            except Exception as exc:
                raise YesCaptchaSolverError(f"查询任务结果失败: {exc}") from exc

            if result.get("errorId", 0) > 0:
                raise YesCaptchaSolverError(
                    result.get("errorDescription")
                    or "YesCaptcha getTaskResult 返回失败"
                )

            if result.get("status") == "ready":
                solution = result.get("solution", {})
                token = solution.get("token") or solution.get("gRecaptchaResponse")
                if token:
                    return str(token)
                raise YesCaptchaSolverError("YesCaptcha 返回 ready 但没有 token")

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
SKIP_COOKIE_LOGIN = env_bool("LINUXDO_SKIP_COOKIE_LOGIN", False)
GH_PAT = env_str("GH_PAT")
ENV_FILE_PATH = env_str("LINUXDO_ENV_FILE", resolve_default_env_file_path())
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
    env_int("YESCAPTCHA_HCAPTCHA_MAX_RETRIES", 45),
)
YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL = env_int(
    "LINUXDO_YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL",
    env_int("YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL", 4),
)
YESCAPTCHA_HCAPTCHA_TIMEOUT = env_int(
    "LINUXDO_YESCAPTCHA_HCAPTCHA_TIMEOUT",
    env_int("YESCAPTCHA_HCAPTCHA_TIMEOUT", 600),
)
BROWSE_ENABLED = env_bool("BROWSE_ENABLED", True)
FORCE_IPV4 = True
DEFAULT_IMPERSONATE = env_str("IMPERSONATE_VERSION", "chrome136") or "chrome136"
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
    except Exception as exc:
        logger.warning(f"保存 Cookie 到 GitHub 异常: {exc}")
        return False


def save_cookie_to_env_file(var_name: str, cookie: str) -> bool:
    if not ENV_FILE_PATH:
        logger.info("未配置 LINUXDO_ENV_FILE，跳过本地环境文件回写")
        return False
    if not cookie:
        logger.warning("Cookie 为空，跳过本地环境文件回写")
        return False

    try:
        lines: List[str] = []
        if os.path.exists(ENV_FILE_PATH):
            with open(ENV_FILE_PATH, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()

        updated = False
        new_lines: List[str] = []
        for line in lines:
            if line.startswith(f"{var_name}="):
                new_lines.append(f"{var_name}={cookie}")
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            new_lines.append(f"{var_name}={cookie}")

        with open(ENV_FILE_PATH, "w", encoding="utf-8") as handle:
            handle.write("\n".join(new_lines).rstrip() + "\n")

        logger.info(f"已将 {var_name} 回写到 {ENV_FILE_PATH}")
        return True
    except Exception as exc:
        logger.warning(f"回写 Cookie 到 {ENV_FILE_PATH} 失败: {exc}")
        return False


def parse_cookie_string(cookie_str: str) -> List[Dict[str, str]]:
    cookies: List[Dict[str, str]] = []
    for part in cookie_str.split(";"):
        segment = part.strip()
        if not segment or "=" not in segment:
            continue
        name, value = segment.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": "linux.do",
                "path": "/",
            }
        )
    return cookies


def get_cookie_names(context) -> List[str]:
    try:
        return sorted(
            {cookie.get("name", "") for cookie in context.cookies() if cookie.get("name")}
        )
    except Exception:
        return []


def get_page_state(page) -> Dict[str, object]:
    script = """
() => {
  const text = (document.body && document.body.innerText) || '';
  const lowered = text.toLowerCase();
  const locationHref = window.location.href || '';
  const title = document.title || '';
  const loginForm = document.querySelector('form#login-form, form[action*="/session"], form[action*="/login"]');
  const loginInput = document.querySelector('input[name="login"], input[type="email"], #login-account-name');
  const passwordInput = document.querySelector('input[name="password"], input[type="password"], #login-account-password');
  const turnstileNode = document.querySelector('.cf-turnstile,[data-sitekey],iframe[src*="turnstile"]');
  const hcaptchaNode = document.querySelector('.h-captcha,[data-hcaptcha-sitekey],iframe[src*="hcaptcha.com"]');
  const challenge = (
    lowered.includes('just a moment') ||
    lowered.includes('checking your browser') ||
    lowered.includes('enable javascript') ||
    (lowered.includes('challenge') && lowered.includes('cloudflare'))
  );
  const accountHints = ['/my/preferences/account', '/u/'];
  const currentUserMeta = document.querySelector('meta[name="current-user"]');
  const accountNav = !!document.querySelector('a[href*="/my/preferences"], a[href*="/u/"]');
  const logoutButton = !!document.querySelector('button[title*="log out"], a[href*="/logout"]');
  return {
    url: locationHref,
    title,
    has_login_form: !!loginForm,
    has_login_input: !!loginInput,
    has_password_input: !!passwordInput,
    has_turnstile: !!turnstileNode,
    has_hcaptcha: !!hcaptchaNode,
    is_challenge: challenge,
    has_current_user: !!(currentUserMeta && currentUserMeta.content),
    has_account_nav: accountNav,
    has_logout: logoutButton,
    looks_logged_in: accountHints.some(hint => locationHref.includes(hint)) || !!(currentUserMeta && currentUserMeta.content) || accountNav || logoutButton,
    text_snippet: text.replace(/\\s+/g, ' ').trim().slice(0, 240),
  };
}
"""
    return page.evaluate(script)


def page_looks_logged_in(state: Dict[str, object]) -> bool:
    if state.get("looks_logged_in"):
        return True
    url = str(state.get("url") or "")
    if "/my/preferences/account" in url:
        return True
    if "/u/" in url and "preferences" in url:
        return True
    return False


def wait_for_settle(seconds: float) -> None:
    time.sleep(seconds)


def navigate_and_capture(page, url: str, label: str) -> Dict[str, object]:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    wait_for_settle(4)
    state = get_page_state(page)
    logger.info(
        f"[{label}] url={state.get('url')} title={state.get('title')} "
        f"challenge={state.get('is_challenge')} turnstile={state.get('has_turnstile')} "
        f"hcaptcha={state.get('has_hcaptcha')} logged_in={state.get('looks_logged_in')}"
    )
    return state


def find_first(page, selectors: List[str]):
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    return None


def locator_exists(locator) -> bool:
    return locator is not None


def detect_turnstile_sitekey(page) -> str:
    script = """
() => {
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
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        result = ""
    return result.strip() if isinstance(result, str) else ""


def inject_turnstile_token(page, token: str) -> bool:
    script = f"""
() => {{
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
    const form = document.querySelector('form#login-form') || document.querySelector('form');
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
}}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def detect_hcaptcha_sitekey(page) -> str:
    script = """
() => {
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
  return '';
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        result = ""
    return result.strip() if isinstance(result, str) else ""


def install_hcaptcha_callback_capture(page) -> bool:
    script = """
() => {
  window.__linuxdoHCaptchaCallbacks = window.__linuxdoHCaptchaCallbacks || [];
  window.__linuxdoWrapHCaptcha = window.__linuxdoWrapHCaptcha || ((api) => {
    if (!api || api.__linuxdoCallbackWrapped || typeof api.render !== 'function') {
      return false;
    }
    const originalRender = api.render.bind(api);
    api.render = (container, options = {}, ...rest) => {
      const wrappedOptions = { ...options };
      if (typeof options.callback === 'function') {
        window.__linuxdoHCaptchaCallbacks.push(options.callback);
        wrappedOptions.callback = (token, ...callbackArgs) => {
          window.__linuxdoLastHCaptchaToken = token;
          return options.callback(token, ...callbackArgs);
        };
      }
      const widgetId = originalRender(container, wrappedOptions, ...rest);
      window.__linuxdoLastHCaptchaWidgetId = widgetId;
      return widgetId;
    };
    api.__linuxdoCallbackWrapped = true;
    return true;
  });

  const existingCallback = window.discourseHCaptchaCallback;
  if (!window.__linuxdoDiscourseHCaptchaCallbackWrapped) {
    window.discourseHCaptchaCallback = function(...args) {
      window.__linuxdoWrapHCaptcha(window.hcaptcha || args[0]);
      if (typeof existingCallback === 'function') {
        return existingCallback.apply(this, args);
      }
    };
    window.__linuxdoDiscourseHCaptchaCallbackWrapped = true;
  }
  return window.__linuxdoWrapHCaptcha(window.hcaptcha);
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def inject_hcaptcha_token(page, token: str) -> bool:
    script = f"""
() => {{
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
    const form = document.querySelector('form#login-form') || document.querySelector('form');
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
    el.setAttribute('value', token);
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}
  for (const iframe of document.querySelectorAll('iframe[data-hcaptcha-widget-id]')) {{
    iframe.setAttribute('data-hcaptcha-response', token);
  }}
  const api = window.hcaptcha;
  if (api) {{
    if (!api.__linuxdoOriginalGetResponse && typeof api.getResponse === 'function') {{
      api.__linuxdoOriginalGetResponse = api.getResponse.bind(api);
    }}
    if (!api.__linuxdoOriginalGetRespKey && typeof api.getRespKey === 'function') {{
      api.__linuxdoOriginalGetRespKey = api.getRespKey.bind(api);
    }}
    api.getResponse = () => token;
    api.getRespKey = () => token;
  }}
  for (const callback of window.__linuxdoHCaptchaCallbacks || []) {{
    try {{ callback(token); }} catch (e) {{}}
  }}
  window.__hcaptchaToken = token;
  window.__hcaptchaResponse = token;
  window.__linuxdoLastHCaptchaToken = token;
  return elements.length > 0;
}}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def submit_login_with_frontend_controller(page, username: str, password: str) -> Dict[str, object]:
    if not username or not password:
        return {"ok": False, "reason": "missing_credentials"}
    script = f"""
async () => {{
  const owner = window.Discourse && window.Discourse.__container__;
  const controller = owner && owner.lookup && owner.lookup('controller:login');
  if (!controller || typeof controller.localLogin !== 'function') {{
    return {{ ok: false, reason: 'missing_login_controller' }};
  }}
  const resetLoggingIn = () => {{
    try {{
      if (typeof controller.set === 'function') {{
        controller.set('loggingIn', false);
      }} else {{
        controller.loggingIn = false;
      }}
    }} catch (e) {{
      try {{ controller.loggingIn = false; }} catch (_) {{}}
    }}
  }};
  try {{
    if (typeof controller.set === 'function') {{
      controller.set('loginName', {json.dumps(username)});
      controller.set('loginPassword', {json.dumps(password)});
      controller.set('loggingIn', false);
    }} else {{
      controller.loginName = {json.dumps(username)};
      controller.loginPassword = {json.dumps(password)};
      controller.loggingIn = false;
    }}
    const timeoutMs = 15000;
    const loginResult = await Promise.race([
      Promise.resolve().then(() => controller.localLogin()).then(() => ({{ timedOut: false }})),
      new Promise(resolve => window.setTimeout(() => resolve({{ timedOut: true }}), timeoutMs))
    ]);
    if (loginResult && loginResult.timedOut) {{
      resetLoggingIn();
      return {{
        ok: false,
        reason: 'frontend_timeout',
        timeoutMs,
        loggedIn: !!controller.loggedIn,
        loggingIn: !!controller.loggingIn,
        flash: controller.flash || '',
        flashType: controller.flashType || ''
      }};
    }}
    return {{
      ok: true,
      loggedIn: !!controller.loggedIn,
      loggingIn: !!controller.loggingIn,
      flash: controller.flash || '',
      flashType: controller.flashType || '',
      showSecondFactor: !!controller.showSecondFactor,
      showSecurityKey: !!controller.showSecurityKey
    }};
  }} catch (e) {{
    resetLoggingIn();
    return {{
      ok: false,
      reason: 'exception',
      error: String(e),
      status: e && e.jqXHR && e.jqXHR.status,
      body: e && e.jqXHR && (e.jqXHR.responseText || JSON.stringify(e.jqXHR.responseJSON || {{}}))
    }};
  }}
}}
"""
    try:
        result = page.evaluate(script)
    except Exception as exc:
        return {"ok": False, "reason": "evaluate_failed", "error": str(exc)}
    return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected_result"}


def get_csrf_token(page) -> str:
    script = """
() => {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) {
    return meta.content;
  }
  return '';
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        result = ""
    return result.strip() if isinstance(result, str) else ""


def get_captcha_response_fields(page) -> Dict[str, str]:
    script = """
() => {
  const fields = {};
  const selectors = [
    'textarea[name*="captcha"]',
    'input[name*="captcha"]',
    'textarea[name*="turnstile"]',
    'input[name*="turnstile"]'
  ];
  for (const el of document.querySelectorAll(selectors.join(','))) {
    const name = (el.getAttribute('name') || '').trim();
    const value = (el.value || el.getAttribute('value') || '').trim();
    if (name && value) {
      fields[name] = value;
    }
  }
  return fields;
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        return {}
    if not isinstance(result, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in result.items()
        if str(name).strip() and str(value).strip()
    }


def hcaptcha_checkbox_looks_solved(frame) -> bool:
    try:
        checkbox = frame.locator("#checkbox")
        if checkbox.count() == 0:
            return False
        return (checkbox.get_attribute("aria-checked") or "").strip().lower() == "true"
    except Exception:
        return False


def try_click_hcaptcha_checkbox_if_needed(page) -> bool:
    try:
        frame = page.frame_locator('iframe[src*="hcaptcha.com"]')
        checkbox = frame.locator("#checkbox")
        if checkbox.count() == 0:
            return False
        if hcaptcha_checkbox_looks_solved(frame):
            return True
        checkbox.click()
        wait_for_settle(2)
        return hcaptcha_checkbox_looks_solved(frame)
    except Exception:
        return False


class LinuxDoCloakBrowser:
    def __init__(
        self,
        headless: bool = False,
        user_data_dir: str = "",
        notifier: Optional[NotificationManager] = None,
    ) -> None:
        launch_context, launch_persistent_context = load_cloakbrowser()
        launch_kwargs = {
            "headless": headless,
            "humanize": True,
            "human_preset": "default",
        }
        if user_data_dir:
            self.context = launch_persistent_context(user_data_dir, **launch_kwargs)
        else:
            self.context = launch_context(**launch_kwargs)
        self.page = self.context.new_page()
        self.session = requests.Session()
        if FORCE_IPV4:
            self.session.curl_options = {
                **getattr(self.session, "curl_options", {}),
                CurlOpt.IPRESOLVE: CurlIpResolve.V4,
            }
        self.session.headers.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        self.notifier = notifier or NotificationManager()
        self.login_name = USERNAME or "Cookie 用户"
        self.login_method = ""
        self.login_verified = False
        self.login_verify_source = ""
        self.connect_summary = ""
        self.browse_stats = {
            "topics_total": 0,
            "topics_planned": 0,
            "topics_completed": 0,
            "likes": 0,
        }

    def close(self) -> None:
        try:
            self.context.close()
        except Exception:
            pass

    @staticmethod
    def normalize_cookie_string(cookie_str: str) -> str:
        latest_by_name: Dict[str, str] = {}
        for part in cookie_str.strip().split(";"):
            segment = part.strip()
            if "=" not in segment:
                continue
            name, _, value = segment.partition("=")
            normalized_name = name.strip()
            if not normalized_name:
                continue
            if normalized_name in latest_by_name:
                latest_by_name.pop(normalized_name, None)
            latest_by_name[normalized_name] = value.strip()
        return "; ".join(f"{name}={value}" for name, value in latest_by_name.items())

    def get_context_cookie_string(self) -> str:
        try:
            cookies = self.context.cookies()
        except Exception:
            cookies = []
        latest_by_name: Dict[str, str] = {}
        for cookie in cookies or []:
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "").strip()
            if not name:
                continue
            if name in latest_by_name:
                latest_by_name.pop(name, None)
            latest_by_name[name] = value
        return "; ".join(f"{name}={value}" for name, value in latest_by_name.items())

    def sync_session_from_cookie_string(self, cookie_str: str) -> None:
        for cookie in parse_cookie_string(cookie_str):
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
            )

    def sync_session_from_context(self) -> str:
        cookie_str = self.get_context_cookie_string()
        if cookie_str:
            self.sync_session_from_cookie_string(cookie_str)
        return cookie_str

    def summarize_browser_cookies(self, cookie_str: str = "") -> str:
        cookie_str = cookie_str or self.get_context_cookie_string()
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

    def log_browser_cookie_summary(self, label: str, cookie_str: str = "") -> str:
        cookie_str = cookie_str or self.sync_session_from_context()
        logger.info(f"{label} Cookie 摘要: {self.summarize_browser_cookies(cookie_str)}")
        return cookie_str

    def persist_cookie_if_possible(self, cookie_str: str) -> None:
        if not cookie_str:
            logger.warning("未获取到可持久化的 Cookie")
            return
        normalized = self.normalize_cookie_string(cookie_str)
        if is_github_actions():
            save_cookie_to_github_var("LINUXDO_COOKIES", normalized)
        else:
            save_cookie_to_env_file("LINUXDO_COOKIES", normalized)

    def mark_login_verified(self, source: str) -> None:
        self.login_verified = True
        self.login_verify_source = source

    def get_login_verify_label(self) -> str:
        return {
            "api": "current session API",
            "account_page": "账号设置页",
        }.get(self.login_verify_source, self.login_verify_source or "未知")

    def get_login_method_label(self) -> str:
        return {
            "cookie": "Cookie",
            "password": "账号密码",
        }.get(self.login_method, self.login_method or "未知")

    def validate_login_via_api(self) -> bool:
        self.sync_session_from_context()
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
        except Exception as exc:
            logger.info(f"通过 API 校验登录状态异常，继续回退到页面校验: {exc}")
            return False

        if resp.status_code != 200:
            logger.info(
                f"通过 API 未直接确认登录态，继续回退到页面校验: "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
            return False

        try:
            data = resp.json()
        except Exception as exc:
            logger.info(f"解析 current session 响应失败，继续回退到页面校验: {exc}")
            return False

        current_user = data.get("current_user")
        if current_user and current_user.get("username"):
            self.login_name = current_user.get("username") or self.login_name
            self.mark_login_verified("api")
            logger.info(f"通过 API 校验登录成功: {self.login_name}")
            return True

        logger.info(f"API 响应中未识别到 current_user，继续回退到页面校验: {str(data)[:200]}")
        return False

    def validate_login(self) -> Tuple[bool, Dict[str, object]]:
        logger.info("验证登录状态，优先通过 current session API，再回退到账号设置页...")
        if self.validate_login_via_api():
            state = navigate_and_capture(self.page, ACCOUNT_PREFERENCES_URL, "validate")
            return True, state

        state = navigate_and_capture(self.page, ACCOUNT_PREFERENCES_URL, "validate")
        if page_looks_logged_in(state):
            self.update_login_name_from_page()
            self.mark_login_verified("account_page")
            self.sync_session_from_context()
            logger.info("通过账号设置页验证登录成功")
            return True, state

        cookie_names = get_cookie_names(self.context)
        logger.info(f"[validate] cookie_names={cookie_names}")
        return False, state

    def update_login_name_from_page(self) -> None:
        script = """
() => {
  const candidates = [
    document.querySelector('.user-menu .username'),
    document.querySelector('.username'),
    document.querySelector('meta[name="current-user"]')
  ];
  for (const node of candidates) {
    if (!node) continue;
    const value = (node.content || node.innerText || '').trim();
    if (value) return value;
  }
  return '';
}
"""
        try:
            username = self.page.evaluate(script)
        except Exception:
            username = ""
        if isinstance(username, str) and username.strip():
            self.login_name = username.strip()

    def solve_turnstile_if_needed(self, page=None) -> str:
        page = page or self.page
        sitekey = detect_turnstile_sitekey(page)
        if not sitekey:
            logger.info("[turnstile] no sitekey detected")
            return ""
        if not YESCAPTCHA_CLIENT_KEY:
            logger.info("[turnstile] solver not configured")
            return ""
        logger.info(f"[turnstile] detected sitekey={sitekey[:12]}...")
        solver = YesCaptchaSolver(
            api_base_url=YESCAPTCHA_API_BASE_URL,
            client_key=YESCAPTCHA_CLIENT_KEY,
            max_retries=YESCAPTCHA_MAX_RETRIES,
            retry_interval=YESCAPTCHA_RETRY_INTERVAL,
            timeout=YESCAPTCHA_TIMEOUT,
            advanced=YESCAPTCHA_ADVANCED,
        )
        user_agent = page.evaluate("() => navigator.userAgent")
        token = solver.solve(LOGIN_URL, sitekey, user_agent=user_agent, captcha_type="turnstile")
        inject_turnstile_token(page, token)
        logger.info("[turnstile] token injected")
        return token

    def solve_hcaptcha_if_needed(self, page=None) -> str:
        page = page or self.page
        sitekey = detect_hcaptcha_sitekey(page)
        if not sitekey:
            logger.info("[hcaptcha] no sitekey detected")
            return ""
        if not YESCAPTCHA_CLIENT_KEY:
            logger.info("[hcaptcha] solver not configured")
            return ""
        logger.info(f"[hcaptcha] detected sitekey={sitekey[:12]}...")
        solver = YesCaptchaSolver(
            api_base_url=YESCAPTCHA_API_BASE_URL,
            client_key=YESCAPTCHA_CLIENT_KEY,
            max_retries=YESCAPTCHA_HCAPTCHA_MAX_RETRIES,
            retry_interval=YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL,
            timeout=YESCAPTCHA_HCAPTCHA_TIMEOUT,
            advanced=YESCAPTCHA_ADVANCED,
        )
        user_agent = page.evaluate("() => navigator.userAgent")
        token = solver.solve(LOGIN_URL, sitekey, user_agent=user_agent, captcha_type="hcaptcha")
        inject_hcaptcha_token(page, token)
        logger.info("[hcaptcha] token injected")
        return token

    def browser_fetch(
        self,
        page,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> Dict[str, object]:
        headers = headers or {}
        script = f"""
async () => {{
  const response = await fetch({json.dumps(url)}, {{
    method: {json.dumps(method)},
    headers: {json.dumps(headers)},
    body: {json.dumps(body) if body is not None else 'undefined'},
    credentials: 'same-origin'
  }});
  return {{
    ok: response.ok,
    status: response.status,
    url: response.url,
    body: await response.text()
  }};
}}
"""
        try:
            result = page.evaluate(script)
        except Exception as exc:
            return {"ok": False, "status": 0, "url": url, "body": str(exc)}
        return result if isinstance(result, dict) else {"ok": False, "status": 0, "url": url, "body": ""}

    def fetch_csrf_token_from_linuxdo(self, page=None) -> Dict[str, object]:
        page = page or self.page
        result = self.browser_fetch(
            page,
            "GET",
            "/session/csrf",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        body = str(result.get("body", ""))
        token = ""
        try:
            parsed = json.loads(body) if body else {}
            if isinstance(parsed, dict):
                token = str(parsed.get("csrf") or "")
        except json.JSONDecodeError:
            token = ""
        result["token"] = token
        return result

    def register_hcaptcha_token_with_linuxdo(self, page, csrf_token: str, token: str) -> Dict[str, object]:
        if not csrf_token or not token:
            return {"success": False, "status": 0, "body": "", "json": {}}
        result = self.browser_fetch(
            page,
            "POST",
            "/hcaptcha/create.json",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
            },
            body=urlencode({"token": token}),
        )
        body = str(result.get("body", ""))
        parsed: Dict[str, object] = {}
        try:
            maybe = json.loads(body) if body else {}
            if isinstance(maybe, dict):
                parsed = maybe
        except json.JSONDecodeError:
            parsed = {}
        result["json"] = parsed
        result["success"] = bool(result.get("ok")) and parsed.get("success") == "OK"
        return result

    def submit_login_with_linuxdo(
        self,
        page,
        csrf_token: str,
        username: str,
        password: str,
        timezone: str,
        hcaptcha_token: str = "",
    ) -> Dict[str, object]:
        if not csrf_token or not username or not password:
            return {"ok": False, "status": 0, "url": "", "body": ""}
        form_data = {
            "login": username,
            "password": password,
            "timezone": timezone or "Asia/Shanghai",
        }
        if hcaptcha_token:
            form_data.update(
                {
                    "h-captcha-response": hcaptcha_token,
                    "g-recaptcha-response": hcaptcha_token,
                    "hcaptcha_token": hcaptcha_token,
                }
            )
        form_data.update(get_captcha_response_fields(page))
        return self.browser_fetch(
            page,
            "POST",
            "/session",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-CSRF-Token": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Discourse-Present": "true",
                "Accept": "*/*",
            },
            body=urlencode(form_data),
        )

    def try_cookie_login(self, cookie_str: str) -> Tuple[bool, Dict[str, object]]:
        cookies = parse_cookie_string(cookie_str)
        if not cookies:
            logger.info("[cookie] no parseable cookies")
            return False, {}

        navigate_and_capture(self.page, HOME_URL, "cookie-home-before")
        self.context.add_cookies(cookies)
        self.sync_session_from_cookie_string(cookie_str)
        state = navigate_and_capture(self.page, HOME_URL, "cookie-home-after")
        ok, validation_state = self.validate_login()
        return ok, validation_state or state

    def try_password_login(self, username: str, password: str) -> Tuple[bool, Dict[str, object]]:
        state = navigate_and_capture(self.page, LOGIN_URL, "password-login")
        try:
            self.solve_turnstile_if_needed(self.page)
            wait_for_settle(2)
        except Exception as exc:
            logger.warning(f"[turnstile] solve failed: {exc}")

        login_input = find_first(
            self.page,
            [
                'input[name="login"]',
                'input[type="email"]',
                '#login-account-name',
                'input[autocomplete="username"]',
            ],
        )
        password_input = find_first(
            self.page,
            [
                '#login-account-password',
                'form#login-form input[type="password"]',
                'input[name="password"]',
                'input[type="password"]',
                'input[autocomplete="current-password"]',
            ],
        )
        submit_button = find_first(
            self.page,
            [
                '#login-button',
                'button.login-page-cta__login',
                'form#login-form button',
                'button[type="submit"]',
                'form button',
            ],
        )
        if not (locator_exists(login_input) and locator_exists(password_input) and locator_exists(submit_button)):
            logger.warning("[password] login form not ready")
            return False, state

        install_hcaptcha_callback_capture(self.page)
        login_input.fill(username)
        password_input.fill(password)
        wait_for_settle(1)
        submit_button.click()
        wait_for_settle(8)

        post_state = get_page_state(self.page)
        logger.info(
            f"[password-post] url={post_state.get('url')} challenge={post_state.get('is_challenge')} "
            f"turnstile={post_state.get('has_turnstile')} hcaptcha={post_state.get('has_hcaptcha')} "
            f"logged_in={post_state.get('looks_logged_in')}"
        )
        if post_state.get("has_hcaptcha"):
            try:
                checkbox_clicked = try_click_hcaptcha_checkbox_if_needed(self.page)
                logger.info(f"[hcaptcha] checkbox_clicked={checkbox_clicked}")
                wait_for_settle(2)
                post_state = get_page_state(self.page)
                if not post_state.get("has_hcaptcha"):
                    return self.validate_login()

                hcaptcha_token = self.solve_hcaptcha_if_needed(self.page)
                wait_for_settle(2)
                csrf_info = self.fetch_csrf_token_from_linuxdo(self.page)
                csrf_token = str(csrf_info.get("token") or get_csrf_token(self.page))
                logger.info(
                    f"[linuxdo-csrf] status={csrf_info.get('status')} token_present={bool(csrf_token)} "
                    f"body={str(csrf_info.get('body', ''))[:200]}"
                )
                registered = self.register_hcaptcha_token_with_linuxdo(
                    self.page, csrf_token, hcaptcha_token
                )
                logger.info(
                    f"[hcaptcha] registered_with_linuxdo={registered.get('success')} "
                    f"status={registered.get('status')} body={str(registered.get('body', ''))[:200]}"
                )
                if registered.get("success") or hcaptcha_token:
                    frontend_result = submit_login_with_frontend_controller(
                        self.page, username, password
                    )
                    logger.info(
                        f"[linuxdo-frontend-login] ok={frontend_result.get('ok')} "
                        f"logged_in={frontend_result.get('loggedIn')} "
                        f"reason={frontend_result.get('reason')} "
                        f"flash={str(frontend_result.get('flash', ''))[:120]}"
                    )
                    wait_for_settle(10)
                    post_state = get_page_state(self.page)
                    logger.info(
                        f"[password-post-frontend] url={post_state.get('url')} "
                        f"challenge={post_state.get('is_challenge')} "
                        f"turnstile={post_state.get('has_turnstile')} "
                        f"hcaptcha={post_state.get('has_hcaptcha')} "
                        f"logged_in={post_state.get('looks_logged_in')}"
                    )
                    if page_looks_logged_in(post_state):
                        return self.validate_login()

                    timezone_value = "Asia/Shanghai"
                    try:
                        timezone_value = self.page.evaluate(
                            "() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'"
                        )
                    except Exception:
                        pass
                    login_result = self.submit_login_with_linuxdo(
                        self.page,
                        csrf_token,
                        username,
                        password,
                        str(timezone_value or "Asia/Shanghai"),
                        hcaptcha_token=hcaptcha_token,
                    )
                    logger.info(
                        f"[linuxdo-session] status={login_result.get('status')} url={login_result.get('url')} "
                        f"body={str(login_result.get('body', ''))[:200]}"
                    )
                    wait_for_settle(10)
                    post_state = get_page_state(self.page)
                    logger.info(
                        f"[password-post-hcaptcha] url={post_state.get('url')} challenge={post_state.get('is_challenge')} "
                        f"turnstile={post_state.get('has_turnstile')} hcaptcha={post_state.get('has_hcaptcha')} "
                        f"logged_in={post_state.get('looks_logged_in')}"
                    )
            except Exception as exc:
                logger.warning(f"[hcaptcha] solve failed: {exc}")

        return self.validate_login()

    def login(self) -> Tuple[bool, Dict[str, object]]:
        if COOKIES and not SKIP_COOKIE_LOGIN:
            self.login_method = "cookie"
            logger.info("尝试使用 Cookie 登录")
            ok, state = self.try_cookie_login(COOKIES)
            if ok:
                cookie_str = self.sync_session_from_context()
                if cookie_str:
                    self.persist_cookie_if_possible(cookie_str)
                return ok, state
            logger.warning("Cookie 登录失败，继续回退账号密码登录")
        elif COOKIES and SKIP_COOKIE_LOGIN:
            logger.info("已按配置跳过 Cookie 登录，直接进入账号密码登录流程")

        if USERNAME and PASSWORD:
            self.login_method = "password"
            logger.info("尝试使用账号密码登录")
            ok, state = self.try_password_login(USERNAME, PASSWORD)
            if ok:
                cookie_str = self.sync_session_from_context()
                if cookie_str:
                    self.persist_cookie_if_possible(cookie_str)
                return ok, state
            return ok, state

        logger.warning("未配置可用的 LinuxDo 登录凭据")
        return False, {}

    def extract_topic_urls_from_current_page(self) -> List[str]:
        selectors = [
            "#list-area .title a",
            "#list-area a.title",
            "table.topic-list a.title",
            ".topic-list a.title",
            ".latest-topic-list-item a.title",
            "a.title[href*='/t/']",
            "a[href*='/t/']",
        ]
        script = f"""
() => {{
  const selectors = {json.dumps(selectors)};
  const seen = new Set();
  const urls = [];
  for (const selector of selectors) {{
    const nodes = Array.from(document.querySelectorAll(selector));
    for (const node of nodes) {{
      const href = (node.href || node.getAttribute('href') || '').trim();
      if (!href || !href.includes('/t/') || seen.has(href)) continue;
      seen.add(href);
      urls.push(href);
    }}
    if (urls.length) return urls;
  }}
  return urls;
}}
"""
        try:
            result = self.page.evaluate(script)
        except Exception:
            result = []
        return result if isinstance(result, list) else []

    def collect_topic_urls(self) -> List[str]:
        topic_urls = self.extract_topic_urls_from_current_page()
        if topic_urls:
            return topic_urls

        for target_url in [TOPIC_LIST_URL, HOME_URL]:
            logger.info(f"当前页面未发现主题列表，尝试打开主题页: {target_url}")
            try:
                self.page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                wait_for_settle(5)
            except Exception as exc:
                logger.warning(f"打开主题页失败: {target_url} ({exc})")
                continue

            topic_urls = self.extract_topic_urls_from_current_page()
            if topic_urls:
                return topic_urls

        logger.warning("已尝试主题列表页和首页，但仍未发现可浏览的主题链接")
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
    def click_one_topic(self, topic_url: str):
        new_page = self.context.new_page()
        liked = False
        try:
            new_page.goto(topic_url, wait_until="domcontentloaded", timeout=45000)
            wait_for_settle(4)
            result = self.browse_post(new_page)
            if random.random() < 0.3:
                liked = self.click_like_via_toggle(new_page, browse_result=result)
            result["liked"] = liked
            return result
        except Exception as exc:
            logger.warning(f"打开或浏览主题失败: {topic_url} ({exc})")
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
() => {
  const direct = Array.from(document.querySelectorAll('[data-post-number]'))
    .map((el) => parseInt((el.getAttribute('data-post-number') || '').trim(), 10))
    .filter(Number.isFinite);
  const fromArticleId = Array.from(document.querySelectorAll('article[id^="post_"]'))
    .map((el) => {
      const match = String(el.id || '').match(/^post_(\\d+)$/);
      return match ? parseInt(match[1], 10) : NaN;
    })
    .filter(Number.isFinite);
  return {
    url: location.href,
    post_numbers: Array.from(new Set(direct.concat(fromArticleId))).slice(0, 6)
  };
}
"""
        page_url = str(getattr(page, "url", "") or "")
        post_numbers: List[int] = []
        try:
            data = page.evaluate(script)
            if isinstance(data, dict):
                page_url = str(data.get("url") or page_url)
                for item in data.get("post_numbers") or []:
                    try:
                        number = int(item)
                    except (TypeError, ValueError):
                        continue
                    if number > 0 and number not in post_numbers:
                        post_numbers.append(number)
        except Exception as exc:
            logger.info(f"获取主题浏览打点上下文失败，继续使用 URL 回退: {exc}")

        if not post_numbers:
            post_numbers = [1]

        return {
            "url": page_url,
            "topic_id": self.extract_topic_id_from_url(page_url),
            "post_numbers": post_numbers,
        }

    def report_topic_timings(self, page, browse_result: Optional[dict] = None) -> bool:
        csrf_info = self.fetch_csrf_token_from_linuxdo(page)
        csrf_token = str(csrf_info.get("token") or get_csrf_token(page))
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

        resp = self.browser_fetch(
            page,
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
            body=urlencode(payload),
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
            f"body={str(resp.get('body', ''))[:200]}"
        )
        return False

    def get_like_target_state(self, page) -> dict:
        script = """
() => {
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

  let node = button;
  while (node && node !== document.documentElement) {
    const directPostId = clean(node.getAttribute && node.getAttribute('data-post-id'));
    if (directPostId) return directPostId;
    const datasetPostId = clean(node.dataset && node.dataset.postId);
    if (datasetPostId) return datasetPostId;
    const reactionPostId = extractPostIdFromReactionId(node.id);
    if (reactionPostId) return reactionPostId;
    node = node.parentElement;
  }
  return '';
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
  if (candidate.can_like) return candidate;
  if (!fallback || !fallback.post_id) fallback = candidate;
}

return fallback || {
  post_id: '',
  url: location.href,
  reaction_id: '',
  exists: false,
  liked: false,
  can_like: false,
  button_count: buttons.length
};
}
"""
        try:
            result = page.evaluate(script)
        except Exception as exc:
            logger.warning(f"读取点赞目标状态失败: {exc}")
            result = {}
        return result if isinstance(result, dict) else {}

    def page_has_challenge(self, page) -> bool:
        script = """
() => {
  const title = (document.title || '').toLowerCase();
  const text = (document.body ? (document.body.innerText || '') : '').slice(0, 3000).toLowerCase();
  const html = (document.documentElement ? (document.documentElement.outerHTML || '') : '').slice(0, 4000).toLowerCase();
  const summary = `${title}\\n${text}\\n${html}`;
  return (
    summary.includes('just a moment') ||
    summary.includes('cloudflare') ||
    summary.includes('cf-chl') ||
    summary.includes('challenge-platform') ||
    summary.includes('please wait while your request is being verified')
  );
}
"""
        try:
            return bool(page.evaluate(script))
        except Exception:
            return False

    def click_like_button_in_page(self, page, post_id: str) -> bool:
        script = f"""
() => {{
  const targetPostId = {json.dumps(post_id)};
  function clean(value) {{
    return String(value || '').trim();
  }}
  function extractPostIdFromReactionId(value) {{
    const text = clean(value);
    if (!text) return '';
    const match = text.match(/^discourse-reactions-(?:actions|counter|list-emoji)-(\\d+)(?:-.+)?$/);
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
      const directPostId = clean(node.getAttribute && node.getAttribute('data-post-id'));
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
}}
"""
        try:
            return bool(page.evaluate(script))
        except Exception as exc:
            logger.warning(f"页面内点击爱心按钮失败: post_id={post_id} error={exc}")
            return False

    def click_like_via_toggle(self, page, browse_result: Optional[dict] = None):
        target = self.get_like_target_state(page)
        post_id = str(target.get("post_id") or "").strip()
        page_url = str(target.get("url") or getattr(page, "url", "") or "").strip()
        if not post_id:
            logger.info(f"当前帖子页未找到可点赞的 post_id: url={page_url} state={target}")
            return False
        if target.get("liked"):
            logger.info(f"当前帖子已是点赞状态，跳过 toggle: post_id={post_id}")
            return False
        if not target.get("can_like"):
            logger.info(f"当前帖子页未识别到可直接点赞的状态: post_id={post_id} state={target}")
            return False

        wait_before_click = random.uniform(1.2, 2.8)
        logger.info(f"准备点击页面爱心按钮: post_id={post_id} wait={wait_before_click:.1f}s")
        time.sleep(wait_before_click)
        if not self.click_like_button_in_page(page, post_id):
            logger.warning(f"未能在页面中点击爱心按钮: post_id={post_id}")
            return False

        for attempt in range(1, 11):
            time.sleep(1)
            current = self.get_like_target_state(page)
            if str(current.get("post_id") or "").strip() == post_id and current.get("liked"):
                logger.info(f"页面爱心按钮点赞成功: post_id={post_id}")
                if browse_result is not None:
                    self.report_topic_timings(page, browse_result)
                return True
            if self.page_has_challenge(page):
                logger.warning(
                    "点击爱心按钮后页面进入 Cloudflare/挑战态: "
                    f"post_id={post_id} url={page_url}"
                )
                return False
            if attempt in {3, 6, 10}:
                logger.info(
                    "等待页面爱心按钮状态更新中: "
                    f"post_id={post_id} attempt={attempt}/10 state={current}"
                )

        logger.warning(f"点击爱心按钮后页面状态未更新为已赞: post_id={post_id} url={page_url}")
        return False

    def browse_post(self, page):
        prev_url = None
        scrolls = 0
        exit_reason = "max_scrolls"
        start_time = time.monotonic()
        for _ in range(10):
            scroll_distance = random.randint(550, 650)
            page.evaluate(f"() => window.scrollBy(0, {scroll_distance})")
            scrolls += 1

            if random.random() < 0.03:
                exit_reason = "random_exit"
                break

            at_bottom = bool(
                page.evaluate(
                    "() => window.scrollY + window.innerHeight >= document.body.scrollHeight"
                )
            )
            current_url = getattr(page, "url", "")
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                exit_reason = "bottom"
                break

            time.sleep(random.uniform(2, 4))

        return {
            "scrolls": scrolls,
            "exit_reason": exit_reason,
            "final_url": getattr(page, "url", ""),
            "duration_ms": max(1000, int((time.monotonic() - start_time) * 1000)),
        }

    def click_topic(self) -> bool:
        topic_urls = self.collect_topic_urls()
        if not topic_urls:
            logger.error("未找到主题帖")
            return False

        sample_count = min(10, len(topic_urls))
        self.browse_stats = {
            "topics_total": len(topic_urls),
            "topics_planned": sample_count,
            "topics_completed": 0,
            "likes": 0,
        }
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

    def print_connect_info(self):
        logger.info("获取连接信息")
        self.sync_session_from_context()
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
                info.append(
                    [
                        cells[0].text.strip(),
                        cells[1].text.strip() or "0",
                        cells[2].text.strip() or "0",
                    ]
                )

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

    def send_notifications(self, browse_enabled: bool):
        status_lines = [
            "✅ LINUX DO 任务完成",
            f"账号: {self.login_name}",
            f"登录确认: 已登录 ({self.get_login_verify_label()} 校验)",
            f"登录方式: {self.get_login_method_label()}",
            "浏览任务: 已完成" if browse_enabled else "浏览任务: 已关闭",
        ]
        if self.connect_summary:
            status_lines.append(f"Connect 信息: {self.connect_summary}")
        if browse_enabled:
            status_lines.append(
                f"浏览摘要: {self.browse_stats['topics_completed']}/{self.browse_stats['topics_planned']} 个主题, "
                f"点赞 {self.browse_stats['likes']} 次"
            )
        self.notifier.send_all("LINUX DO", "\n".join(status_lines))

    def run(self, browse_enabled: bool = BROWSE_ENABLED) -> bool:
        try:
            ok, _ = self.login()
            if not ok:
                self.send_failure_notification("登录验证失败")
                return False

            logger.success(
                f"已确认登录: {self.login_name} "
                f"({self.get_login_verify_label()} 校验, {self.get_login_method_label()} 登录)"
            )

            if browse_enabled and not self.click_topic():
                self.send_failure_notification("浏览任务失败")
                return False

            try:
                self.print_connect_info()
            except Exception as exc:
                self.connect_summary = f"获取失败: {exc}"
                logger.warning(f"获取 Connect 信息失败: {exc}")
            self.send_notifications(browse_enabled)
            return True
        finally:
            self.close()


def build_result(
    ok: bool,
    method: str,
    state: Dict[str, object],
    screenshot_path: str,
    loaded_envs: List[str],
) -> Dict[str, object]:
    return {
        "ok": ok,
        "method": method,
        "url": state.get("url"),
        "title": state.get("title"),
        "challenge": bool(state.get("is_challenge")),
        "turnstile": bool(state.get("has_turnstile")),
        "hcaptcha": bool(state.get("has_hcaptcha")),
        "logged_in": bool(state.get("looks_logged_in")),
        "text_snippet": state.get("text_snippet", ""),
        "screenshot": screenshot_path,
        "loaded_env_files": loaded_envs,
    }


def run_login_smoke_test(
    headless: bool = True,
    user_data_dir: str = "",
    screenshot_path: str = "linuxdo-cloakbrowser-login-test.png",
) -> int:
    loaded_envs = preload_env_files()
    browser = LinuxDoCloakBrowser(headless=headless, user_data_dir=user_data_dir)
    result = None
    try:
        if COOKIES and not SKIP_COOKIE_LOGIN:
            browser.login_method = "cookie"
            logger.info("[flow] trying cookie login")
            ok, state = browser.try_cookie_login(COOKIES)
            result = build_result(ok, "cookie", state, screenshot_path, loaded_envs)
            if ok:
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                return 0
        elif COOKIES and SKIP_COOKIE_LOGIN:
            logger.info("[flow] skip cookie login by configuration")

        if USERNAME and PASSWORD:
            browser.login_method = "password"
            logger.info("[flow] trying password login")
            ok, state = browser.try_password_login(USERNAME, PASSWORD)
            result = build_result(ok, "password", state, screenshot_path, loaded_envs)
            if ok:
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                return 0

        if result is None:
            result = build_result(False, "none", {}, screenshot_path, loaded_envs)
        try:
            browser.page.screenshot(path=screenshot_path, full_page=True)
        except Exception as exc:
            logger.warning(f"[screenshot] failed: {exc}")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 1
    finally:
        browser.close()


def has_linuxdo_credentials() -> bool:
    return bool(COOKIES or (USERNAME and PASSWORD))


def run_linuxdo_task(headless: bool = False, user_data_dir: str = "") -> bool:
    browser = LinuxDoCloakBrowser(
        headless=headless,
        user_data_dir=user_data_dir,
        notifier=NotificationManager(),
    )
    return browser.run(browse_enabled=BROWSE_ENABLED)


def main() -> int:
    parser = argparse.ArgumentParser(description="LinuxDo CloakBrowser task runner")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--show-browser", action="store_true", help="Run in headed mode")
    parser.add_argument("--user-data-dir", default="", help="Persistent profile path")
    parser.add_argument("--screenshot", default="linuxdo-cloakbrowser-login-test.png")
    parser.add_argument("--login-only", action="store_true", help="Only run login smoke test")
    args = parser.parse_args()

    headless = True
    if args.show_browser:
        headless = False
    elif args.headless:
        headless = True

    if args.login_only:
        return run_login_smoke_test(
            headless=headless,
            user_data_dir=args.user_data_dir,
            screenshot_path=args.screenshot,
        )

    if not has_linuxdo_credentials():
        print("Need LINUXDO_COOKIES or LINUXDO_USERNAME/LINUXDO_PASSWORD", file=sys.stderr)
        return 2

    return 0 if run_linuxdo_task(headless=headless, user_data_dir=args.user_data_dir) else 1


if __name__ == "__main__":
    raise SystemExit(main())
