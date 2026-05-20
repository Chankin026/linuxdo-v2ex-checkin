import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LINUXDO_CLOAK_PATH = REPO_ROOT / "linuxdo_cloak.py"
MAIN_PATH = REPO_ROOT / "main.py"


def load_module(module_name: str, path: pathlib.Path, stub_modules=None):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(sys.modules, stub_modules or {}):
        spec.loader.exec_module(module)
    return module


def build_main_stub_modules():
    logger = mock.Mock()

    drissionpage = types.ModuleType("DrissionPage")
    drissionpage.Chromium = object
    drissionpage.ChromiumOptions = object

    curl_cffi = types.ModuleType("curl_cffi")
    curl_cffi.requests = types.SimpleNamespace(
        Session=mock.Mock,
        request=mock.Mock(),
        get=mock.Mock(),
        post=mock.Mock(),
    )

    curl_cffi_const = types.ModuleType("curl_cffi.const")
    curl_cffi_const.CurlIpResolve = types.SimpleNamespace(V4="V4")
    curl_cffi_const.CurlOpt = types.SimpleNamespace(IPRESOLVE="IPRESOLVE")

    bs4 = types.ModuleType("bs4")
    bs4.BeautifulSoup = object

    loguru = types.ModuleType("loguru")
    loguru.logger = logger

    class DummyMission:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return True

    nodeseek = types.ModuleType("nodeseek")
    nodeseek.NodeSeekDailyMission = DummyMission

    v2ex = types.ModuleType("v2ex")
    v2ex.V2EXDailyMission = DummyMission

    xiaoheihe = types.ModuleType("xiaoheihe")
    xiaoheihe.XIAOHEIHE_REQUEST_MODE_LABELS = {}
    xiaoheihe.XiaoHeiHeDailyMission = DummyMission
    xiaoheihe.resolve_request_mode_label = lambda mode: mode

    return {
        "DrissionPage": drissionpage,
        "curl_cffi": curl_cffi,
        "curl_cffi.const": curl_cffi_const,
        "bs4": bs4,
        "loguru": loguru,
        "nodeseek": nodeseek,
        "v2ex": v2ex,
        "xiaoheihe": xiaoheihe,
    }


class LinuxDoCloakDependencyTests(unittest.TestCase):
    def test_load_cloakbrowser_wraps_missing_dependency(self):
        module = load_module("linuxdo_cloak_under_test", LINUXDO_CLOAK_PATH)

        with mock.patch.object(
            module.importlib,
            "import_module",
            side_effect=ModuleNotFoundError("No module named 'cloakbrowser'"),
        ):
            with self.assertRaises(ModuleNotFoundError) as ctx:
                module.load_cloakbrowser()

        self.assertIn("pip install cloakbrowser", str(ctx.exception))


class MainLinuxDoEntryTests(unittest.TestCase):
    def test_run_configured_tasks_uses_linuxdo_cloak_entrypoint(self):
        module = load_module(
            "main_linuxdo_entry_under_test",
            MAIN_PATH,
            stub_modules=build_main_stub_modules(),
        )

        linuxdo_module = mock.Mock()
        linuxdo_module.run_linuxdo_task.return_value = True

        module.COOKIES = "foo=bar"
        module.USERNAME = ""
        module.PASSWORD = ""
        module.V2EX_ENABLED = False
        module.V2EX_COOKIE = ""
        module.NODESEEK_ENABLED = False
        module.XIAOHEIHE_COOKIE = ""
        module.load_linuxdo_cloak_module = mock.Mock(return_value=linuxdo_module)
        module.LinuxDoBrowser = mock.Mock(
            side_effect=AssertionError("old LinuxDoBrowser path should not be used")
        )

        module.run_configured_tasks()

        module.load_linuxdo_cloak_module.assert_called_once_with()
        linuxdo_module.run_linuxdo_task.assert_called_once_with(headless=False)

    def test_run_configured_tasks_does_not_import_xiaoheihe_when_disabled(self):
        stub_modules = build_main_stub_modules()
        module = load_module(
            "main_linuxdo_entry_without_xiaoheihe_under_test",
            MAIN_PATH,
            stub_modules=stub_modules,
        )

        linuxdo_module = mock.Mock()
        linuxdo_module.run_linuxdo_task.return_value = True

        module.COOKIES = "foo=bar"
        module.USERNAME = ""
        module.PASSWORD = ""
        module.V2EX_ENABLED = False
        module.V2EX_COOKIE = ""
        module.NODESEEK_ENABLED = False
        module.XIAOHEIHE_COOKIE = ""
        module.load_linuxdo_cloak_module = mock.Mock(return_value=linuxdo_module)
        module.load_xiaoheihe_module = mock.Mock(
            side_effect=AssertionError("xiaoheihe should not load when disabled")
        )

        module.run_configured_tasks()

        module.load_linuxdo_cloak_module.assert_called_once_with()
        module.load_xiaoheihe_module.assert_not_called()


if __name__ == "__main__":
    unittest.main()
