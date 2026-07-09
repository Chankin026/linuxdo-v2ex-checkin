#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
ACCOUNT_PREFERENCES_URL = "https://linux.do/my/preferences/account"
YESCAPTCHA_API_BASE_URL = "https://api.yescaptcha.com"


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


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_solver_settings() -> Dict[str, object]:
    return {
        "api_base_url": (
            env_str("LINUXDO_YESCAPTCHA_API_BASE_URL")
            or env_str("YESCAPTCHA_API_BASE_URL")
            or YESCAPTCHA_API_BASE_URL
        ),
        "advanced": env_str("LINUXDO_YESCAPTCHA_ADVANCED", "").lower()
        in {"1", "true", "yes", "on"},
        "turnstile": {
            "max_retries": env_int(
                "LINUXDO_YESCAPTCHA_MAX_RETRIES",
                env_int("YESCAPTCHA_MAX_RETRIES", 20),
            ),
            "retry_interval": env_int(
                "LINUXDO_YESCAPTCHA_RETRY_INTERVAL",
                env_int("YESCAPTCHA_RETRY_INTERVAL", 3),
            ),
            "timeout": env_int(
                "LINUXDO_YESCAPTCHA_TIMEOUT",
                env_int("YESCAPTCHA_TIMEOUT", 60),
            ),
        },
        "hcaptcha": {
            "max_retries": env_int(
                "LINUXDO_YESCAPTCHA_HCAPTCHA_MAX_RETRIES",
                env_int("YESCAPTCHA_HCAPTCHA_MAX_RETRIES", 45),
            ),
            "retry_interval": env_int(
                "LINUXDO_YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL",
                env_int("YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL", 4),
            ),
            "timeout": env_int(
                "LINUXDO_YESCAPTCHA_HCAPTCHA_TIMEOUT",
                env_int("YESCAPTCHA_HCAPTCHA_TIMEOUT", 600),
            ),
        },
    }


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


class YesCaptchaSolverError(Exception):
    pass


