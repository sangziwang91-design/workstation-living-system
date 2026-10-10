"""Personal WLS context must not silently leave the local planner boundary."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from wls.planner import OpenAICompatibleProvider


def remote(**extra: object) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider({
        "base_url": "https://model.example.invalid/v1",
        "model": "frozen-test",
        "api_key_env": "WLS_OWNER_CONTEXT_TEST_DOES_NOT_EXIST",
        **extra,
    })


@pytest.mark.parametrize("context", [
    {"memories": [{"memory_type": "owner_context", "content": {"text": "PRIVATE RECORD"}}]},
    {"goals": [{"source": "owner_context", "description": "PRIVATE RECORD"}]},
    {"workspace": [{"payload": {"memory": {
        "memory_type": "owner_context", "content": {"text": "PRIVATE RECORD"}
    }}}]},
    {"memory_retrieval": {"selected": [{
        "source_ids": ["owner_context:owner-record"], "content": {"text": "PRIVATE RECORD"}
    }]}},
])
def test_remote_planner_rejects_any_nested_private_owner_context_before_io(
    context: dict,
) -> None:
    with patch("wls.planner.Request", side_effect=AssertionError("NETWORK REACHED")):
        with pytest.raises(PermissionError, match="explicit remote disclosure"):
            remote().create_plan(context)


def test_explicit_boolean_opt_in_is_required_not_truthy_string(
) -> None:
    context = {"memories": [{"memory_type": "owner_context", "content": "PRIVATE"}]}
    with pytest.raises(PermissionError):
        remote(allow_owner_context_remote="true").create_plan(context)
    with pytest.raises(RuntimeError, match="missing API key"):
        remote(allow_owner_context_remote=True).create_plan(context)


def test_loopback_model_does_not_trigger_external_owner_context_denial() -> None:
    local = OpenAICompatibleProvider({
        "base_url": "http://127.0.0.1:7890/v1",
        "model": "local-only-test",
        "api_key_env": "WLS_OWNER_CONTEXT_TEST_DOES_NOT_EXIST",
    })
    with pytest.raises(RuntimeError, match="missing API key"):
        local.create_plan({"memories": [{"memory_type": "owner_context"}]})


def test_unrelated_context_keeps_existing_provider_behavior() -> None:
    with pytest.raises(RuntimeError, match="missing API key"):
        remote().create_plan({"memories": [{"memory_type": "episodic"}]})


def test_cannot_hide_owner_context_in_nested_tuple_or_source_identifier() -> None:
    provider = remote()
    assert provider._has_owner_context(({"source": "owner_context"},)) is True
    assert provider._has_owner_context({"links": ["owner_context:method-a"]}) is True
    assert provider._has_owner_context({"links": ["public:source-a"]}) is False
