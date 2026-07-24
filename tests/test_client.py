import sys
from types import SimpleNamespace

import pytest

from noval.client import (
    ANTHROPIC_ADAPTER,
    OPENAI_ADAPTER,
    AnthropicMessagesClient,
    OpenAICompatibleClient,
    ProviderError,
    ProviderErrorKind,
    ProviderIdentity,
    ToolDefinition,
)
from noval.messages import (
    AdapterReplayState,
    ReplayScope,
    ToolCallBlock,
    assistant_message,
    tool_result_message,
    user_message,
)
from noval.session import JsonlSessionStore


class FakeCreate:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def openai_client(responses):
    create = FakeCreate(responses)
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=create))
    client.model = "deepseek-v4-pro"
    client.identity = ProviderIdentity(OPENAI_ADAPTER, client.model, OPENAI_ADAPTER)
    return client, create


def anthropic_client(responses):
    create = FakeCreate(responses)
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._client = SimpleNamespace(messages=create)
    client.model = "claude-test"
    client.max_tokens = 4096
    client.identity = ProviderIdentity("anthropic", client.model, ANTHROPIC_ADAPTER)
    return client, create


def openai_response(*, content=None, reasoning=None, calls=(), usage=True, model="provider-model"):
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=list(calls),
        annotations=["must not leak"],
    )
    raw_usage = None
    if usage:
        raw_usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_cache_hit_tokens=60,
            prompt_cache_miss_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
        )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=raw_usage, model=model,
    )


def openai_call(call_id="call-1", name="read_file", arguments='{"path":"a"}'):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def openai_chunk(
    *,
    content=None,
    reasoning=None,
    calls=(),
    usage=None,
    model="provider-stream-model",
):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=list(calls),
    )
    choices = [SimpleNamespace(delta=delta)] if any(
        value is not None and value != ()
        for value in (content, reasoning, calls)
    ) else []
    return SimpleNamespace(
        choices=choices,
        usage=usage,
        model=model,
    )


def openai_call_delta(index, *, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def anthropic_response(content, *, model="claude-provider"):
    return SimpleNamespace(
        content=content,
        model=model,
        usage=SimpleNamespace(
            input_tokens=80,
            output_tokens=15,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=30,
        ),
    )


class FakeAnthropicStream:
    def __init__(self, text_stream, response):
        self.text_stream = text_stream
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def get_final_message(self):
        return self.response


class FakeAnthropicMessages:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        result = self.streams.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_response_has_one_canonical_message_and_no_raw_sdk_object():
    client, _ = openai_client([openai_response(content="answer")])

    response = client.complete([user_message("question")], [])

    assert response.message.text == "answer"
    assert response.message.provenance.model == "provider-model"
    assert response.provider.model == "provider-model"
    assert not hasattr(response, "raw")
    assert not hasattr(response, "content")


def test_openai_client_sets_timeout_and_retries(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    client = OpenAICompatibleClient(
        "https://example.invalid", "key", "model-x", timeout=45.5, max_retries=0,
    )

    assert client.model == "model-x"
    assert seen == {
        "base_url": "https://example.invalid",
        "api_key": "key",
        "timeout": 45.5,
        "max_retries": 0,
    }


def test_openai_loopback_client_bypasses_the_system_proxy(monkeypatch):
    openai_options = {}
    httpx_options = {}
    local_http_client = object()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            openai_options.update(kwargs)

    class FakeHttpxClient:
        def __new__(cls, **kwargs):
            httpx_options.update(kwargs)
            return local_http_client

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=FakeOpenAI),
    )
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(Client=FakeHttpxClient),
    )

    OpenAICompatibleClient(
        "http://127.0.0.1:8080/v1",
        "key",
        "local-model",
        timeout=7,
    )

    assert httpx_options == {"timeout": 7, "trust_env": False}
    assert openai_options["http_client"] is local_http_client


def test_plain_reasoning_is_not_retained_but_usage_is_normalized():
    client, _ = openai_client([
        openai_response(content="answer", reasoning="private final reasoning"),
    ])

    response = client.complete([user_message("question")], [])

    assert response.message.replay_state is None
    assert response.meta["thinking_enabled"] is True
    assert response.usage.prompt_tokens == 100
    assert response.usage.reasoning_tokens == 12


