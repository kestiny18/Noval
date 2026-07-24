"""Opt-in real Adapter contract for every shipped OpenAI-compatible model."""
from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from noval.client import (
    OPENAI_ADAPTER,
    OpenAICompatibleClient,
    ToolDefinition,
)
from noval.messages import ReplayScope, system_message, user_message
from noval.model_config import BUILTIN_PROFILES


LIVE_ENABLED = os.environ.get(
    "NOVAL_RUN_LIVE_PROVIDER_CONTRACT",
    "",
).strip() == "1"
PROFILE_MODELS = tuple(
    (profile, model)
    for profile in BUILTIN_PROFILES
    for model in profile.models
)

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="set NOVAL_RUN_LIVE_PROVIDER_CONTRACT=1 to call real Providers",
)


@pytest.mark.parametrize(
    ("profile", "provider_model"),
    PROFILE_MODELS,
    ids=[
        f"{profile.id}:{model.id}"
        for profile, model in PROFILE_MODELS
    ],
)
def test_shipped_model_passes_openai_compatible_contract(
    profile,
    provider_model,
):
    api_key = os.environ.get(profile.api_key_env, "").strip()
    assert api_key, (
        f"{profile.api_key_env} is required when the live Provider "
        f"contract is enabled"
    )
    scope = ReplayScope(
        adapter=OPENAI_ADAPTER,
        connection_id=f"live-{profile.id}",
        configured_model_id=f"live-{profile.id}-{provider_model.id}",
        provider_model=provider_model.id,
        transport_revision=1,
        adapter_schema_version=1,
        credential_epoch="live-contract",
    )
    client = OpenAICompatibleClient(
        profile.base_url,
        api_key,
        provider_model.id,
        timeout=60,
        max_retries=0,
        replay_scope=scope,
    )
    marker = "noval-" + uuid4().hex[:12]
    try:
        completed = client.complete(
            [
                system_message(
                    "Return only the exact marker supplied by the user."
                ),
                user_message(marker),
            ],
            [],
        )
        assert marker in completed.message.text
        assert completed.provider.adapter == OPENAI_ADAPTER

        deltas = []
        streamed = client.stream_complete(
            [
                system_message(
                    "Return only the exact marker supplied by the user."
                ),
                user_message(marker),
            ],
            [],
            deltas.append,
        )
        visible = "".join(
            event.text or ""
            for event in deltas
            if event.type == "text.delta"
        )
        assert marker in visible
        assert streamed.message.text == visible

        tool_response = client.complete(
            [
                system_message(
                    "You must call contract_echo exactly once with the exact "
                    "marker supplied by the user. Do not answer with text."
                ),
                user_message(marker),
            ],
            [
                ToolDefinition(
                    name="contract_echo",
                    description="Return the supplied contract marker.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "marker": {"type": "string"},
                        },
                        "required": ["marker"],
                        "additionalProperties": False,
                    },
                )
            ],
        )
        assert len(tool_response.message.tool_calls) == 1
        call = tool_response.message.tool_calls[0]
        assert call.name == "contract_echo"
        assert json.loads(call.arguments) == {"marker": marker}
    finally:
        transport = getattr(client, "_client", None)
        close = getattr(transport, "close", None)
        if callable(close):
            close()
