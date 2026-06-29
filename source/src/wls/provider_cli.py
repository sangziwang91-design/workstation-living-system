from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import argparse
import getpass
import json
import os

from .provider_hub import ProviderHub


_LITERAL_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _default_home() -> Path:
    configured = os.environ.get("WLS_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WLS"
        ).resolve()
    return (Path.home() / ".wls").resolve()


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _validate_cli_custom_base(provider_id: str, base_url: str | None) -> None:
    if provider_id != "custom_openai" or base_url is None:
        return
    parsed = urlparse(base_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in _LITERAL_LOOPBACK_HOSTS:
        raise ValueError(
            "custom_openai is limited to literal loopback/localhost while the "
            "Provider Hub remains candidate-only; use a fixed reviewed preset for public providers"
        )
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("custom_openai base URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("userinfo credentials in provider URLs are forbidden")
    if parsed.fragment:
        raise ValueError("provider URL fragments are forbidden")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wls-provider",
        description=(
            "Candidate-only local Provider Hub. Configuration does not attach a "
            "provider to the canonical WLS planner."
        ),
    )
    parser.add_argument("--home", default=str(_default_home()))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List provider presets and local configuration")
    status = sub.add_parser("status", help="Show one provider without secrets")
    status.add_argument("provider_id")

    configure = sub.add_parser("configure", help="Configure non-secret provider metadata")
    configure.add_argument("provider_id")
    configure.add_argument("--model")
    configure.add_argument("--base-url")
    configure.add_argument("--disabled", action="store_true")
    configure.add_argument(
        "--store-credential",
        action="store_true",
        help="Read a credential from an interactive hidden prompt and store it in the OS vault",
    )

    probe = sub.add_parser("probe", help="Run a bounded models-endpoint probe")
    probe.add_argument("provider_id")
    probe.add_argument("--timeout-seconds", type=float, default=15.0)

    select = sub.add_parser("select", help="Select an enabled configured provider candidate")
    select.add_argument("provider_id")

    remove = sub.add_parser(
        "remove-credential", help="Remove a provider credential from the OS vault"
    )
    remove.add_argument("provider_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    hub = ProviderHub(Path(args.home))
    try:
        if args.command == "list":
            _print(hub.list())
        elif args.command == "status":
            _print(hub.get_public(args.provider_id))
        elif args.command == "configure":
            _validate_cli_custom_base(args.provider_id, args.base_url)
            credential = None
            if args.store_credential:
                credential = getpass.getpass("Provider credential: ")
            _print(
                hub.configure(
                    args.provider_id,
                    credential=credential,
                    enabled=not args.disabled,
                    model=args.model,
                    base_url=args.base_url,
                )
            )
        elif args.command == "probe":
            result = hub.probe(
                args.provider_id, timeout_seconds=args.timeout_seconds
            )
            _print(result)
            return 0 if result["status"] == "PASS" else 2
        elif args.command == "select":
            _print(hub.select(args.provider_id))
        elif args.command == "remove-credential":
            _print(hub.remove_credential(args.provider_id))
        else:  # pragma: no cover
            raise ValueError(f"unsupported command: {args.command}")
        return 0
    except (KeyError, ValueError, RuntimeError) as exc:
        _print({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
