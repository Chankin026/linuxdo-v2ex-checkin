import importlib.util
import pathlib
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "linuxdo_cloakbrowser_login_test.py"
SPEC = importlib.util.spec_from_file_location("linuxdo_cloakbrowser_login_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CookieParsingTests(unittest.TestCase):
    def test_parse_cookie_string_ignores_invalid_segments(self):
        cookies = MODULE.parse_cookie_string("foo=1; bar=2; invalid; =bad; baz=3")
        self.assertEqual([item["name"] for item in cookies], ["foo", "bar", "baz"])
        self.assertEqual([item["value"] for item in cookies], ["1", "2", "3"])
        self.assertTrue(all(item["domain"] == "linux.do" for item in cookies))

    def test_page_looks_logged_in_checks_preferences_url(self):
        self.assertTrue(
            MODULE.page_looks_logged_in(
                {"url": "https://linux.do/my/preferences/account", "looks_logged_in": False}
            )
        )
        self.assertFalse(
            MODULE.page_looks_logged_in(
                {"url": "https://linux.do/login", "looks_logged_in": False}
            )
        )


class SolverSettingsTests(unittest.TestCase):
    def test_load_solver_settings_uses_linuxdo_hcaptcha_defaults(self):
        with mock.patch.dict(MODULE.os.environ, {}, clear=True):
            settings = MODULE.load_solver_settings()

        self.assertEqual(settings["turnstile"]["max_retries"], 20)
        self.assertEqual(settings["turnstile"]["retry_interval"], 3)
        self.assertEqual(settings["turnstile"]["timeout"], 60)
        self.assertEqual(settings["hcaptcha"]["max_retries"], 45)
        self.assertEqual(settings["hcaptcha"]["retry_interval"], 4)
        self.assertEqual(settings["hcaptcha"]["timeout"], 600)

    def test_load_solver_settings_allows_linuxdo_hcaptcha_overrides(self):
        with mock.patch.dict(
            MODULE.os.environ,
            {
                "LINUXDO_YESCAPTCHA_HCAPTCHA_MAX_RETRIES": "9",
                "LINUXDO_YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL": "5",
                "LINUXDO_YESCAPTCHA_HCAPTCHA_TIMEOUT": "120",
            },
            clear=True,
        ):
            settings = MODULE.load_solver_settings()

        self.assertEqual(settings["hcaptcha"]["max_retries"], 9)
        self.assertEqual(settings["hcaptcha"]["retry_interval"], 5)
        self.assertEqual(settings["hcaptcha"]["timeout"], 120)


class HCaptchaCheckboxTests(unittest.TestCase):
    def test_hcaptcha_checkbox_looks_solved_when_aria_checked_true(self):
        frame = mock.Mock()
        checkbox = mock.Mock()
        frame.locator.return_value = checkbox
        checkbox.count.return_value = 1
        checkbox.get_attribute.return_value = "true"

        self.assertTrue(MODULE.hcaptcha_checkbox_looks_solved(frame))

    def test_hcaptcha_checkbox_looks_solved_when_checkbox_missing(self):
        frame = mock.Mock()
        checkbox = mock.Mock()
        frame.locator.return_value = checkbox
        checkbox.count.return_value = 0

        self.assertFalse(MODULE.hcaptcha_checkbox_looks_solved(frame))

    def test_try_click_hcaptcha_checkbox_if_needed_clicks_checkbox(self):
        page = mock.Mock()
        frame_locator = mock.Mock()
        frame = mock.Mock()
        checkbox = mock.Mock()

        page.frame_locator.return_value = frame_locator
        frame_locator.locator.return_value = checkbox
        checkbox.count.return_value = 1
        checkbox.get_attribute.side_effect = ["false", "true"]

        frame.locator.return_value = checkbox
        with mock.patch.object(MODULE, "wait_for_settle") as wait_for_settle:
            clicked = MODULE.try_click_hcaptcha_checkbox_if_needed(page)

        self.assertTrue(clicked)
        page.frame_locator.assert_called_once_with('iframe[src*="hcaptcha.com"]')
        self.assertGreaterEqual(frame_locator.locator.call_count, 1)
        checkbox.click.assert_called_once()
        self.assertGreaterEqual(wait_for_settle.call_count, 1)


class LinuxDoHCaptchaHandshakeTests(unittest.TestCase):
    def test_get_csrf_token_prefers_meta_tag(self):
        page = mock.Mock()
        page.evaluate.return_value = "csrf-from-meta"

        token = MODULE.get_csrf_token(page)

        self.assertEqual(token, "csrf-from-meta")

    def test_fetch_csrf_token_from_linuxdo_reads_session_endpoint(self):
        page = mock.Mock()
        page.evaluate.return_value = {"ok": True, "status": 200, "body": '{"csrf":"endpoint-csrf"}'}

        result = MODULE.fetch_csrf_token_from_linuxdo(page)

        self.assertEqual(result["token"], "endpoint-csrf")
        self.assertEqual(result["status"], 200)
        args = page.evaluate.call_args.args
        self.assertEqual(len(args), 1)
        self.assertIn("/session/csrf", args[0])

    def test_get_captcha_response_fields_reads_existing_challenge_values(self):
        page = mock.Mock()
        page.evaluate.return_value = {
            "cf-turnstile-response": "turnstile-token",
            "h-captcha-response": "hcaptcha-token",
            "empty": "",
        }

        fields = MODULE.get_captcha_response_fields(page)

        self.assertEqual(fields["cf-turnstile-response"], "turnstile-token")
        self.assertEqual(fields["h-captcha-response"], "hcaptcha-token")
        self.assertNotIn("empty", fields)

    def test_register_hcaptcha_token_posts_to_linuxdo_endpoint(self):
        page = mock.Mock()
        page.evaluate.return_value = {"ok": True, "status": 200, "body": '{"success":"OK"}'}

        result = MODULE.register_hcaptcha_token_with_linuxdo(page, "csrf-token", "pass-token")

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["json"].get("success"), "OK")
        args = page.evaluate.call_args.args
        self.assertEqual(len(args), 1)
        self.assertIn("/hcaptcha/create.json", args[0])
        self.assertIn("csrf-token", args[0])
        self.assertIn("pass-token", args[0])

    def test_submit_login_with_linuxdo_posts_credentials(self):
        page = mock.Mock()
        page.evaluate.return_value = {"ok": True, "status": 200, "url": "https://linux.do/session", "body": '{"result":"ok"}'}

        with mock.patch.object(
            MODULE,
            "get_captcha_response_fields",
            return_value={"cf-turnstile-response": "turnstile-token"},
        ):
            result = MODULE.submit_login_with_linuxdo(
                page,
                "csrf-token",
                "user@example.com",
                "password123",
                "Asia/Shanghai",
                hcaptcha_token="pass-token",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        args = page.evaluate.call_args.args
        self.assertEqual(len(args), 1)
        self.assertIn("/session", args[0])
        self.assertIn("csrf-token", args[0])
        self.assertIn("user@example.com", args[0])
        self.assertIn("password123", args[0])
        self.assertNotIn("second_factor_method", args[0])
        self.assertIn("h-captcha-response", args[0])
        self.assertIn("g-recaptcha-response", args[0])
        self.assertIn("hcaptcha_token", args[0])
        self.assertIn("pass-token", args[0])
        self.assertIn("cf-turnstile-response", args[0])
        self.assertIn("turnstile-token", args[0])
        self.assertIn("X-Requested-With", args[0])
        self.assertIn("Discourse-Present", args[0])
        self.assertIn("Accept", args[0])


if __name__ == "__main__":
    unittest.main()
