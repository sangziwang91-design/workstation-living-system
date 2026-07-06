from __future__ import annotations

from pathlib import Path
import argparse
import json
import signal
import threading

from wls.runtime import LivingSystem
from wls.ui_server import WLSUIServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an installed WLS UI server")
    parser.add_argument("--config", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()

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
    ready_path = Path(args.ready_file)
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(
        json.dumps(
            {
                "url": server.bootstrap_url,
                "bound_port": server.bound_port,
                "authority": "canonical LivingSystem",
                "projection_only": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    stop.wait()
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
