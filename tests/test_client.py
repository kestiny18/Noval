from types import SimpleNamespace

from noval.client import LLMResponse, OpenAICompatibleClient


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _client(responses):
    completions = _FakeCompletions(responses)
    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client.model = "deepseek-v4-pro"
    return client, completions


def _response(*, content, reasoning_content, tool_calls=None, reasoning_tokens=0):
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        annotations=["must not leak"],
    )
    usage = SimpleNamespace(
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens)
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_llm_response_keeps_raw_as_fourth_positional_argument():
    response = LLMResponse(None, [], {"role": "assistant", "content": None}, "raw")
    assert response.raw == "raw"
    assert response.meta == {}


def test_reasoning_is_omitted_for_plain_assistant_message():
    client, _ = _client([_response(
        content="answer",
        reasoning_content="private reasoning",
        reasoning_tokens=12,
    )])

    result = client.complete([{"role": "user", "content": "question"}], [])

    assert result.assistant_message == {"role": "assistant", "content": "answer"}
    assert result.meta["thinking_enabled"] is True
    assert result.meta["reasoning_tokens"] == 12
    assert result.meta["duration_ms"] >= 0


def test_tool_call_reasoning_is_preserved_once_and_replayed():
    tool_calls = [
        _tool_call("call-1", "read_file", '{"path":"a"}'),
        _tool_call("call-2", "read_file", '{"path":"b"}'),
    ]
    client, completions = _client([
        _response(
            content=None,
            reasoning_content="need both files",
            tool_calls=tool_calls,
            reasoning_tokens=24,
        ),
        _response(content="done", reasoning_content="final reasoning", reasoning_tokens=8),
    ])

    first = client.complete([{"role": "user", "content": "read both"}], [])
    assert len(first.tool_calls) == 2
    assert first.assistant_message["reasoning_content"] == "need both files"
    assert "annotations" not in first.assistant_message

    history = [
        {"role": "user", "content": "read both"},
        first.assistant_message,
        {"role": "tool", "tool_call_id": "call-1", "content": "A"},
        {"role": "tool", "tool_call_id": "call-2", "content": "B"},
    ]
    client.complete(history, [])

    replayed = completions.calls[1]["messages"][1]
    assert replayed["reasoning_content"] == "need both files"
