import importlib.util
import json
import os
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_PATH = REPO_ROOT / "main.py"
NODESEEK_PATH = REPO_ROOT / "nodeseek.py"


def load_module(module_name: str, path: pathlib.Path, stub_modules=None):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    module_names = set((stub_modules or {}).keys())
    if path == NODESEEK_PATH:
        module_names.add("nodeseek_email")
    if path == MAIN_PATH:
        module_names.add("main")
    if path == NODESEEK_PATH:
        module_names.add("nodeseek")

    saved_modules = {
        name: sys.modules[name]
        for name in module_names
        if name in sys.modules
    }
    for name in module_names:
        sys.modules.pop(name, None)

    with mock.patch.dict(sys.modules, stub_modules or {}):
        spec.loader.exec_module(module)

    for name in module_names:
        sys.modules.pop(name, None)
    sys.modules.update(saved_modules)
    return module


def build_main_stub_modules():
    logger = mock.Mock()

    loguru = types.ModuleType("loguru")
    loguru.logger = logger

    class DummyMission:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def run(self):
            return True

    nodeseek = types.ModuleType("nodeseek")
    nodeseek.NodeSeekDailyMission = DummyMission

    notify = types.ModuleType("notify")
    notify.NotificationManager = object

    v2ex = types.ModuleType("v2ex")
    v2ex.V2EXDailyMission = DummyMission

    xiaoheihe = types.ModuleType("xiaoheihe")
    xiaoheihe.XiaoHeiHeDailyMission = DummyMission
    xiaoheihe.resolve_request_mode_label = lambda mode: mode

    return {
        "loguru": loguru,
        "nodeseek": nodeseek,
        "notify": notify,
        "v2ex": v2ex,
        "xiaoheihe": xiaoheihe,
    }


def build_nodeseek_stub_modules():
    logger = mock.Mock()

    loguru = types.ModuleType("loguru")
    loguru.logger = logger

    class FakeCookieJar:
        def __iter__(self):
            return iter([])

    class FakeCookies:
        jar = FakeCookieJar()

        def set(self, *args, **kwargs):
            return None

    class FakeSession:
        def __init__(self):
            self.headers = {}
            self.cookies = FakeCookies()

    curl_cffi = types.ModuleType("curl_cffi")
    curl_cffi.requests = types.SimpleNamespace(Session=FakeSession)

    captcha_solver = types.ModuleType("captcha_solver")

    class YesCaptchaSolverError(Exception):
        pass

    class FakeYesCaptchaSolver:
        def __init__(self, *args, **kwargs):
            pass

        def solve(self, *args, **kwargs):
            return "turnstile-token"

    captcha_solver.YesCaptchaSolver = FakeYesCaptchaSolver
    captcha_solver.YesCaptchaSolverError = YesCaptchaSolverError

    nodeseek_email = types.ModuleType("nodeseek_email")

    class FakeImapEmailCodeFetcher:
        created_kwargs = []

        def __init__(self, *args, **kwargs):
            self.created_kwargs.append(kwargs)

        def wait_for_code(self, **kwargs):
            return "654321"

    nodeseek_email.ImapEmailCodeFetcher = FakeImapEmailCodeFetcher
    nodeseek_email.infer_imap_host = lambda email_address: (
        "imap.qq.com" if str(email_address).endswith("@qq.com") else ""
    )

    notify = types.ModuleType("notify")
    notify.NotificationManager = object

    return {
        "loguru": loguru,
        "curl_cffi": curl_cffi,
        "captcha_solver": captcha_solver,
        "nodeseek_email": nodeseek_email,
        "notify": notify,
    }


