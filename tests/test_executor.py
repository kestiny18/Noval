"""Executor tests for normalized errors, truncation, approval, and Context injection."""
import json

from noval.config import Config
from noval.executor import execute_tool_call
from noval.messages import user_message
from noval.permissions import PermissionController, PermissionMode, PermissionState
from noval.session import JsonlSessionStore
from noval.tools import Context, Risk, tool

BASE = dict(
    model="m", base_url="u", api_key_env="K", max_steps=5,
    max_tool_output_chars=100,
)


def cfg(**over):
    d = dict(BASE)
    d.update(over)
    return Config(**d)


def test_unknown_tool_lists_available():
    r = execute_tool_call("nope", "{}", cfg())
    assert r.is_error and "Available tools" in r.content


def test_invalid_json_is_error(tmp_path):
    r = execute_tool_call("read_file", "{not json", cfg(), context=Context(workdir=tmp_path))
    assert r.is_error and "JSON" in r.content


def test_missing_required_param(tmp_path):
    # Schema validation precedes Context validation.
    r = execute_tool_call("read_file", "{}", cfg(), context=Context(workdir=tmp_path))
    assert r.is_error and "missing required arguments" in r.content


def test_missing_context_reported():
    # read_file reports a missing injected Context explicitly.
    r = execute_tool_call("read_file", json.dumps({"path": "x"}), cfg())
    assert r.is_error and "execution context" in r.content


def test_tool_error_surfaced(tmp_path):
    r = execute_tool_call("read_file", json.dumps({"path": "nope.txt"}),
                          cfg(), context=Context(workdir=tmp_path))
    assert r.is_error and "not found" in r.content


def test_tool_error_is_truncated():
    @tool(name="_big_error")
    def big_error() -> str:
        """big error"""
        from noval.tools import ToolError

        raise ToolError("x" * 500)

    r = execute_tool_call("_big_error", "{}", cfg(max_tool_output_chars=100))

    assert r.is_error
    assert r.truncated
    assert "omitted" in r.content
    assert r.meta["original_chars"] == 507


def test_tool_error_redaction_is_reported():
    @tool(name="_secret_error")
    def secret_error() -> str:
        """secret error"""
        from noval.tools import ToolError

        raise ToolError("password=FAKE_PASSWORD")

    r = execute_tool_call("_secret_error", "{}", cfg(max_tool_output_chars=1000))

    assert r.is_error
    assert "FAKE_PASSWORD" not in r.content
    assert "password=<redacted>" in r.content
    assert r.meta["redacted"] is True


def test_truncation():
    @tool(name="_big")
    def big() -> str:
        """big"""
        return "x" * 500

    r = execute_tool_call("_big", "{}", cfg(max_tool_output_chars=100))
    assert r.truncated and "omitted" in r.content
    assert r.meta["original_chars"] == 500


def test_tool_output_redacts_common_secret_shapes():
    @tool(name="_secrets")
    def secrets() -> str:
        """secrets"""
        return "\n".join([
            "gboat3.db.password=FAKE_DB_PASSWORD",
            "gtm.openapi.secret=FAKE_OPENAPI_SECRET",
            "gtm.gsignature.appSecret=FAKE_APP_SECRET",
            "robotUrl=https://example.invalid/webhook/send?key=FAKE_WEBHOOK_KEY",
            "normal.value=visible",
        ])

    r = execute_tool_call("_secrets", "{}", cfg(max_tool_output_chars=1000))

    assert not r.is_error
    assert r.meta["redacted"] is True
    assert "FAKE_DB_PASSWORD" not in r.content
    assert "FAKE_OPENAPI_SECRET" not in r.content
    assert "FAKE_APP_SECRET" not in r.content
    assert "FAKE_WEBHOOK_KEY" not in r.content
    assert "normal.value=visible" in r.content
    assert "<redacted>" in r.content


def test_tool_output_redaction_keeps_json_valid():
    @tool(name="_json_secret")
    def json_secret() -> str:
        """json secret"""
        return json.dumps({"password": "FAKE_DB_PASSWORD", "normal": "visible"}, ensure_ascii=False)

    r = execute_tool_call("_json_secret", "{}", cfg(max_tool_output_chars=1000))
    payload = json.loads(r.content)

    assert payload == {"password": "<redacted>", "normal": "visible"}
    assert r.meta["redacted"] is True


def test_tool_output_redaction_keeps_source_references_visible():
    @tool(name="_source_refs")
    def source_refs() -> str:
        """source refs"""
        return "\n".join([
            "const access_token: string = ...",
            "password = input()",
            "token: TokenType",
            "api_key = os.getenv(\"API_KEY\")",
            "actual_token=sk-abc123",
            "{\"token\":\"abc\",\"normal\":\"visible\"}",
        ])

    r = execute_tool_call("_source_refs", "{}", cfg(max_tool_output_chars=1000))

    assert not r.is_error
    assert "const access_token: string = ..." in r.content
    assert "password = input()" in r.content
    assert "token: TokenType" in r.content
    assert "api_key = os.getenv(\"API_KEY\")" in r.content
    assert "actual_token=<redacted>" in r.content
    assert "{\"token\":\"<redacted>\",\"normal\":\"visible\"}" in r.content
    assert r.meta["redacted"] is True


def test_internal_typeerror_not_mislabeled():
    @tool(name="_internal_te")
    def boom() -> str:
        """boom"""
        return len(None)  # type: ignore[arg-type]

    r = execute_tool_call("_internal_te", "{}", cfg())
    assert r.is_error and "signature mismatch" not in r.content and "execution failed" in r.content