def test_openai_stream_emits_only_visible_text_and_reconstructs_final_response():
    raw_usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=2),
    )
    client, create = openai_client([[
        openai_chunk(reasoning="need "),
        openai_chunk(
            content="Inspect",
            calls=(openai_call_delta(
                0,
                call_id="call-1",
                name="read_file",
                arguments='{"pa',
            ),),
        ),
        openai_chunk(
            content="ing",
            reasoning="the file",
            calls=(openai_call_delta(0, arguments='th":"a"}'),),
        ),
        openai_chunk(usage=raw_usage),
    ]])
    deltas = []

    response = client.stream_complete(
        [user_message("read")],
        [ToolDefinition("read_file", "read", {"type": "object"})],
        deltas.append,
    )

    assert [event.text for event in deltas] == ["Inspect", "ing"]
    assert response.message.text == "Inspecting"
    assert response.message.tool_calls == (
        ToolCallBlock("call-1", "read_file", '{"path":"a"}'),
    )
    assert response.message.replay_state.payload == {
        "reasoning_content": "need the file",
    }
    assert response.provider.model == "provider-stream-model"
    assert response.usage.total_tokens == 15
    assert response.usage.reasoning_tokens == 2
    assert create.calls[0]["stream"] is True
    assert "stream_options" not in create.calls[0]
    assert create.calls[0]["tool_choice"] == "auto"


def test_openai_stream_normalizes_iteration_failures_after_visible_output():
    class RateLimitError(Exception):
        status_code = 429

    class BrokenStream:
        def __iter__(self):
            yield openai_chunk(content="partial")
            raise RateLimitError("secret response body")

    client, _ = openai_client([BrokenStream()])
    deltas = []

    with pytest.raises(ProviderError) as caught:
        client.stream_complete(
            [user_message("question")], [], deltas.append
        )

    assert [event.text for event in deltas] == ["partial"]
    assert caught.value.kind is ProviderErrorKind.RATE_LIMIT
    assert "secret response body" not in caught.value.safe_message


def test_openai_tool_reasoning_round_trips_as_adapter_owned_state():
    client, create = openai_client([
        openai_response(calls=[openai_call()], reasoning="need the file"),
        openai_response(content="done"),
    ])
    first = client.complete([user_message("read")], [])
    assert first.message.replay_state.adapter == OPENAI_ADAPTER
    assert first.message.replay_state.payload == {"reasoning_content": "need the file"}

    history = [
        user_message("read"),
        first.message,
        tool_result_message("call-1", "A"),
    ]
    client.complete(history, [])

    replayed = create.calls[1]["messages"][1]
    assert replayed["reasoning_content"] == "need the file"
    assert replayed["tool_calls"][0]["function"]["name"] == "read_file"


@pytest.mark.parametrize(
    "other_scope",
    [
        ReplayScope(
            OPENAI_ADAPTER,
            "connection-b",
            "configured-a",
            "model-a",
            1,
            1,
        ),
        ReplayScope(
            OPENAI_ADAPTER,
            "connection-a",
            "configured-b",
            "model-a",
            1,
            1,
        ),
        ReplayScope(
            OPENAI_ADAPTER,
            "connection-a",
            "configured-a",
            "model-b",
            1,
            1,
        ),
        ReplayScope(
            OPENAI_ADAPTER,
            "connection-a",
            "configured-a",
            "model-a",
            2,
            1,
        ),
        ReplayScope(
            OPENAI_ADAPTER,
            "connection-a",
            "configured-a",
            "model-a",
            1,
            2,
        ),
    ],
)
def test_openai_rejects_same_adapter_replay_from_another_scope(other_scope):
    current_scope = ReplayScope(
        OPENAI_ADAPTER,
        "connection-a",
        "configured-a",
        "model-a",
        1,
        1,
    )
    client, create = openai_client([openai_response(content="unused")])
    client.replay_scope = current_scope
    replayed = assistant_message(
        tool_calls=(ToolCallBlock("call-1", "read_file", "{}"),),
        replay_state=AdapterReplayState(
            OPENAI_ADAPTER,
            1,
            {"reasoning_content": "private"},
            other_scope,
        ),
    )

    with pytest.raises(ProviderError, match="another model scope") as raised:
        client.complete(
            [user_message("read"), replayed, tool_result_message("call-1", "A")],
            [],
        )

    assert raised.value.kind is ProviderErrorKind.PROTOCOL
    assert create.calls == []


