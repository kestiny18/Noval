import io
import json
from types import SimpleNamespace

import pytest

from desktop.sidecar.noval_sidecar.protocol import MAX_LINE_BYTES, ProtocolError, parse_request
from desktop.sidecar.noval_sidecar.server import SidecarServer
from noval import API_SCHEMA_VERSION
from noval.model_config import packaged_settings


def request(method, params=None, request_id="req-1"):
    return json.dumps({
        "protocol_version": 2,
        "kind": "request",
        "request_id": request_id,
        "method": method,
        "params": params or {},
    }).encode()


def start_runtime(server, tmp_path):
    settings = tmp_path / "settings.json"
    document = packaged_settings()
    document.update(
        {
            "sessions_dir": str(tmp_path / "sessions"),
            "logs_dir": str(tmp_path / "logs"),
            "usage_dir": str(tmp_path / "usage"),
        }
    )
    settings.write_text(json.dumps(document), encoding="utf-8")
    return server.dispatch(
        parse_request(
            request("runtime.start", {"settings_path": str(settings)})
        )
    )


def test_parse_request_rejects_invalid_and_oversized_input():
    with pytest.raises(ProtocolError, match="UTF-8 JSON"):
        parse_request(b"not-json")
    with pytest.raises(ProtocolError, match="size limit"):
        parse_request(b"x" * (MAX_LINE_BYTES + 1))


def test_hello_reports_stable_capabilities():
    parsed = parse_request(request("system.hello"))
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    result = server.dispatch(parsed)
    assert result["protocol_version"] == 2
    assert "sessions" in result["capabilities"]
    assert "transcript_history" in result["capabilities"]
    assert "model_configuration" in result["capabilities"]
    assert result["core_version"]


def test_server_returns_safe_error_without_echoing_input():
    secret = "FAKE_SECRET_MUST_NOT_ECHO"
    output = io.BytesIO()
    SidecarServer(io.BytesIO((secret + "\n").encode()), output).serve()
    value = json.loads(output.getvalue())
    assert value["error"]["code"] == "invalid_json"
    assert secret not in output.getvalue().decode()


def test_runtime_start_preserves_typed_settings_schema_failure(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"schema_version": 1, "model": "legacy"}),
        encoding="utf-8",
    )
    output = io.BytesIO()
    SidecarServer(
        io.BytesIO(
            request(
                "runtime.start",
                {"settings_path": str(settings)},
            )
            + b"\n"
        ),
        output,
    ).serve()

    value = json.loads(output.getvalue())
    assert value["ok"] is False
    assert value["error"]["code"] == "unsupported_settings_schema"
    assert str(settings) in value["error"]["safe_message"]