class YesCaptchaSolver:
    def __init__(
        self,
        client_key: str,
        api_base_url: str,
        max_retries: int,
        retry_interval: int,
        timeout: int,
        advanced: bool = False,
    ) -> None:
        self.client_key = client_key
        self.api_base_url = api_base_url.rstrip("/")
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
        return self._wait_for_result(task_id)

    def _create_task(self, url: str, sitekey: str, user_agent: str, captcha_type: str) -> str:
        import httpx

        if captcha_type == "hcaptcha":
            task_type = "HCaptchaTaskProxyless"
        else:
            task_type = "TurnstileTaskProxylessM1" if self.advanced else "TurnstileTaskProxyless"

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
            response = httpx.post(
                f"{self.api_base_url}/createTask",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            raise YesCaptchaSolverError(f"createTask failed: {exc}") from exc

        if result.get("errorId") == 0 and result.get("taskId"):
            return str(result["taskId"])
        raise YesCaptchaSolverError(result.get("errorDescription") or "createTask returned error")

    def _wait_for_result(self, task_id: str) -> str:
        import httpx

        payload = {"clientKey": self.client_key, "taskId": task_id}

        for _ in range(self.max_retries):
            try:
                response = httpx.post(
                    f"{self.api_base_url}/getTaskResult",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                result = response.json()
            except Exception as exc:
                raise YesCaptchaSolverError(f"getTaskResult failed: {exc}") from exc

            if result.get("errorId", 0) > 0:
                raise YesCaptchaSolverError(
                    result.get("errorDescription") or "getTaskResult returned error"
                )

            if result.get("status") == "ready":
                solution = result.get("solution", {})
                token = solution.get("token") or solution.get("gRecaptchaResponse")
                if token:
                    return str(token)
                raise YesCaptchaSolverError("ready response did not include token")

            time.sleep(self.retry_interval)

        raise YesCaptchaSolverError("captcha solving timed out")


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
  const owner = window.Discourse && window.Discourse.__container__;
  const service = owner && owner.lookup && owner.lookup('service:captcha-service');
  if (service) {{
    service.token = token;
    service.invalid = !token;
    service.submitted = !!token;
  }}
  window.__hcaptchaToken = token;
  window.__hcaptchaResponse = token;
  window.__linuxdoLastHCaptchaToken = token;
  return elements.length > 0 || !!service;
}}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def install_login_request_capture(page) -> bool:
    script = """
() => {
  if (window.__linuxdoLoginCaptureInstalled) {
    return true;
  }
  window.__linuxdoLoginRequests = window.__linuxdoLoginRequests || [];
  const shouldCapture = (url) => {
    const value = String(url || '');
    return (
      value.includes('/session') ||
      value.includes('/captcha') ||
      value.includes('/hcaptcha') ||
      value.includes('/login')
    );
  };
  const summarizeBody = (body) => {
    if (!body) return { length: 0, keys: [] };
    let text = '';
    if (typeof body === 'string') {
      text = body;
    } else if (body instanceof URLSearchParams) {
      text = body.toString();
    } else {
      return {
        length: 0,
        keys: [],
        kind: Object.prototype.toString.call(body)
      };
    }
    const keys = [];
    try {
      const params = new URLSearchParams(text);
      for (const [key] of params.entries()) {
        keys.push(key);
      }
    } catch (e) {}
    return {
      length: text.length,
      keys: Array.from(new Set(keys)).sort()
    };
  };
  const summarizeResponse = (text) => {
    const value = String(text || '');
    if (!value) return '';
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object') {
        const summary = { jsonKeys: Object.keys(parsed).sort().slice(0, 20) };
        if (parsed.error_type) summary.error_type = String(parsed.error_type);
        if (parsed.result) summary.result = String(parsed.result);
        if (Array.isArray(parsed.errors)) {
          summary.errors = parsed.errors.slice(0, 3).map((item) => String(item));
        }
        return JSON.stringify(summary).slice(0, 300);
      }
    } catch (e) {}
    return value.replace(/\\s+/g, ' ').slice(0, 300);
  };
  const pushTrace = (trace) => {
    window.__linuxdoLoginRequests.push(trace);
    if (window.__linuxdoLoginRequests.length > 30) {
      window.__linuxdoLoginRequests.shift();
    }
  };

  if (typeof window.fetch === 'function' && !window.fetch.__linuxdoLoginCaptureWrapped) {
    const originalFetch = window.fetch.bind(window);
    const wrappedFetch = async (...args) => {
      const input = args[0];
      const init = args[1] || {};
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const method = (init.method || (input && input.method) || 'GET').toUpperCase();
      const trace = shouldCapture(url)
        ? {
            type: 'fetch',
            method,
            url: String(url),
            request: summarizeBody(init.body)
          }
        : null;
      try {
        const response = await originalFetch(...args);
        if (trace) {
          trace.status = response.status;
          trace.ok = response.ok;
          trace.responseUrl = response.url;
          try {
            trace.body = summarizeResponse(await response.clone().text());
          } catch (e) {
            trace.body = '';
          }
          pushTrace(trace);
        }
        return response;
      } catch (e) {
        if (trace) {
          trace.error = String(e);
          pushTrace(trace);
        }
        throw e;
      }
    };
    wrappedFetch.__linuxdoLoginCaptureWrapped = true;
    window.fetch = wrappedFetch;
  }

  if (window.XMLHttpRequest && !window.XMLHttpRequest.prototype.__linuxdoLoginCaptureWrapped) {
    const proto = window.XMLHttpRequest.prototype;
    const originalOpen = proto.open;
    const originalSend = proto.send;
    proto.open = function(method, url, ...rest) {
      this.__linuxdoLoginTrace = shouldCapture(url)
        ? { type: 'xhr', method: String(method || 'GET').toUpperCase(), url: String(url) }
        : null;
      return originalOpen.call(this, method, url, ...rest);
    };
    proto.send = function(body) {
      const trace = this.__linuxdoLoginTrace;
      if (trace) {
        trace.request = summarizeBody(body);
        this.addEventListener('loadend', () => {
          trace.status = this.status;
          trace.ok = this.status >= 200 && this.status < 300;
          trace.responseUrl = this.responseURL || trace.url;
          trace.body = summarizeResponse(this.responseText || '');
          pushTrace(trace);
        });
      }
      return originalSend.call(this, body);
    };
    proto.__linuxdoLoginCaptureWrapped = true;
  }

  window.__linuxdoLoginCaptureInstalled = true;
  return true;
}
"""
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def get_login_request_traces(page) -> List[Dict[str, object]]:
    script = """
() => Array.isArray(window.__linuxdoLoginRequests)
  ? window.__linuxdoLoginRequests.slice(-20)
  : []
"""
    try:
        result = page.evaluate(script)
    except Exception:
        return []
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


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


def fetch_csrf_token_from_linuxdo(page) -> Dict[str, object]:
    script = """
async () => {
  const response = await fetch('/session/csrf', {
    method: 'GET',
    headers: {
      'Accept': 'application/json, text/javascript, */*; q=0.01',
      'X-Requested-With': 'XMLHttpRequest'
    },
    credentials: 'same-origin'
  });
  return {
    ok: response.ok,
    status: response.status,
    body: await response.text()
  };
}
"""
    fallback = {"ok": False, "status": 0, "body": "", "token": ""}
    try:
        result = page.evaluate(script)
    except Exception as exc:
        fallback["body"] = str(exc)
        return fallback
    if not isinstance(result, dict):
        return fallback
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


