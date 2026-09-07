"""Unit tests of success/failure reporting with a FAKE SeleniumBase driver."""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_task


class Driver:
    def __init__(self, status=200, insecure=False, navigation_error=False):
        self.status = status
        self.capabilities = {"browserVersion": "MOCK", "acceptInsecureCerts": insecure}
        self.current_url = "https://private.example.test/secret"
        self.navigation_error = navigation_error

    def set_page_load_timeout(self, *_): pass
    def set_script_timeout(self, *_): pass
    def execute_cdp_cmd(self, command, args):
        return {"frameTree": {"frame": {"id": "main"}}} if command == "Page.getFrameTree" else {}
    def get_log(self, *_):
        return [{"message": json.dumps({"message": {"method": "Network.responseReceived", "params": {
            "type": "Document", "frameId": "main", "response": {"status": self.status}
        }}})}] if self.status is not None else []
    def execute_script(self, code):
        return 0 if "responseStatus" in code else True
    def get(self, *_):
        if self.navigation_error:
            raise RuntimeError("ERR_TUNNEL_CONNECTION_FAILED at https://private.example.test/secret")


class Wait:
    def __init__(self, driver, timeout): self.driver = driver
    def until(self, predicate): assert predicate(self.driver)


class FakeSB:
    def __init__(self, driver, assertion_failure=False):
        self.driver = driver
        self.assertion_failure = assertion_failure
    def __enter__(self): return self
    def __exit__(self, *_): return True  # Simulate a context that can swallow errors.
    def wait_for_element_visible(self, *_, **__):
        if self.assertion_failure: raise AssertionError("missing")
    def assert_text(self, *_, **__):
        if self.assertion_failure: raise AssertionError("missing")


class WorkerTests(unittest.TestCase):
    def run_worker(self, status=200, insecure=False, navigation_error=False, expected="", assertion_failure=False):
        fake = FakeSB(Driver(status, insecure, navigation_error), assertion_failure)
        sb_module = types.ModuleType("seleniumbase")
        sb_module.SB = lambda **kwargs: fake
        ui_module = types.ModuleType("selenium.webdriver.support.ui")
        ui_module.WebDriverWait = Wait
        modules = {name: types.ModuleType(name) for name in ("selenium", "selenium.webdriver", "selenium.webdriver.support")}
        modules.update({"seleniumbase": sb_module, "selenium.webdriver.support.ui": ui_module})
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules), patch.dict(os.environ, {
            "ARTIFACT_DIR": directory, "LOCAL_PROXY": "127.0.0.1:1", "TARGET_URL": "https://private.example.test/secret",
            "EXPECT_TEXT": expected, "SAVE_PAGE_ARTIFACTS": "0", "BROWSER_MODE": "standard",
        }, clear=True), patch("importlib.metadata.version", return_value="MOCK"), patch.object(browser_task, "strict_chrome_options", side_effect=contextlib.nullcontext), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = browser_task.main()
            result = json.loads((Path(directory) / "result.json").read_text())
        return code, result

    def test_http_200_is_not_called_content_success(self):
        code, result = self.run_worker()
        self.assertEqual(code, 0)
        self.assertEqual(result["verdict"], "PASS_HTTP_ONLY_CONTENT_UNVERIFIED")

    def test_expected_content_success(self):
        code, result = self.run_worker(expected="desired")
        self.assertEqual(code, 0)
        self.assertEqual(result["verdict"], "PASS_CONTENT_ASSERTION")

    def test_403_must_fail(self):
        code, result = self.run_worker(status=403)
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 403)

    def test_missing_content_must_fail_even_if_context_swallows(self):
        code, result = self.run_worker(expected="desired", assertion_failure=True)
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])

    def test_unknown_status_is_not_success(self):
        code, result = self.run_worker(status=None)
        self.assertEqual(code, 1)

    def test_insecure_capability_is_rejected(self):
        code, result = self.run_worker(insecure=True)
        self.assertEqual(code, 1)

    def test_navigation_error_redacts_private_url(self):
        code, result = self.run_worker(navigation_error=True)
        self.assertEqual(code, 1)
        self.assertIn("ERR_TUNNEL_CONNECTION_FAILED", result["chrome_error_codes"])
        self.assertNotIn("private.example", json.dumps(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
