import json
from pathlib import Path

from noval.api import (
    EventType,
    RequestInspection,
    SessionOptions,
    SessionPersistence,
    TurnRequest,
)
from noval.application import NovalRuntime
from noval.client import (
    ANTHROPIC_ADAPTER,
    OPENAI_ADAPTER,
    AnthropicMessagesClient,
    MockClient,
    OpenAICompatibleClient,
    ProviderIdentity,
    ToolDefinition,
    mock_text,
)
from noval.config import Config
from noval.messages import AdapterReplayState, assistant_message, user_message
from noval.requests import (
    InMemoryRequestJournal,
    RequestContext,
    RequestRecordingClient,
    RequestSequence,
    current_request_id,
)


def request_config(tmp_path: Path) -> Config:
    return Config(
        model="agent-model",
        judge_model="judge-model",
        base_url="https://example.invalid",
        api_key_env="NOVAL_TEST_KEY",
        api_key="runtime-secret-key",
        max_steps=4,
        max_tool_output_chars=2000,
        persist_sessions=True,
        sessions_dir_setting=str(tmp_path / "sessions"),
        persist_logs=False,
        persist_usage=False,
        context_budget_tokens=256000,
        provider="openai-compatible",
    )


class RequestClientFactory:
    def __init__(self, replies):
        self.replies = iter(replies)

    def __call__(self, spec):
        if spec.purpose == "agent":
            return MockClient([mock_text(next(self.replies))])
        return MockClient([])


def test_request_recording_client_records_semantic_input_and_request_id():
    journal = InMemoryRequestJournal()
    observed_request_ids = []

    class ObservingClient(MockClient):
        def complete(self, messages, tools):
            observed_request_ids.append(current_request_id())
            return super().complete(messages, tools)

        def render_request(self, messages, tools):
            return {
                "messages": [{"content": "password=FAKE_ADAPTER_PASSWORD"}],
                "privateKey": "FAKE_ADAPTER_PRIVATE_KEY",
            }

    client = RequestRecordingClient(
        ObservingClient([mock_text("done")]),
        journal,
        lambda: RequestContext("session-1", "turn-1"),
        purpose="agent",
        identity=ProviderIdentity("mock", "model", "mock"),
    )
    tools = [ToolDefinition(
        "read_file",
        "read",
        {
            "type": "object",
            "properties": {"password": {"type": "string"}},
        },
    )]

    response = client.complete_with_request(
        [user_message("password=FAKE_USER_PASSWORD")],
        tools,
        request_id="request-1",
    )
    inspection = journal.get("request-1")

    assert response.meta["request_id"] == "request-1"
    assert observed_request_ids == ["request-1"]
    assert current_request_id() is None
    assert inspection is not None
    assert inspection.turn_id == "turn-1"
    assert inspection.step == 1
    assert inspection.canonical_messages[0]["role"] == "user"
    assert inspection.tools[0]["name"] == "read_file"
    assert inspection.tools[0]["input_schema"]["properties"] == {
        "password": {"type": "string"},
    }
    encoded = json.dumps(inspection.to_dict(), ensure_ascii=False)
    assert "FAKE_USER_PASSWORD" not in encoded
    assert "FAKE_ADAPTER_PASSWORD" not in encoded
    assert "FAKE_ADAPTER_PRIVATE_KEY" not in encoded
    assert "<redacted>" in encoded
    assert RequestInspection.from_dict(inspection.to_dict()) == inspection


def test_adapter_inspection_payloads_exclude_credentials_and_opaque_thinking():
    openai = object.__new__(OpenAICompatibleClient)
    openai.model = "openai-model"
    openai_message = assistant_message(
        "visible",
        replay_state=AdapterReplayState(
            OPENAI_ADAPTER,
            1,
            {"reasoning_content": "hidden chain of thought"},
        ),
    )
    openai_payload = openai.render_request([openai_message], [])

    anthropic = object.__new__(AnthropicMessagesClient)
    anthropic.model = "anthropic-model"
    anthropic.max_tokens = 100
    anthropic_message = assistant_message(
        "visible",
        replay_state=AdapterReplayState(
            ANTHROPIC_ADAPTER,
            1,
            {"blocks": [{"type": "thinking", "thinking": "hidden thought"}]},
        ),
    )
    anthropic_payload = anthropic.render_request([anthropic_message], [])
    encoded = json.dumps(
        {"openai": openai_payload, "anthropic": anthropic_payload}
    )

    assert "hidden chain of thought" not in encoded
    assert "hidden thought" not in encoded
    assert "api_key" not in encoded
    assert "authorization" not in encoded.lower()


def test_request_steps_are_shared_across_agent_and_judge_clients():
    journal = InMemoryRequestJournal()
    sequence = RequestSequence()
    context = lambda: RequestContext("session-1", "turn-1")
    identity = ProviderIdentity("mock", "model", "mock")
    agent = RequestRecordingClient(
        MockClient([mock_text("agent")]),
        journal,
        context,
        purpose="agent",
        identity=identity,
        sequence=sequence,
    )
    judge = RequestRecordingClient(
        MockClient([mock_text("judge")]),
        journal,
        context,
        purpose="completion_judge",
        identity=identity,
        sequence=sequence,
    )

    agent.complete_with_request([], [], request_id="request-agent")
    judge.complete_with_request([], [], request_id="request-judge")

    assert journal.get("request-agent").step == 1
    assert journal.get("request-judge").step == 2


def test_persistent_request_can_be_inspected_after_session_resume(tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    config = request_config(tmp_path)
    events = []

    with NovalRuntime(
        config,
        client_factory=RequestClientFactory(["first reply"]),
        event_sink=events.append,
    ) as runtime:
        session = runtime.create_session(SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        session_id = session.info.session_id
        session.run_turn(TurnRequest(
            "record this request\npassword=FAKE_PERSISTED_PASSWORD"
        ))
        started = next(
            event for event in events
            if event.type == EventType.MODEL_STARTED.value
        )
        request_id = started.payload["request_id"]
        inspection = session.inspect_request(request_id)

        assert inspection.session_id == session_id
        assert inspection.turn_id == started.turn_id
        assert inspection.purpose == "agent"
        assert "record this request" in json.dumps(
            inspection.canonical_messages, ensure_ascii=False
        )
        assert "FAKE_PERSISTED_PASSWORD" not in json.dumps(
            inspection.to_dict(), ensure_ascii=False
        )
        assert "runtime-secret-key" not in json.dumps(
            inspection.to_dict(), ensure_ascii=False
        )
        request_path = session._store.request_path()
        assert "FAKE_PERSISTED_PASSWORD" not in request_path.read_text(
            encoding="utf-8"
        )

    with NovalRuntime(
        config,
        client_factory=RequestClientFactory(["unused"]),
    ) as runtime:
        resumed = runtime.resume_session(session_id, SessionOptions(
            workdir=str(workdir),
            persistence=SessionPersistence.PERSISTENT,
        ))
        restored = resumed.inspect_request(request_id)

    assert restored == inspection