def register_hcaptcha_token_with_linuxdo(page, csrf_token: str, token: str) -> Dict[str, object]:
    if not csrf_token or not token:
        return {"success": False, "status": 0, "body": "", "json": {}}
    script = f"""
async () => {{
  const response = await fetch('/captcha/hcaptcha/create.json', {{
    method: 'POST',
    headers: {{
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'X-CSRF-Token': {json.dumps(csrf_token)},
      'X-Requested-With': 'XMLHttpRequest'
    }},
    body: new URLSearchParams({{ token: {json.dumps(token)} }}).toString(),
    credentials: 'same-origin'
  }});
  return {{
    ok: response.ok,
    status: response.status,
    body: await response.text()
  }};
}}
"""
    try:
        result = page.evaluate(script)
    except Exception as exc:
        return {"success": False, "status": 0, "body": str(exc), "json": {}}
    if not isinstance(result, dict):
        return {"success": False, "status": 0, "body": "", "json": {}}
    body = str(result.get("body", ""))
    parsed = {}
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed = {}
    result["json"] = parsed if isinstance(parsed, dict) else {}
    result["success"] = bool(result.get("ok")) and result["json"].get("success") == "OK"
    return result


def submit_login_with_linuxdo(
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
    script = f"""
async () => {{
  const response = await fetch('/session', {{
    method: 'POST',
    headers: {{
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'X-CSRF-Token': {json.dumps(csrf_token)},
      'X-Requested-With': 'XMLHttpRequest',
      'Discourse-Present': 'true',
      'Accept': '*/*'
    }},
    body: new URLSearchParams({json.dumps(form_data)}).toString(),
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
        return {"ok": False, "status": 0, "url": "", "body": str(exc)}
    return result if isinstance(result, dict) else {"ok": False, "status": 0, "url": "", "body": ""}


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


def solve_turnstile_if_needed(page, user_agent: str, solver: Optional[YesCaptchaSolver]) -> str:
    sitekey = detect_turnstile_sitekey(page)
    if not sitekey:
        print("[turnstile] no sitekey detected", flush=True)
        return ""
    print(f"[turnstile] detected sitekey={sitekey[:12]}...", flush=True)
    if solver is None:
        print("[turnstile] solver not configured", flush=True)
        return ""
    token = solver.solve(LOGIN_URL, sitekey, user_agent=user_agent, captcha_type="turnstile")
    inject_turnstile_token(page, token)
    print("[turnstile] token injected", flush=True)
    return token


def solve_hcaptcha_if_needed(page, user_agent: str, solver: Optional[YesCaptchaSolver]) -> str:
    sitekey = detect_hcaptcha_sitekey(page)
    if not sitekey:
        print("[hcaptcha] no sitekey detected", flush=True)
        return ""
    print(f"[hcaptcha] detected sitekey={sitekey[:12]}...", flush=True)
    if solver is None:
        print("[hcaptcha] solver not configured", flush=True)
        return ""
    token = solver.solve(LOGIN_URL, sitekey, user_agent=user_agent, captcha_type="hcaptcha")
    inject_hcaptcha_token(page, token)
    print("[hcaptcha] token injected", flush=True)
    return token


def try_password_login(
    page,
    context,
    username: str,
    password: str,
    turnstile_solver: Optional[YesCaptchaSolver],
    hcaptcha_solver: Optional[YesCaptchaSolver],
) -> Tuple[bool, Dict[str, object]]:
    state = navigate_and_capture(page, LOGIN_URL, "password-login")
    user_agent = page.evaluate("() => navigator.userAgent")

    try:
        solve_turnstile_if_needed(page, user_agent, turnstile_solver)
        wait_for_settle(2)
    except Exception as exc:
        print(f"[turnstile] solve failed: {exc}", flush=True)

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

    install_hcaptcha_callback_capture(page)
    install_login_request_capture(page)
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

    if post_state.get("has_hcaptcha"):
        try:
            checkbox_clicked = try_click_hcaptcha_checkbox_if_needed(page)
            print(f"[hcaptcha] checkbox_clicked={checkbox_clicked}", flush=True)
            wait_for_settle(2)
            post_state = get_page_state(page)
            if not post_state.get("has_hcaptcha"):
                ok, validation_state = validate_login(page, context)
                return ok, validation_state or post_state
            hcaptcha_token = solve_hcaptcha_if_needed(page, user_agent, hcaptcha_solver)
            wait_for_settle(2)
            csrf_info = fetch_csrf_token_from_linuxdo(page)
            csrf_token = str(csrf_info.get("token") or get_csrf_token(page))
            print(
                f"[linuxdo-csrf] status={csrf_info.get('status')} token_present={bool(csrf_token)} "
                f"body={str(csrf_info.get('body', ''))[:200]}",
                flush=True,
            )
            registered = register_hcaptcha_token_with_linuxdo(page, csrf_token, hcaptcha_token)
            print(
                f"[hcaptcha] registered_with_linuxdo={registered.get('success')} "
                f"status={registered.get('status')} body={str(registered.get('body', ''))[:200]}",
                flush=True,
            )
            if registered.get("success") or hcaptcha_token:
                frontend_result = submit_login_with_frontend_controller(page, username, password)
                print(
                    f"[linuxdo-frontend-login] ok={frontend_result.get('ok')} "
                    f"logged_in={frontend_result.get('loggedIn')} "
                    f"reason={frontend_result.get('reason')} "
                    f"flash={str(frontend_result.get('flash', ''))[:120]}",
                    flush=True,
                )
                print(
                    "[linuxdo-login-requests] "
                    + json.dumps(get_login_request_traces(page), ensure_ascii=False)[:1200],
                    flush=True,
                )
                wait_for_settle(10)
                post_state = get_page_state(page)
                print(
                    f"[password-post-frontend] url={post_state.get('url')} "
                    f"challenge={post_state.get('is_challenge')} "
                    f"turnstile={post_state.get('has_turnstile')} "
                    f"hcaptcha={post_state.get('has_hcaptcha')} "
                    f"logged_in={post_state.get('looks_logged_in')}",
                    flush=True,
                )
                if page_looks_logged_in(post_state):
                    ok, validation_state = validate_login(page, context)
                    return ok, validation_state or post_state

                timezone_value = "Asia/Shanghai"
                try:
                    timezone_value = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'")
                except Exception:
                    pass
                login_result = submit_login_with_linuxdo(
                    page,
                    csrf_token,
                    username,
                    password,
                    str(timezone_value or "Asia/Shanghai"),
                    hcaptcha_token=hcaptcha_token,
                )
                print(
                    f"[linuxdo-session] status={login_result.get('status')} url={login_result.get('url')} "
                    f"body={str(login_result.get('body', ''))[:200]}",
                    flush=True,
                )
                print(
                    "[linuxdo-login-requests] "
                    + json.dumps(get_login_request_traces(page), ensure_ascii=False)[:1200],
                    flush=True,
                )
                wait_for_settle(10)
                post_state = get_page_state(page)
                print(
                    f"[password-post-hcaptcha] url={post_state.get('url')} challenge={post_state.get('is_challenge')} "
                    f"turnstile={post_state.get('has_turnstile')} hcaptcha={post_state.get('has_hcaptcha')} "
                    f"logged_in={post_state.get('looks_logged_in')}",
                    flush=True,
                )
        except Exception as exc:
            print(f"[hcaptcha] solve failed: {exc}", flush=True)

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
    solver_key = (
        env_str("CLIENTT_KEY")
        or env_str("LINUXDO_YESCAPTCHA_CLIENT_KEY")
        or env_str("YESCAPTCHA_CLIENT_KEY")
    )
    solver_settings = load_solver_settings()

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
    turnstile_solver = None
    hcaptcha_solver = None
    if solver_key:
        turnstile_solver = YesCaptchaSolver(
            client_key=solver_key,
            api_base_url=str(solver_settings["api_base_url"]),
            max_retries=int(solver_settings["turnstile"]["max_retries"]),
            retry_interval=int(solver_settings["turnstile"]["retry_interval"]),
            timeout=int(solver_settings["turnstile"]["timeout"]),
            advanced=bool(solver_settings["advanced"]),
        )
        hcaptcha_solver = YesCaptchaSolver(
            client_key=solver_key,
            api_base_url=str(solver_settings["api_base_url"]),
            max_retries=int(solver_settings["hcaptcha"]["max_retries"]),
            retry_interval=int(solver_settings["hcaptcha"]["retry_interval"]),
            timeout=int(solver_settings["hcaptcha"]["timeout"]),
            advanced=bool(solver_settings["advanced"]),
        )
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
            ok, state = try_password_login(
                page,
                context,
                username,
                password,
                turnstile_solver,
                hcaptcha_solver,
            )
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
