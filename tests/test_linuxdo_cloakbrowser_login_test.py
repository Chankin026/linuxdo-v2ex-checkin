import importlib.util
import pathlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