class NodeSeekEmailCodeParsingTests(unittest.TestCase):
    def test_extract_verification_code_prefers_code_near_keyword(self):
        from nodeseek_email import extract_verification_code

        body = "NodeSeek 邮箱动态验证登录\n验证码：654321\n本邮件发送于 2026-05-25"

        self.assertEqual(extract_verification_code(body), "654321")

    def test_extract_verification_code_supports_long_alpha_numeric_token(self):
        from nodeseek_email import extract_verification_code

        body = (
            "nodeseek邮箱动态验证登录，你的验证码是"
            "a1b2c3d4e5f60718293a4b5c，不要告诉他人"
        )

        self.assertEqual(
            extract_verification_code(body),
            "a1b2c3d4e5f60718293a4b5c",
        )

    def test_find_latest_verification_code_ignores_stale_mail(self):
        from nodeseek_email import (
            EmailVerificationMessage,
            find_latest_verification_code,
        )

        started_at = datetime(2026, 5, 25, 21, 0, tzinfo=timezone.utc)
        messages = [
            EmailVerificationMessage(
                received_at=started_at - timedelta(minutes=5),
                sender="noreply@nodeseek.com",
                subject="NodeSeek 验证码",
                body="验证码：111111",
            ),
            EmailVerificationMessage(
                received_at=started_at + timedelta(seconds=20),
                sender="noreply@nodeseek.com",
                subject="NodeSeek 验证码",
                body="验证码：222222",
            ),
        ]

        self.assertEqual(
            find_latest_verification_code(messages, not_before=started_at),
            "222222",
        )

    def test_parse_raw_email_decodes_subject_date_and_plain_body(self):
        from nodeseek_email import parse_raw_email

        raw = b"".join(
            [
            b"From: NodeSeek <noreply@nodeseek.com>\r\n"
            b"Subject: =?utf-8?b?Tm9kZVNlZWsg6aqM6K+B56CB?=\r\n",
            b"Date: Mon, 25 May 2026 21:05:00 +0800\r\n",
            b"Content-Type: text/plain; charset=utf-8\r\n",
            b"\r\n",
            "验证码：987654\r\n".encode("utf-8"),
            ]
        )

        message = parse_raw_email(raw)

        self.assertIn("NodeSeek", message.subject)
        self.assertIn("987654", message.body)
        self.assertEqual(message.received_at.year, 2026)

    def test_infer_imap_host_for_common_email_domains(self):
        from nodeseek_email import infer_imap_host

        self.assertEqual(infer_imap_host("user@qq.com"), "imap.qq.com")
        self.assertEqual(infer_imap_host("user@gmail.com"), "imap.gmail.com")
        self.assertEqual(infer_imap_host("user@163.com"), "imap.163.com")


class MainNodeSeekEmailConfigTests(unittest.TestCase):
    def test_single_account_loads_email_imap_config(self):
        env = {
            "NODESEEK_USERNAME": "neal",
            "NODESEEK_PASSWORD": "secret",
            "NODESEEK_EMAIL": "neal@example.com",
            "NODESEEK_EMAIL_IMAP_HOST": "imap.example.com",
            "NODESEEK_EMAIL_IMAP_PORT": "993",
            "NODESEEK_EMAIL_IMAP_USERNAME": "mail-user",
            "NODESEEK_EMAIL_IMAP_PASSWORD": "mail-pass",
            "NODESEEK_EMAIL_IMAP_MAILBOX": "INBOX",
            "NODESEEK_EMAIL_CODE_TIMEOUT": "180",
            "NODESEEK_EMAIL_CODE_POLL_INTERVAL": "7",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("os.path.exists", return_value=False),
        ):
            module = load_module(
                "main_nodeseek_email_single_under_test",
                MAIN_PATH,
                stub_modules=build_main_stub_modules(),
            )

        config = module.build_nodeseek_account_config()

        self.assertEqual(config["email_address"], "neal@example.com")
        self.assertEqual(config["email_imap_host"], "imap.example.com")
        self.assertEqual(config["email_imap_port"], 993)
        self.assertEqual(config["email_imap_username"], "mail-user")
        self.assertEqual(config["email_imap_password"], "mail-pass")
        self.assertEqual(config["email_imap_mailbox"], "INBOX")
        self.assertEqual(config["email_code_timeout"], 180)
        self.assertEqual(config["email_code_poll_interval"], 7)

    def test_indexed_account_loads_email_imap_config(self):
        env = {
            "NODESEEK_USERNAME_2": "backup",
            "NODESEEK_PASSWORD_2": "secret",
            "NODESEEK_EMAIL_2": "backup@example.com",
            "NODESEEK_EMAIL_IMAP_HOST_2": "imap.backup.example.com",
            "NODESEEK_EMAIL_IMAP_USERNAME_2": "mail-backup",
            "NODESEEK_EMAIL_IMAP_PASSWORD_2": "mail-pass-2",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("os.path.exists", return_value=False),
        ):
            module = load_module(
                "main_nodeseek_email_indexed_under_test",
                MAIN_PATH,
                stub_modules=build_main_stub_modules(),
            )

            self.assertEqual(module.collect_nodeseek_account_indexes(), [2])
            config = module.build_nodeseek_account_config(2)

        self.assertEqual(config["email_address"], "backup@example.com")
        self.assertEqual(config["email_imap_host"], "imap.backup.example.com")
        self.assertEqual(config["email_imap_username"], "mail-backup")
        self.assertEqual(config["email_imap_password"], "mail-pass-2")

    def test_single_account_can_configure_only_imap_password(self):
        env = {
            "NODESEEK_USERNAME": "neal",
            "NODESEEK_PASSWORD": "secret",
            "NODESEEK_EMAIL_IMAP_PASSWORD": "mail-pass",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("os.path.exists", return_value=False),
        ):
            module = load_module(
                "main_nodeseek_email_minimal_under_test",
                MAIN_PATH,
                stub_modules=build_main_stub_modules(),
            )

        config = module.build_nodeseek_account_config()

        self.assertEqual(config["email_address"], "")
        self.assertEqual(config["email_imap_host"], "")
        self.assertEqual(config["email_imap_username"], "")
        self.assertEqual(config["email_imap_password"], "mail-pass")


