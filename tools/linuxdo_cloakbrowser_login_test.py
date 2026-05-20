#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple


HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
ACCOUNT_PREFERENCES_URL = "https://linux.do/my/preferences/account"


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

                if override or not os.environ.get(key, "").strip():
                    os.environ[key] = value
                    loaded_any = True
    except OSError as exc:
        print(f"[env] failed to load {path}: {exc}", file=sys.stderr)
        return False

    return loaded_any


def preload_env_files() -> List[str]:
    repo_dir = Path(__file__).resolve().parents[1]
    candidates = []

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

    loaded = []
    seen = set()
    for candidate in candidates:
        normalized = os.path.abspath(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if load_env_file(normalized):
            loaded.append(normalized)
    return loaded


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


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
        return sorted({cookie.get("name", "") for cookie in context.cookies() if cookie.get("name")})
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

  const accountHints = [
    '/my/preferences/account',
    '/u/',
  ];
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
    print(
        f"[{label}] url={state.get('url')} title={state.get('title')} "
        f"challenge={state.get('is_challenge')} turnstile={state.get('has_turnstile')} "
        f"hcaptcha={state.get('has_hcaptcha')} logged_in={state.get('looks_logged_in')}",
        flush=True,
    )
    return state


def validate_login(page, context) -> Tuple[bool, Dict[str, object]]:
    state = navigate_and_capture(page, ACCOUNT_PREFERENCES_URL, "validate")
    if page_looks_logged_in(state):
        return True, state

    cookie_names = get_cookie_names(context)
    print(f"[validate] cookie_names={cookie_names}", flush=True)
    return False, state


def try_cookie_login(page, context, cookie_str: str) -> Tuple[bool, Dict[str, object]]:
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        print("[cookie] no parseable cookies", flush=True)
        return False, {}

    navigate_and_capture(page, HOME_URL, "cookie-home-before")
    context.add_cookies(cookies)
    state = navigate_and_capture(page, HOME_URL, "cookie-home-after")
    ok, validation_state = validate_login(page, context)
    return ok, validation_state or state


def find_first(page, selectors: List[str]):
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    return None


def locator_exists(locator) -> bool:
    return locator is not None


def try_password_login(page, context, username: str, password: str) -> Tuple[bool, Dict[str, object]]:
    state = navigate_and_capture(page, LOGIN_URL, "password-login")

    login_input = find_first(
        page,
        [
            'input[name="login"]',
            'input[type="email"]',
            '#login-account-name',
            'input[autocomplete="username"]',
        ],
    )
    password_input = find_first(
        page,
        [
            '#login-account-password',
            'form#login-form input[type="password"]',
            'input[name="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ],
    )
    submit_button = find_first(
        page,
        [
            '#login-button',
            'button.login-page-cta__login',
            'form#login-form button',
            'button[type="submit"]',
            'form button',
        ],
    )

    if not locator_exists(login_input) or not locator_exists(password_input) or not locator_exists(submit_button):
        print("[password] login form not ready", flush=True)
        return False, state

    login_input.fill(username)
    password_input.fill(password)
    wait_for_settle(1)
    submit_button.click()
    wait_for_settle(8)

    post_state = get_page_state(page)
    print(
        f"[password-post] url={post_state.get('url')} challenge={post_state.get('is_challenge')} "
        f"turnstile={post_state.get('has_turnstile')} hcaptcha={post_state.get('has_hcaptcha')} "
        f"logged_in={post_state.get('looks_logged_in')}",
        flush=True,
    )

    ok, validation_state = validate_login(page, context)
    return ok, validation_state or post_state


def build_result(ok: bool, method: str, state: Dict[str, object], screenshot_path: str, loaded_envs: List[str]) -> Dict[str, object]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal LinuxDo login smoke test via CloakBrowser")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--show-browser", action="store_true", help="Run in headed mode")
    parser.add_argument("--user-data-dir", default="", help="Persistent profile path")
    parser.add_argument("--screenshot", default="linuxdo-cloakbrowser-login-test.png", help="Failure screenshot path")
    args = parser.parse_args()

    loaded_envs = preload_env_files()
    cookie_str = env_str("LINUXDO_COOKIES")
    username = env_str("LINUXDO_USERNAME") or env_str("USERNAME")
    password = env_str("LINUXDO_PASSWORD") or env_str("PASSWORD")

    if not cookie_str and not (username and password):
        print("Need LINUXDO_COOKIES or LINUXDO_USERNAME/LINUXDO_PASSWORD", file=sys.stderr)
        return 2

    try:
        from cloakbrowser import launch_context, launch_persistent_context
    except ModuleNotFoundError:
        print("cloakbrowser is not installed. Run: pip install cloakbrowser", file=sys.stderr)
        return 3

    headless = True
    if args.show_browser:
        headless = False
    elif args.headless:
        headless = True

    print(f"[setup] headless={headless} loaded_env_files={loaded_envs}", flush=True)

    launch_kwargs = {
        "headless": headless,
        "humanize": True,
        "human_preset": "default",
    }
    if args.user_data_dir:
        context = launch_persistent_context(args.user_data_dir, **launch_kwargs)
    else:
        context = launch_context(**launch_kwargs)

    page = context.new_page()
    result = None

    try:
        if cookie_str:
            print("[flow] trying cookie login", flush=True)
            ok, state = try_cookie_login(page, context, cookie_str)
            result = build_result(ok, "cookie", state, args.screenshot, loaded_envs)
            if ok:
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                return 0

        if username and password:
            print("[flow] trying password login", flush=True)
            ok, state = try_password_login(page, context, username, password)
            result = build_result(ok, "password", state, args.screenshot, loaded_envs)
            if ok:
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                return 0

        if result is None:
            result = build_result(False, "none", {}, args.screenshot, loaded_envs)
        try:
            page.screenshot(path=args.screenshot, full_page=True)
        except Exception as exc:
            print(f"[screenshot] failed: {exc}", flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 1
    finally:
        context.close()


if __name__ == "__main__":
    raise SystemExit(main())