def test_signature_mismatch_reported():
    @tool(name="_needs_x")
    def f(x: str) -> str:
        """f"""
        return x

    r = execute_tool_call("_needs_x", '{"x": "a", "y": "b"}', cfg())
    assert r.is_error and "do not match the tool signature" in r.content


def test_confirmation_gate():
    @tool(name="_danger", risk=Risk.DANGEROUS)
    def danger() -> str:
        """danger"""
        return "did it"

    assert execute_tool_call("_danger", "{}", cfg()).is_error                 # No approver means denied.
    r = execute_tool_call("_danger", "{}", cfg(), approver=lambda t, a: True)  # Allowed.
    assert not r.is_error and r.content == "did it"


def test_dynamic_risk_allows_readonly_bash_without_prompt(tmp_path):
    # A read-only run_bash command is downgraded to READ and needs no approver.
    import json as _j
    r = execute_tool_call("run_bash", _j.dumps({"command": "echo hi"}),
                          cfg(), context=Context(workdir=tmp_path))
    assert not r.is_error and "hi" in r.content


def test_dynamic_risk_still_blocks_mutating_bash(tmp_path):
    import json as _j
    r = execute_tool_call("run_bash", _j.dumps({"command": "rm -rf somedir"}),
                          cfg(), context=Context(workdir=tmp_path))   # No approver.
    assert r.is_error and "denied" in r.content                      # Dangerous command remains blocked.


def test_always_decision_remembered_for_session():
    calls = {"n": 0}

    def approver(t, a):
        calls["n"] += 1
        return "always"

    @tool(name="_dang_remember", risk=Risk.DANGEROUS)
    def d() -> str:
        """d"""
        return "ok"

    c = Context(workdir=__import__("pathlib").Path("."))
    r1 = execute_tool_call("_dang_remember", "{}", cfg(), approver=approver, context=c)
    r2 = execute_tool_call("_dang_remember", "{}", cfg(), approver=approver, context=c)
    assert not r1.is_error and not r2.is_error
    assert calls["n"] == 1                       # No second prompt.


def test_always_decision_persists_across_resume(tmp_path):
    @tool(name="_dang_persist", risk=Risk.DANGEROUS)
    def dangerous() -> str:
        """dangerous"""
        return "ok"

    workdir = tmp_path / "project"
    workdir.mkdir()
    store = JsonlSessionStore.create(tmp_path / "sessions", workdir, "m")
    store.append(user_message("create session"))
    permissions = PermissionController(on_change=lambda snapshot: store.update_metadata({
        "permissions": snapshot,
    }))
    context = Context(workdir=workdir, permissions=permissions)

    result = execute_tool_call(
        "_dang_persist", "{}", cfg(), approver=lambda tool, args: "always", context=context
    )
    assert not result.is_error

    store.close()
    resumed = JsonlSessionStore.open(tmp_path / "sessions", workdir, store.session_id, "m")
    restored = PermissionController(PermissionState.from_dict(
        resumed.load_metadata()["permissions"]
    ))
    assert restored.approved_tools == {"_dang_persist"}
    assert restored.requires_approval("_dang_persist", "dangerous") is False


def test_full_access_bypasses_approval():
    @tool(name="_full_access", risk=Risk.DANGEROUS)
    def dangerous() -> str:
        """dangerous"""
        return "ok"

    permissions = PermissionController()
    permissions.set_mode(PermissionMode.FULL_ACCESS)
    context = Context(workdir=__import__("pathlib").Path("."), permissions=permissions)

    result = execute_tool_call("_full_access", "{}", cfg(), context=context)
    assert not result.is_error and result.content == "ok"


def test_duration_excludes_approval_wait():
    # Execution timing begins after approval; approval wait is separate.
    slow_approve = lambda t, a: (__import__("time").sleep(0.05) or True)

    @tool(name="_dang_timing", risk=Risk.DANGEROUS)
    def d() -> str:
        """d"""
        return "ok"

    r = execute_tool_call("_dang_timing", "{}", cfg(), approver=slow_approve,
                          context=Context(workdir=__import__("pathlib").Path(".")))
    assert not r.is_error
    assert r.meta["duration_ms"] < 40            # Excludes the 50ms approval wait.
    assert r.meta.get("approval_wait_ms", 0) >= 40


def test_pre_execute_callback_runs_after_approval_before_tool():
    events = []

    @tool(name="_pre_order", risk=Risk.DANGEROUS)
    def target() -> str:
        """target"""
        events.append("tool")
        return "ok"

    def approve(tool, args):
        events.append("approval")
        return "yes"

    def before(tool, args, risk):
        events.append("pre")
        return None

    result = execute_tool_call(
        "_pre_order", "{}", cfg(), approver=approve, before_execute=before
    )

    assert not result.is_error
    assert result.meta["executed"] is True
    assert events == ["approval", "pre", "tool"]


def test_pre_execute_callback_can_block_without_marking_tool_executed():
    called = []

    @tool(name="_pre_block")
    def target() -> str:
        """target"""
        called.append(True)
        return "ok"

    result = execute_tool_call(
        "_pre_block",
        "{}",
        cfg(),
        before_execute=lambda tool, args, risk: "policy denied",
    )

    assert result.is_error
    assert result.meta["executed"] is False
    assert result.meta["pre_tool_hook_blocked"] is True
    assert "policy denied" in result.content
    assert called == []
