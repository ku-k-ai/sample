"""SeleniumBase worker; upstream proxy credentials are absent from its environment.

Default: Chrome for Testing, visible Windows desktop, standard WebDriver mode.
The narrow options adapter below targets the pinned SeleniumBase 4.51.12.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def harden_options(options: Any) -> Any:
    """Remove SeleniumBase's implicit TLS bypasses, not Windows-wide policy."""
    rejected = {
        "--ignore-certificate-errors", "--ignore-certificate-errors-spki-list",
        "--ignore-ssl-errors", "--allow-insecure-localhost",
        "--allow-running-insecure-content", "--disable-web-security",
    }
    # ChromiumOptions.arguments is the mutable argument list.
    options.arguments[:] = [arg for arg in options.arguments if arg.split("=", 1)[0] not in rejected]
    options.accept_insecure_certs = False
    return options


@contextlib.contextmanager
def strict_chrome_options():
    from seleniumbase.core import browser_launcher
    original = browser_launcher._set_chrome_options

    def wrapped(*args, **kwargs):
        return harden_options(original(*args, **kwargs))

    browser_launcher._set_chrome_options = wrapped
    try:
        yield
    finally:
        browser_launcher._set_chrome_options = original


def navigation_status(driver: Any) -> int | None:
    """Match the current top-frame loader, not a stale pre-redirect document."""
    frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]
    status = None
    for record in driver.get_log("performance"):
        try:
            message = json.loads(record["message"])["message"]
            params = message.get("params", {})
            if (message.get("method") == "Network.responseReceived"
                and params.get("type") == "Document"
                and params.get("frameId") == frame["id"]
                and (not frame.get("loaderId") or params.get("loaderId") == frame["loaderId"])):
                status = int(params["response"]["status"])
        except (KeyError, TypeError, ValueError):
            continue
    if status is None:
        value = driver.execute_script(
            "return performance.getEntriesByType('navigation')[0]?.responseStatus || 0"
        )
        status = int(value) if value else None
    return status


def main() -> int:
    output = Path(os.environ["ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"ok": False, "phase": "configuration", "platform": sys.platform}
    code = 1
    try:
        target = os.environ.get("TARGET_URL", "https://geo.brdtest.com/welcome.txt")
        parsed = urlsplit(target)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("TARGET_URL must be an HTTPS URL without userinfo")
        timeout = int(os.environ.get("PAGE_TIMEOUT", "45"))
        if not 1 <= timeout <= 3600:
            raise ValueError("PAGE_TIMEOUT must be 1..3600 seconds")
        mode = os.environ.get("BROWSER_MODE", "standard")
        if mode not in ("standard", "uc"):
            raise ValueError("BROWSER_MODE must be standard or uc")
        headless = os.environ.get("HEADLESS", "0") == "1"
        local_proxy = os.environ["LOCAL_PROXY"]
        if not re.fullmatch(r"127\.0\.0\.1:\d{1,5}", local_proxy):
            raise ValueError("LOCAL_PROXY must be the loopback relay")
        result.update(phase="browser_import", mode=mode, headless=headless)
        from seleniumbase import SB
        from selenium.webdriver.support.ui import WebDriverWait
        result["seleniumbase"] = importlib.metadata.version("seleniumbase")
        result["phase"] = "browser_start"
        with strict_chrome_options(), SB(
            browser="chrome", binary_location=os.environ.get("BROWSER_BINARY", "cft"),
            proxy=local_proxy, proxy_bypass_list="<-loopback>",
            headed=not headless, headless=headless, xvfb=False,
            uc=(mode == "uc"), enable_ws=True, log_cdp_events=True,
            page_load_strategy="eager", skip_js_waits=True,
            chromium_arg=["--disable-quic", "--window-size=1365,900",
                          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            raise_test_failure=True,
        ) as sb:
            driver = sb.driver
            try:
                result["browser_version"] = driver.capabilities.get("browserVersion")
                result["chromedriver_version"] = driver.capabilities.get("chrome", {}).get("chromedriverVersion", "").split(" ")[0]
                if driver.capabilities.get("acceptInsecureCerts") is True:
                    raise RuntimeError("Unexpected acceptInsecureCerts=True")
                driver.set_page_load_timeout(timeout)
                driver.set_script_timeout(10)
                driver.execute_cdp_cmd("Network.enable", {})
                driver.get_log("performance")
                result["phase"] = "navigation"
                driver.get(target)
                WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return !!document.body"))
                if urlsplit(driver.current_url).scheme != "https":
                    raise RuntimeError("Navigation did not finish on HTTPS")
                result["phase"] = "http_status"
                status = navigation_status(driver)
                result["http_status"] = status
                if status is None or not 200 <= status < 300:
                    raise RuntimeError("No confirmed successful top-level HTTP response")
                result["phase"] = "content_check"
                text = os.environ.get("EXPECT_TEXT", "")
                selector = os.environ.get("EXPECT_SELECTOR", "")
                if selector:
                    sb.wait_for_element_visible(selector, timeout=timeout)
                if text:
                    sb.assert_text(text, "body", timeout=timeout)
                result["content_asserted"] = bool(text or selector)
                result["verdict"] = "PASS_CONTENT_ASSERTION" if (text or selector) else "PASS_HTTP_ONLY_CONTENT_UNVERIFIED"
                result.update(ok=True, phase="complete")
                code = 0
            except Exception as exc:
                traceback.print_exc()  # Raw details stay in local worker.log.
                result.update(ok=False, error_type=type(exc).__name__,
                              chrome_error_codes=sorted(set(re.findall(r"ERR_[A-Z0-9_]+", str(exc)))))
                code = 1
            finally:
                if os.environ.get("SAVE_PAGE_ARTIFACTS", "0") == "1":
                    with contextlib.suppress(Exception):
                        driver.save_screenshot(str(output / "page.png"))
                    with contextlib.suppress(Exception):
                        (output / "page.html").write_text(driver.page_source, encoding="utf-8")
    except Exception as exc:
        traceback.print_exc()  # Never upload worker.log without reviewing it.
        result.update(ok=False, error_type=type(exc).__name__,
                      chrome_error_codes=sorted(set(re.findall(r"ERR_[A-Z0-9_]+", str(exc)))))
        code = 1
    if not result["ok"]:
        code = 1
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
