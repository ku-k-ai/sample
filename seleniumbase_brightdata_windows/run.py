"""Windows-native runner: .env -> loopback relay -> supervised browser worker.

No Docker, WSL, Xvfb, shell-evaluated settings, or administrator privileges.
Only the parent process receives the upstream proxy credentials.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relay import ConnectRelay, ProxyConfig

ROOT = Path(__file__).resolve().parent
KEYS = {
    "BRD_PROXY_HOST", "BRD_PROXY_PORT", "BRD_PROXY_USER", "BRD_PROXY_PASS",
    "BRD_PROXY_SCHEME", "BRD_PROXY_CA_FILE", "TARGET_URL", "EXPECT_TEXT",
    "EXPECT_SELECTOR", "BROWSER_MODE", "BROWSER_BINARY", "HEADLESS",
    "PAGE_TIMEOUT", "RUN_TIMEOUT", "SAVE_PAGE_ARTIFACTS", "ARTIFACT_DIR",
}


def read_settings(path: Path, environ: dict[str, str]) -> dict[str, str]:
    """Literal KEY=value format; no interpolation, inline comments, or eval."""
    settings: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in KEYS or key in settings:
            raise ValueError(f"Invalid or duplicate setting on line {number}")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed quote on line {number}")
            value = value[1:-1]
        if any(c in value for c in ("\x00", "\r", "\n")):
            raise ValueError(f"Invalid setting on line {number}")
        settings[key] = value
    settings.update({key: environ[key] for key in KEYS if key in environ})
    return settings


def required(settings: dict[str, str], name: str) -> str:
    value = settings.get(name, "")
    if not value or value.startswith("CHANGE_ME"):
        raise ValueError(f"Set {name} in your local .env file")
    return value


def worker_environment(settings: dict[str, str], output: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    # .env is read only here; browser_task.py never reads it.
    env.update(settings)
    for key in list(env):
        if key.startswith("BRD_"):
            del env[key]
    env.update(LOCAL_PROXY=f"127.0.0.1:{port}", ARTIFACT_DIR=str(output.resolve()),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def stop_worker(process: subprocess.Popen, windows: bool | None = None) -> None:
    """On timeout/Ctrl+C kill only this worker tree, never all chrome.exe."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        if process.poll() is None:
            # /T includes browser/driver children; /PID scopes this run only.
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if result.returncode and process.poll() is None:
                process.kill()
    else:
        # Supports local Linux unit tests; Windows never reaches os.killpg.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=3)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    output: Path | None = None
    summary: dict[str, Any] = {"ok": False, "phase": "configuration", "platform": sys.platform}
    process = None
    try:
        settings = read_settings(args.env_file.resolve(), dict(os.environ))
        if args.headless:
            settings["HEADLESS"] = "1"
        timeout = int(settings.get("RUN_TIMEOUT", "180"))
        if not 1 <= timeout <= 86400:
            raise ValueError("RUN_TIMEOUT must be 1..86400 seconds")
        for key in ("HEADLESS", "SAVE_PAGE_ARTIFACTS"):
            if settings.get(key, "0") not in ("0", "1"):
                raise ValueError(f"{key} must be 0 or 1")
        for key in ("BRD_PROXY_HOST", "BRD_PROXY_PORT", "BRD_PROXY_USER", "BRD_PROXY_PASS"):
            required(settings, key)
        config = ProxyConfig(
            host=settings["BRD_PROXY_HOST"], port=int(settings["BRD_PROXY_PORT"]),
            username=settings["BRD_PROXY_USER"], password=settings["BRD_PROXY_PASS"],
            scheme=settings.get("BRD_PROXY_SCHEME", "http"),
            extra_ca=settings.get("BRD_PROXY_CA_FILE") or None,
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        base = Path(settings.get("ARTIFACT_DIR", "artifacts"))
        if not base.is_absolute():
            base = ROOT / base
        output = base / stamp
        output.mkdir(parents=True, exist_ok=False)
        summary["phase"] = "relay_start"
        with ConnectRelay(config, output / "relay.jsonl") as relay:
            env = worker_environment(settings, output, relay.port)
            summary["phase"] = "browser_worker"
            print("Relay ready. Starting browser worker.", flush=True)
            options: dict[str, Any] = {}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            with (output / "worker.log").open("wb") as log:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "browser_task.py")], cwd=ROOT,
                    env=env, stdout=log, stderr=subprocess.STDOUT, **options,
                )
                try:
                    code = process.wait(timeout=timeout)
                finally:
                    stop_worker(process)
            report_path = output / "result.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            summary.update(phase="complete", worker_exit_code=code, browser=report,
                           ok=(code == 0 and report.get("ok") is True))
    except subprocess.TimeoutExpired:
        summary.update(phase="wall_timeout", error_type="TimeoutExpired")
    except KeyboardInterrupt:
        summary.update(phase="interrupted", error_type="KeyboardInterrupt")
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        # Raw exception text may contain URLs, filenames, or entered credentials.
        if summary["phase"] == "configuration":
            summary["hint"] = "Check .env path/format, required settings, and numeric values."
    finally:
        if process is not None and process.poll() is None:
            stop_worker(process)
        if output is not None:
            (output / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if output is not None:
        print(f"Artifacts: {output}", flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
