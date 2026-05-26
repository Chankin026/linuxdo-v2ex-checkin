import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
XIAOHEIHE_PATH = REPO_ROOT / "xiaoheihe.py"


def load_xiaoheihe_module():
    spec = importlib.util.spec_from_file_location("xiaoheihe_under_test", XIAOHEIHE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    curl_cffi = types.ModuleType("curl_cffi")
    fake_session = mock.Mock()
    fake_session.headers = {}
    curl_cffi.requests = types.SimpleNamespace(
        get=mock.Mock(),
        Session=mock.Mock(return_value=fake_session),
    )

    loguru = types.ModuleType("loguru")
    loguru.logger = mock.Mock()

    notify = types.ModuleType("notify")
    notify.NotificationManager = object

    pure_signin = types.ModuleType("pure_signin")
    pure_signin.API_BASE = "https://api.xiaoheihe.cn"
    pure_signin.DEFAULT_ANDROID_ID = "android-id"
    pure_signin.SIGN_PATH = "/task/sign_v3/sign"
    pure_signin.SIGN_STATE_PATH = "/task/sign_v3/get_sign_state"
    pure_signin.build_signed_url = mock.Mock(return_value=("https://example.com", {}))
    pure_signin.derive_heybox_id = mock.Mock(return_value="heybox-id")
    pure_signin.parse_cookie = mock.Mock(
        return_value={"pkey": "abc", "x_xhh_tokenid": "token"}
    )

    with mock.patch.dict(
        sys.modules,
        {
            "curl_cffi": curl_cffi,
            "loguru": loguru,
            "notify": notify,
            "pure_signin": pure_signin,
        },
    ):
        spec.loader.exec_module(module)
    return module


class XiaoHeiHeNotificationTests(unittest.TestCase):
    def test_success_notification_shows_reward_instead_of_request_mode(self):
        module = load_xiaoheihe_module()
        notifier = mock.Mock()
        mission = module.XiaoHeiHeDailyMission(
            notifier=notifier,
            account_name="main",
            cookie="pkey=abc; x_xhh_tokenid=token",
        )

        mission.send_success_notification("status=ok | state=ok | H币+3 | exp+15")

        notifier.send_all.assert_called_once()
        title, message = notifier.send_all.call_args.args
        self.assertEqual(title, "Xiaoheihe")
        self.assertIn("Reward: H币+3, exp+15", message)
        self.assertNotIn("Request mode:", message)


if __name__ == "__main__":
    unittest.main()
