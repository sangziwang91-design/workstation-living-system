from __future__ import annotations

import argparse
import json
import struct
import subprocess  # nosec B404
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "source" / "src"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from wls.runtime import LivingSystem
from wls.ui_server import WLSUIServer

DEFAULT_BROWSERS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]
REQUIRED_DOM_MARKERS = [
    "Workstation Living System",
    "Owner Console",
    "Active Goals",
    "canonical projection",
]


def _find_browser(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else DEFAULT_BROWSERS
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("no Edge or Chrome executable found")


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG screenshot: {path}")
    return struct.unpack(">II", data[16:24])


def _browser_command(
    browser: Path,
    url: str,
    *,
    user_data_dir: Path,
    screenshot: Path | None = None,
    dump_dom: bool = False,
) -> list[str]:
    command = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-sync",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={user_data_dir}",
        "--window-size=1440,1000",
        "--virtual-time-budget=5000",
    ]
    if screenshot is not None:
        command.append(f"--screenshot={screenshot}")
    if dump_dom:
        command.append("--dump-dom")
    command.append(url)
    return command


def _run_browser(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=45,
    )


def _with_server(config: Path, callback: Any) -> Any:
    runtime = LivingSystem.from_config_path(config)
    server = WLSUIServer(runtime, port=0)
    server.build()
    server.start_in_thread()
    try:
        time.sleep(0.25)
        return callback(server)
    finally:
        server.shutdown()


def run_smoke(*, config: Path, browser: Path, evidence_dir: Path) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = evidence_dir / "owner-console-home.png"
    dom_path = evidence_dir / "owner-console-home.dom.html"

    with tempfile.TemporaryDirectory(prefix="wls-ui-browser-") as temp_root:
        temp = Path(temp_root)

        def capture_screenshot(server: WLSUIServer) -> dict[str, Any]:
            command = _browser_command(
                browser,
                server.bootstrap_url,
                user_data_dir=temp / "screen-profile",
                screenshot=screenshot_path,
            )
            result = _run_browser(command, cwd=REPO_ROOT)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
            width, height = _png_dimensions(screenshot_path)
            return {
                "path": str(screenshot_path),
                "bytes": screenshot_path.stat().st_size,
                "width": width,
                "height": height,
                "stderr_tail": result.stderr[-800:],
            }

        screenshot = _with_server(config, capture_screenshot)

        def capture_dom(server: WLSUIServer) -> dict[str, Any]:
            command = _browser_command(
                browser,
                server.bootstrap_url,
                user_data_dir=temp / "dom-profile",
                dump_dom=True,
            )
            result = _run_browser(command, cwd=REPO_ROOT)
            dom_path.write_text(result.stdout, encoding="utf-8")
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])
            missing = [marker for marker in REQUIRED_DOM_MARKERS if marker not in result.stdout]
            if missing:
                raise RuntimeError(f"rendered DOM missing markers: {missing}")
            return {
                "path": str(dom_path),
                "bytes": dom_path.stat().st_size,
                "markers": REQUIRED_DOM_MARKERS,
                "stderr_tail": result.stderr[-800:],
            }

        dom = _with_server(config, capture_dom)

    if screenshot["bytes"] < 10_000:
        raise RuntimeError("browser screenshot is unexpectedly small")
    if screenshot["width"] < 1000 or screenshot["height"] < 700:
        raise RuntimeError("browser screenshot dimensions are unexpectedly small")

    return {
        "status": "PASS",
        "browser": str(browser),
        "config": str(config),
        "evidence_dir": str(evidence_dir),
        "screenshot": screenshot,
        "dom": dom,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run WLS UI browser smoke test")
    parser.add_argument("--config", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--browser")
    args = parser.parse_args(argv)

    try:
        browser = _find_browser(args.browser)
        result = run_smoke(
            config=Path(args.config),
            browser=browser,
            evidence_dir=Path(args.evidence_dir),
        )
    except Exception as exc:
        result = {
            "status": "FAIL",
            "error": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
