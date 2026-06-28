from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import getpass
import json
import os
import threading
import webbrowser

from .config import default_config, load_or_create_config
from .provider_hub import ProviderHub
from .runtime import LivingSystem
from .server import WLSServer


def _default_config_path() -> Path:
    configured = os.environ.get("WLS_CONFIG")
    if configured:
        return Path(configured)
    home = os.environ.get("WLS_HOME")
    if home:
        return Path(home) / "config.json"
    return default_config().home_path / "config.json"


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))


def _hub(config_path: str | None) -> ProviderHub:
    config = load_or_create_config(Path(config_path or _default_config_path()))
    return ProviderHub(config.home_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m wls.provider_cli",
        description="WLS local provider switchboard",
    )
    parser.add_argument("--config", help="Path to the WLS config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List provider metadata without exposing credentials")

    set_key = sub.add_parser("set-key", help="Store one provider credential in the OS vault")
    set_key.add_argument("provider_id")

    delete_key = sub.add_parser("delete-key", help="Delete one provider credential")
    delete_key.add_argument("provider_id")

    configure = sub.add_parser("configure", help="Configure provider model and enabled state")
    configure.add_argument("provider_id")
    configure.add_argument("--model")
    configure.add_argument("--base-url")
    configure.add_argument("--disabled", action="store_true")

    probe = sub.add_parser("probe", help="Validate access and discover visible models")
    probe.add_argument("provider_id")
    probe.add_argument("--timeout", type=float, default=15.0)

    select = sub.add_parser("select", help="Set the preferred candidate provider")
    select.add_argument("provider_id")

    ui = sub.add_parser("ui", help="Open the loopback-only provider page")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        hub = _hub(args.config)
        if args.command == "list":
            _json(hub.list())
        elif args.command == "set-key":
            provider = hub.get_public(args.provider_id)
            prompt = f"Paste the {provider['name']} credential (input hidden): "
            value = getpass.getpass(prompt)
            hub.secrets.set(args.provider_id, value)
            if not provider["enabled"]:
                hub.configure(
                    args.provider_id,
                    enabled=True,
                    model=provider["model"],
                    base_url=(
                        provider["configured_base_url"]
                        if provider["custom_base_url"]
                        else None
                    ),
                )
            _json(
                {
                    "provider_id": args.provider_id,
                    "credential_configured": True,
                    "stored_in": hub.secrets.status()["backend"],
                    "plaintext_in_wls_files": False,
                }
            )
        elif args.command == "delete-key":
            _json(hub.remove_credential(args.provider_id))
        elif args.command == "configure":
            _json(
                hub.configure(
                    args.provider_id,
                    enabled=not args.disabled,
                    model=args.model,
                    base_url=args.base_url,
                )
            )
        elif args.command == "probe":
            _json(hub.probe(args.provider_id, timeout_seconds=args.timeout))
        elif args.command == "select":
            _json(hub.select(args.provider_id))
        elif args.command == "ui":
            config_path = Path(args.config or _default_config_path())
            runtime = LivingSystem.from_config_path(config_path)
            server = WLSServer(runtime, args.host, args.port)
            url = server.provider_ui_url()
            _json(
                {
                    "provider_ui": url,
                    "host": args.host,
                    "port": args.port,
                    "credential_entry": "terminal getpass -> OS credential vault",
                    "runtime_attached": False,
                }
            )
            if not args.no_browser:
                threading.Timer(0.5, webbrowser.open, args=(url,)).start()
            server.serve_forever()
        return 0
    except (KeyboardInterrupt, EOFError):
        _json({"error": "cancelled"})
        return 130
    except Exception as exc:
        _json({"error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