def test_workspace_must_be_selected_before_listing(tmp_path):
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    start_runtime(server, tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        server.dispatch(parse_request(request("session.list")))
    selected = server.dispatch(parse_request(request("workspace.select", {"workdir": str(tmp_path)})))
    assert selected["workdir"] == str(tmp_path.resolve())
    assert server.dispatch(parse_request(request("session.list"))) == {"sessions": []}
    server.close()


def test_runtime_exposes_safe_configuration_and_project_inventory(tmp_path):
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    start_runtime(server, tmp_path)

    configuration = server.dispatch(parse_request(request("runtime.configuration")))
    projects = server.dispatch(parse_request(request("workspace.list")))

    assert configuration["models"]["default_model_id"]
    assert configuration["models"]["connections"][0]["adapter"] == (
        "openai-compatible"
    )
    assert isinstance(
        configuration["models"]["connections"][0]["credential_available"],
        bool,
    )
    assert "api_key" not in configuration
    assert isinstance(projects["projects"], list)
    server.close()


def test_model_configuration_mutations_never_return_credentials(tmp_path):
    secret = "FAKE_SIDECAR_SECRET_MUST_NOT_RETURN"
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    start_runtime(server, tmp_path)

    profiles = server.dispatch(parse_request(request("model.profiles")))
    configuration = server.dispatch(
        parse_request(request("model.configuration"))
    )
    revision = configuration["revision"]
    updated = server.dispatch(parse_request(request(
        "model.connection.upsert",
        {
            "schema_version": API_SCHEMA_VERSION,
            "expected_configuration_revision": revision,
            "label": "Local gateway",
            "profile_id": "custom",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key_env": "LOCAL_GATEWAY_KEY",
            "api_key": secret,
        },
    )))

    encoded = json.dumps(updated)
    assert len(
        [profile for profile in profiles["profiles"] if profile["kind"] == "builtin"]
    ) == 6
    assert profiles["profiles"][-1]["kind"] == "custom"
    assert secret not in encoded
    assert '"api_key":' not in encoded
    assert updated["connections"][-1]["credential_available"] is True
    settings_text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert secret in settings_text
    server.close()


def test_session_model_selection_can_derive_the_runtime_judge(tmp_path):
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    start_runtime(server, tmp_path)
    server.dispatch(parse_request(request(
        "workspace.select", {"workdir": str(tmp_path)}
    )))
    created = server.dispatch(parse_request(request("session.create")))
    session_id = created["session"]["session_id"]

    selected = server.dispatch(parse_request(request(
        "session.models.select",
        {
            "session_id": session_id,
            "selected_model_id": "model-deepseek-v4-pro-default",
        },
    )))

    assert selected["selection"]["selected_model_id"] == (
        "model-deepseek-v4-pro-default"
    )
    assert selected["selection"]["selected_judge_model_id"] == (
        "model-deepseek-v4-flash-default"
    )
    server.close()


def test_workspace_sessions_lists_without_changing_active_workspace(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    start_runtime(server, tmp_path)
    server.dispatch(parse_request(request("workspace.select", {"workdir": str(first)})))
    assert server.dispatch(parse_request(request("workspace.sessions", {"workdir": str(second)}))) == {"sessions": []}
    assert server.dispatch(parse_request(request("session.list"))) == {"sessions": []}
    assert server._workspace == first.resolve()
    server.close()


def test_resume_is_idempotent_for_a_session_already_open_in_the_sidecar(tmp_path):
    info = SimpleNamespace(to_dict=lambda: {"session_id": "open-session"})
    permissions = SimpleNamespace(to_dict=lambda: {"mode": "ask", "approved_tools": []})
    session = SimpleNamespace(info=info, permission_state=lambda: permissions)

    class Runtime:
        def get_session(self, session_id):
            assert session_id == "open-session"
            return session

        def resume_session(self, *args, **kwargs):
            raise AssertionError("an already-open Session must not be reopened")

    server = SidecarServer(io.BytesIO(), io.BytesIO())
    server._runtime = Runtime()
    server._workspace = tmp_path

    result = server.dispatch(parse_request(request(
        "session.resume", {"session_id": "open-session"}
    )))

    assert result["session"]["session_id"] == "open-session"


def test_transcript_history_forwards_the_exclusive_cursor(tmp_path):
    page = SimpleNamespace(to_dict=lambda: {
        "schema_version": 1,
        "entries": [],
        "previous_sequence": 25,
        "has_more": True,
    })

    class Session:
        def transcript_history(self, *, before_sequence, limit):
            assert before_sequence == 25
            assert limit == 24
            return page

    class Runtime:
        def get_session(self, session_id):
            assert session_id == "open-session"
            return Session()

    server = SidecarServer(io.BytesIO(), io.BytesIO())
    server._runtime = Runtime()
    server._workspace = tmp_path

    result = server.dispatch(parse_request(request(
        "session.transcript_history",
        {"session_id": "open-session", "before_sequence": 25, "limit": 24},
    )))

    assert result["previous_sequence"] == 25
    assert result["has_more"] is True


def test_configuration_exit_is_returned_as_safe_error(monkeypatch):
    output = io.BytesIO()
    server = SidecarServer(io.BytesIO(request("runtime.start") + b"\n"), output)
    monkeypatch.setattr(server, "_runtime_start", lambda _params: (_ for _ in ()).throw(SystemExit("Configuration is missing.")))
    server.serve()
    value = json.loads(output.getvalue())
    assert value["error"]["code"] == "configuration_error"
    assert value["error"]["safe_message"] == "Configuration is missing."