def test_replay_scope_round_trips_with_opaque_payload():
    scope = ReplayScope(
        OPENAI_ADAPTER,
        "connection-a",
        "configured-a",
        "model-a",
        7,
        1,
    )
    original = AdapterReplayState(
        OPENAI_ADAPTER,
        1,
        {"reasoning_content": "private"},
        scope,
    )

    restored = AdapterReplayState.from_dict(original.to_dict())

    assert restored == original
    assert restored.scope == scope


def test_anthropic_thinking_round_trips_without_entering_semantic_view():
    thinking = {"type": "thinking", "thinking": "private", "signature": "sig"}
    client, create = anthropic_client([
        anthropic_response([
            thinking,
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a"}},
        ]),
        anthropic_response([{"type": "text", "text": "done"}]),
    ])
    first = client.complete([user_message("read")], [])

    assert first.message.replay_state.payload == {"blocks": [thinking]}
    assert "replay_state" not in first.message.semantic_dict()
    client.complete([
        user_message("read"), first.message, tool_result_message("call-1", "A"),
    ], [])

    replayed = create.calls[1]["messages"][1]["content"]
    assert replayed[0] == thinking
    assert replayed[1]["type"] == "tool_use"


def test_anthropic_rejects_replay_from_another_connection_scope():
    client, create = anthropic_client(
        [anthropic_response([{"type": "text", "text": "unused"}])]
    )
    client.replay_scope = ReplayScope(
        ANTHROPIC_ADAPTER,
        "connection-a",
        "configured-a",
        "claude-test",
        1,
        1,
    )
    replayed = assistant_message(
        tool_calls=(ToolCallBlock("call-1", "read_file", "{}"),),
        replay_state=AdapterReplayState(
            ANTHROPIC_ADAPTER,
            1,
            {
                "blocks": [
                    {
                        "type": "thinking",
                        "thinking": "private",
                        "signature": "sig",
                    }
                ]
            },
            ReplayScope(
                ANTHROPIC_ADAPTER,
                "connection-b",
                "configured-a",
                "claude-test",
                1,
                1,
            ),
        ),
    )

    with pytest.raises(ProviderError, match="another model scope"):
        client.complete(
            [user_message("read"), replayed, tool_result_message("call-1", "A")],
            [],
        )

    assert create.calls == []


def test_anthropic_stream_emits_visible_text_and_keeps_thinking_opaque():
    thinking = {"type": "thinking", "thinking": "private", "signature": "sig"}
    final = anthropic_response([
        thinking,
        {"type": "text", "text": "Inspecting"},
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "read_file",
            "input": {"path": "a"},
        },
    ])
    messages = FakeAnthropicMessages([
        FakeAnthropicStream(iter(["Inspect", "ing"]), final),
    ])
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._client = SimpleNamespace(messages=messages)
    client.model = "claude-test"
    client.max_tokens = 4096
    client.identity = ProviderIdentity("anthropic", client.model, ANTHROPIC_ADAPTER)
    deltas = []

    response = client.stream_complete(
        [user_message("read")],
        [ToolDefinition("read_file", "read", {"type": "object"})],
        deltas.append,
    )

    assert [event.text for event in deltas] == ["Inspect", "ing"]
    assert response.message.text == "Inspecting"
    assert response.message.tool_calls == (
        ToolCallBlock("call-1", "read_file", '{"path":"a"}'),
    )
    assert response.message.replay_state.payload == {"blocks": [thinking]}
    assert response.provider.model == "claude-provider"
    assert response.usage.total_tokens == 95
    assert messages.calls[0]["tools"][0]["name"] == "read_file"
    assert all("private" not in event.text for event in deltas)


def test_anthropic_stream_normalizes_iteration_failures_after_visible_output():
    class ServerError(Exception):
        status_code = 500

    def broken_text_stream():
        yield "partial"
        raise ServerError("secret response body")

    messages = FakeAnthropicMessages([
        FakeAnthropicStream(
            broken_text_stream(),
            anthropic_response([{"type": "text", "text": "unused"}]),
        ),
    ])
    client = AnthropicMessagesClient.__new__(AnthropicMessagesClient)
    client._client = SimpleNamespace(messages=messages)
    client.model = "claude-test"
    client.max_tokens = 4096
    client.identity = ProviderIdentity("anthropic", client.model, ANTHROPIC_ADAPTER)
    deltas = []

    with pytest.raises(ProviderError) as caught:
        client.stream_complete([user_message("question")], [], deltas.append)

    assert [event.text for event in deltas] == ["partial"]
    assert caught.value.kind is ProviderErrorKind.SERVER
    assert caught.value.retryable is True
    assert "secret response body" not in caught.value.safe_message


