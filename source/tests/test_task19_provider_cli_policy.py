from __future__ import annotations

import pytest

from wls.provider_cli import _validate_cli_custom_base


def test_custom_provider_cli_allows_only_literal_loopback() -> None:
    _validate_cli_custom_base("custom_openai", "http://127.0.0.1:8000/v1")
    _validate_cli_custom_base("custom_openai", "http://localhost:11434/v1")
    _validate_cli_custom_base("custom_openai", "http://[::1]:8000/v1")

    with pytest.raises(ValueError, match="literal loopback"):
        _validate_cli_custom_base("custom_openai", "https://example.com/v1")
    with pytest.raises(ValueError, match="literal loopback"):
        _validate_cli_custom_base("custom_openai", "http://2130706433:8000/v1")
    with pytest.raises(ValueError, match="userinfo"):
        _validate_cli_custom_base(
            "custom_openai", "http://user:password@127.0.0.1:8000/v1"
        )
    with pytest.raises(ValueError, match="fragments"):
        _validate_cli_custom_base("custom_openai", "http://127.0.0.1:8000/v1#token")


def test_fixed_presets_ignore_custom_base_policy() -> None:
    _validate_cli_custom_base("deepseek", "https://api.deepseek.com")
