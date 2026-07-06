from __future__ import annotations

from pathlib import Path
import argparse
import json
import signal
import sys
import threading


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "source" / "src"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from wls.runtime import LivingSystem  # noqa: E402
from wls.ui_server import WLSUIServer  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run WLS UI server for visible QA")
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args(argv)

    runtime = LivingSystem.from_config_path(Path(args.config))
    server = WLSUIServer(runtime, host=args.host, port=args.port)
    server.build()
    stop = threading.Event()

    def stop_server(*_: object) -> None:
        stop.set()
        server.shutdown()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    thread = server.start_in_thread()
    ready = {
        "url": server.bootstrap_url,
        "bound_port": server.bound_port,
        "authority": "canonical LivingSystem",
        "projection_only": True,
    }
    Path(args.ready_file).write_text(
        json.dumps(ready, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    stop.wait()
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