def test_two_adapters_produce_equivalent_canonical_tool_transcript():
    openai, _ = openai_client([
        openai_response(content="inspect", calls=[openai_call()]),
    ])
    anthropic, _ = anthropic_client([
        anthropic_response([
            {"type": "text", "text": "inspect"},
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a"}},
        ]),
    ])

    left = openai.complete([user_message("read")], []).message.semantic_dict()
    right = anthropic.complete([user_message("read")], []).message.semantic_dict()

    assert left == right


def test_anthropic_encodes_tool_errors_and_tool_definitions():
    client, create = anthropic_client([
        anthropic_response([{"type": "text", "text": "handled"}]),
    ])
    tool = ToolDefinition("read_file", "read", {"type": "object"})

    client.complete([tool_result_message("call-1", "failed", is_error=True)], [tool])

    assert create.calls[0]["messages"][0]["content"][0]["is_error"] is True
    assert create.calls[0]["tools"] == [{
        "name": "read_file", "description": "read", "input_schema": {"type": "object"},
    }]


def test_provider_errors_are_safe_normalized_and_retryable():
    class RateLimitError(Exception):
        status_code = 429

    client, _ = openai_client([RateLimitError("secret provider body")])

    with pytest.raises(ProviderError) as caught:
        client.complete([user_message("question")], [])

    assert caught.value.kind is ProviderErrorKind.RATE_LIMIT
    assert caught.value.retryable is True
    assert "secret provider body" not in caught.value.safe_message


def test_foreign_replay_state_is_ignored_by_other_adapter():
    client, create = openai_client([openai_response(content="ok")])
    foreign = assistant_message(
        tool_calls=(ToolCallBlock("c1", "read_file", "{}"),),
        replay_state=AdapterReplayState(
            ANTHROPIC_ADAPTER, 1, {"blocks": [{"type": "thinking", "thinking": "x"}]},
        ),
    )

    client.complete([user_message("read"), foreign, tool_result_message("c1", "A")], [])

    replayed = create.calls[0]["messages"][1]
    assert "reasoning_content" not in replayed


def test_anthropic_rejects_unrepresentable_raw_tool_arguments():
    client, _ = anthropic_client([])
    invalid = assistant_message(tool_calls=(
        ToolCallBlock("c1", "read_file", "not-json"),
    ))

    with pytest.raises(ProviderError) as caught:
        client.complete([invalid], [])

    assert caught.value.kind is ProviderErrorKind.PROTOCOL
    assert caught.value.retryable is False


def test_openai_replay_survives_session_v3_recovery(tmp_path):
    client, _ = openai_client([
        openai_response(calls=[openai_call()], reasoning="exact deepseek state"),
    ])
    first = client.complete([user_message("read")], []).message
    assert first.replay_state.scope == ReplayScope(
        OPENAI_ADAPTER,
        OPENAI_ADAPTER,
        client.model,
        client.model,
        1,
        1,
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = JsonlSessionStore.create(tmp_path / "sessions", workdir, "model")
    for message in (user_message("read"), first, tool_result_message("call-1", "A")):
        store.append(message)
    store.close()

    recovered = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model",
    ).load()
    resumed, create = openai_client([openai_response(content="done")])
    resumed.complete(recovered, [])

    assert create.calls[0]["messages"][1]["reasoning_content"] == "exact deepseek state"


def test_anthropic_replay_survives_session_v3_recovery(tmp_path):
    thinking = {"type": "thinking", "thinking": "private", "signature": "exact-sig"}
    client, _ = anthropic_client([
        anthropic_response([
            thinking,
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
        ]),
    ])
    first = client.complete([user_message("read")], []).message
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = JsonlSessionStore.create(tmp_path / "sessions", workdir, "model")
    for message in (user_message("read"), first, tool_result_message("call-1", "A")):
        store.append(message)
    store.close()

    recovered = JsonlSessionStore.open(
        store.base_dir, store.workdir, store.session_id, "model",
    ).load()
    resumed, create = anthropic_client([
        anthropic_response([{"type": "text", "text": "done"}]),
    ])
    resumed.complete(recovered, [])

    assert create.calls[0]["messages"][1]["content"][0] == thinking