class NodeSeekBrowserEmailVerificationTests(unittest.TestCase):
    def test_browser_login_completes_email_verification_when_required(self):
        module = load_module(
            "nodeseek_email_browser_under_test",
            NODESEEK_PATH,
            stub_modules=build_nodeseek_stub_modules(),
        )

        class FakeEmailCodeFetcher:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, *, email_address, not_before=None):
                self.calls.append((email_address, not_before))
                return "654321"

        class FakeBrowser:
            title = "NodeSeek"
            url = "https://www.nodeseek.com/signIn.html"

            def __init__(self):
                self.loaded_urls = []
                self.scripts = []

            def get(self, url, timeout=30):
                self.loaded_urls.append(url)
                self.url = url

            def wait(self, seconds):
                return None

            def run_js(self, js_code):
                self.scripts.append(js_code)
                if "querySelector('[data-sitekey]')" in js_code:
                    return ""
                if "/api/account/signIn" in js_code:
                    return json.dumps(
                        {
                            "status": 200,
                            "body": json.dumps(
                                {
                                    "success": True,
                                    "data": {
                                        "url": "/emailSignIn.html?email=neal%40example.com"
                                    },
                                }
                            ),
                        }
                    )
                if "/api/email" in js_code:
                    return json.dumps(
                        {
                            "status": 200,
                            "body": json.dumps({"success": True, "message": "sent"}),
                        }
                    )
                if "/api/account/emailSignIn" in js_code:
                    return json.dumps(
                        {
                            "status": 200,
                            "body": json.dumps({"success": True, "message": "ok"}),
                        }
                    )
                raise AssertionError(f"Unexpected JS: {js_code}")

        fetcher = FakeEmailCodeFetcher()
        mission = module.NodeSeekDailyMission(
            username="neal",
            password="password",
            solver_type="yescaptcha",
            yescaptcha_client_key="client-key",
            email_code_fetcher=fetcher,
        )
        browser = FakeBrowser()

        ok, detail = mission._browser_login(browser)

        self.assertTrue(ok, detail)
        self.assertEqual(fetcher.calls[0][0], "neal@example.com")
        self.assertIn(
            "https://www.nodeseek.com/emailSignIn.html?email=neal%40example.com",
            browser.loaded_urls,
        )
        all_js = "\n".join(browser.scripts)
        self.assertIn("/api/email", all_js)
        self.assertIn("mode: 'totp'", all_js)
        self.assertIn("version: 'v3'", all_js)
        self.assertIn("/api/account/emailSignIn", all_js)
        self.assertIn("654321", all_js)

    def test_email_fetcher_infers_missing_host_and_username(self):
        module = load_module(
            "nodeseek_email_minimal_fetcher_under_test",
            NODESEEK_PATH,
            stub_modules=build_nodeseek_stub_modules(),
        )

        mission = module.NodeSeekDailyMission(email_imap_password="mail-pass")

        fetcher = mission.build_email_code_fetcher("neal@qq.com")

        self.assertIsNotNone(fetcher)
        self.assertEqual(module.ImapEmailCodeFetcher.created_kwargs[-1]["host"], "imap.qq.com")
        self.assertEqual(module.ImapEmailCodeFetcher.created_kwargs[-1]["username"], "neal@qq.com")
        self.assertEqual(module.ImapEmailCodeFetcher.created_kwargs[-1]["password"], "mail-pass")


if __name__ == "__main__":
    unittest.main()
