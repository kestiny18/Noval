import json
import sys
from pathlib import Path

import pytest

from noval.agent import Agent
from noval.api import (
    AcceptanceCriterion,
    CompletionStatus,
    CriterionStatus,
    GoalContract,
)
from noval.client import MockClient, mock_text, mock_tool_call
from noval.config import Config
from noval.hooks import HookEvent, HookOutcome, HookRegistry
from noval.permissions import PermissionController, PermissionMode
from noval.process import NoSandbox, ProcessResult, SandboxStatus
from noval.messages import MessageRole
from noval.shell import ShellBackend
from noval.tools import Context, Risk, tool
from noval.task import TaskController


def cfg():
    return Config(
        model="m",
        base_url="u",
        api_key_env="K",
        max_steps=8,
        max_tool_output_chars=8000,
    )


def write_hooks(workdir: Path, groups):
    config_dir = workdir / ".noval"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": groups}, ensure_ascii=False),
        encoding="utf-8",
    )


class RecordingRuntime:
    def __init__(self, results):
        self.results = list(results)
        self.specs = []

    def run(self, spec):
        self.specs.append(spec)
        stdout, stderr, returncode = self.results.pop(0)
        return ProcessResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            duration_ms=1.0,
            sandbox=SandboxStatus(backend="test", strength=NoSandbox().status.strength),
        )


class RecordingStore:
    def __init__(self):
        self.saved = []

    def append(self, message):
        self.saved.append(message)


def full_access():
    permissions = PermissionController()
    permissions.set_mode(PermissionMode.FULL_ACCESS)
    return permissions


def hook_goal(hook_id):
    return GoalContract(
        goal_id="hook-goal",
        objective="Pass the configured project validation.",
        acceptance_criteria=(
            AcceptanceCriterion(
                criterion_id="validation",
                description="The configured validation passes.",
                verification_source=f"hook:{hook_id}",
            ),
        ),
    )


def test_grouped_config_preserves_order_and_match_fields(tmp_path):
    write_hooks(tmp_path, {
        "PostToolUse": [
            {
                "id": "first",
                "match": {"tools": ["edit_file"], "status": ["success"]},
                "command": "first-command",
            },
            {
                "id": "second",
                "command": "second-command",
            },
        ],
        "Stop": [
            {
                "id": "tests",
                "match": {"afterTools": ["edit_file"]},
                "command": "test-command",
            }
        ],
    })

    registry = HookRegistry.discover(tmp_path)

    assert registry.errors == ()
    assert [hook.hook_id for hook in registry.hooks_for(HookEvent.POST_TOOL_USE)] == [
        "first", "second",
    ]
    assert registry.hooks_for(HookEvent.STOP)[0].match.after_tools == ("edit_file",)
    assert registry.is_stop_repair_tool("edit_file") is True
    assert registry.is_stop_repair_tool("read_file") is False


def test_invalid_event_and_duplicate_ids_are_reported_without_crashing(tmp_path):
    write_hooks(tmp_path, {
        "BeforeEverything": [{"id": "x", "command": "x"}],
        "PreToolUse": [
            {"id": "same", "command": "one"},
            {"id": "same", "command": "two"},
        ],
    })

    registry = HookRegistry.discover(tmp_path)

    assert any("unknown hook event" in error for error in registry.errors)
    assert any("duplicate" in error for error in registry.errors)
    assert len(registry.hooks_for(HookEvent.PRE_TOOL_USE)) == 1


def test_unknown_config_version_disables_all_hooks(tmp_path):
    config_dir = tmp_path / ".noval"
    config_dir.mkdir()
    (config_dir / "hooks.json").write_text(json.dumps({
        "version": 2,
        "hooks": {"PreToolUse": [{"id": "policy", "command": "check"}]},
    }), encoding="utf-8")

    registry = HookRegistry.discover(tmp_path)

    assert not registry.has_hooks()
    assert any("version must be 1" in error for error in registry.errors)


@pytest.mark.parametrize("timeout", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_timeout_is_rejected(tmp_path, timeout):
    config_dir = tmp_path / ".noval"
    config_dir.mkdir()
    (config_dir / "hooks.json").write_text(
        '{"version":1,"hooks":{"Stop":['
        f'{{"id":"tests","command":"check","timeout":{timeout}}}'
        ']}}',
        encoding="utf-8",
    )

    registry = HookRegistry.discover(tmp_path)

    assert not registry.has_hooks()
    assert any("timeout must be a finite positive number" in error for error in registry.errors)


def test_hook_config_symlink_cannot_escape_workdir(tmp_path):
    project = tmp_path / "project"
    external = tmp_path / "external-hooks.json"
    (project / ".noval").mkdir(parents=True)
    external.write_text('{"version":1,"hooks":{}}', encoding="utf-8")
    try:
        (project / ".noval" / "hooks.json").symlink_to(external)
    except OSError:
        pytest.skip("symlinks are unavailable on this host")

    registry = HookRegistry.discover(project)

    assert not registry.has_hooks()
    assert any("escape the workdir through a symbolic link" in error for error in registry.errors)


def test_pre_tool_use_stops_after_first_deny(tmp_path):
    write_hooks(tmp_path, {
        "PreToolUse": [
            {"id": "deny-first", "command": "one"},
            {"id": "never-run", "command": "two"},
        ]
    })
    runtime = RecordingRuntime([("", "blocked", 1)])

    batch = HookRegistry.discover(tmp_path).run(
        HookEvent.PRE_TOOL_USE,
        runtime=runtime,
        permissions=full_access(),
        approver=None,
        max_output_chars=1000,
        tool_name="edit_file",
    )

    assert batch.blocked
    assert [result.hook_id for result in batch.results] == ["deny-first"]
    assert [spec.argv[0] for spec in runtime.specs] == ["one"]


def test_post_tool_use_runs_all_matching_hooks_in_order(tmp_path):
    write_hooks(tmp_path, {
        "PostToolUse": [
            {"id": "lint", "command": "lint"},
            {"id": "audit", "command": "audit"},
        ]
    })
    runtime = RecordingRuntime([
        ("lint failed", "", 1),
        ("", "", 0),
    ])

    batch = HookRegistry.discover(tmp_path).run(
        HookEvent.POST_TOOL_USE,
        runtime=runtime,
        permissions=full_access(),
        approver=None,
        max_output_chars=1000,
        tool_name="edit_file",
        status="success",
    )

    assert [spec.argv[0] for spec in runtime.specs] == ["lint", "audit"]
    assert [result.outcome for result in batch.results] == [
        HookOutcome.DENY, HookOutcome.ALLOW,
    ]
    assert "lint failed" in (batch.feedback() or "")


def test_hook_always_approval_is_bound_to_config_fingerprint(tmp_path):
    write_hooks(tmp_path, {
        "PreToolUse": [{"id": "policy", "command": "check", "args": ["one"]}]
    })
    permissions = PermissionController()
    approvals = []

    def approve(hook_tool, args):
        approvals.append(hook_tool.name)
        return "always"

    first_registry = HookRegistry.discover(tmp_path)
    first_registry.run(
        HookEvent.PRE_TOOL_USE,
        runtime=RecordingRuntime([("", "", 0)]),
        permissions=permissions,
        approver=approve,
        max_output_chars=1000,
        tool_name="edit_file",
    )
    first_registry.run(
        HookEvent.PRE_TOOL_USE,
        runtime=RecordingRuntime([("", "", 0)]),
        permissions=permissions,
        approver=approve,
        max_output_chars=1000,
        tool_name="edit_file",
    )
    write_hooks(tmp_path, {
        "PreToolUse": [{"id": "policy", "command": "check", "args": ["two"]}]
    })
    HookRegistry.discover(tmp_path).run(
        HookEvent.PRE_TOOL_USE,
        runtime=RecordingRuntime([("", "", 0)]),
        permissions=permissions,
        approver=approve,
        max_output_chars=1000,
        tool_name="edit_file",
    )

    assert len(approvals) == 2
    assert approvals[0] != approvals[1]


def test_hook_approval_failure_is_isolated_as_deny(tmp_path):
    write_hooks(tmp_path, {
        "PreToolUse": [{"id": "policy", "command": "check"}]
    })

    batch = HookRegistry.discover(tmp_path).run(
        HookEvent.PRE_TOOL_USE,
        runtime=RecordingRuntime([]),
        permissions=PermissionController(),
        approver=lambda tool, args: (_ for _ in ()).throw(RuntimeError("prompt failed")),
        max_output_chars=1000,
        tool_name="edit_file",
    )

    assert batch.blocked
    assert "Hook framework failure" in (batch.feedback() or "")


def test_json_protocol_returns_redacted_context(tmp_path):
    write_hooks(tmp_path, {
        "PostToolUse": [{
            "id": "context",
            "command": "context",
            "protocol": "json",
        }]
    })
    payload = json.dumps({
        "outcome": "context",
        "text": "token=SECRET_VALUE\nuse this diagnostic",
    })

    batch = HookRegistry.discover(tmp_path).run(
        HookEvent.POST_TOOL_USE,
        runtime=RecordingRuntime([(payload, "", 0)]),
        permissions=full_access(),
        approver=None,
        max_output_chars=1000,
        tool_name="edit_file",
        status="success",
    )

    feedback = batch.feedback() or ""
    assert "SECRET_VALUE" not in feedback
    assert "<redacted>" in feedback
    assert "use this diagnostic" in feedback


def test_pre_hook_denial_prevents_target_tool_and_reaches_model(tmp_path):
    called = []

    @tool(name="_pre_hook_target")
    def target() -> str:
        """Target used by the PreToolUse hook test."""
        called.append(True)
        return "executed"

    write_hooks(tmp_path, {
        "PreToolUse": [{
            "id": "policy",
            "match": {"tools": ["_pre_hook_target"]},
            "command": sys.executable,
            "args": ["-c", "import sys; print('project policy denied'); sys.exit(1)"],
        }]
    })
    client = MockClient([
        mock_tool_call("c1", "_pre_hook_target", "{}"),
        mock_text("I will not run it."),
    ])

    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
    )

    assert agent.send("run target") == "I will not run it."
    assert called == []
    tool_messages = [
        message for message in client.seen_messages[1]
        if message.role is MessageRole.TOOL
    ]
    assert "project policy denied" in tool_messages[-1].tool_results[0].content
    assert "PreToolUse" in tool_messages[-1].tool_results[0].content


def test_post_hook_failure_is_attached_to_tool_result(tmp_path):
    @tool(name="_post_hook_target")
    def target() -> str:
        """Target used by the PostToolUse hook test."""
        return "tool succeeded"

    write_hooks(tmp_path, {
        "PostToolUse": [{
            "id": "lint",
            "match": {"tools": ["_post_hook_target"], "status": ["success"]},
            "command": sys.executable,
            "args": ["-c", "import sys; print('lint failed'); sys.exit(1)"],
        }]
    })
    client = MockClient([
        mock_tool_call("c1", "_post_hook_target", "{}"),
        mock_text("fixed response"),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
    )

    assert agent.send("run target") == "fixed response"
    tool_message_content = next(
        message.tool_results[0].content for message in client.seen_messages[1]
        if message.role is MessageRole.TOOL
    )
    assert "tool succeeded" in tool_message_content
    assert "lint failed" in tool_message_content
    assert 'event="PostToolUse"' in tool_message_content


def test_run_bash_nonzero_exit_triggers_error_post_hook(tmp_path):
    write_hooks(tmp_path, {
        "PostToolUse": [{
            "id": "diagnose-build-failure",
            "match": {"tools": ["run_bash"], "status": ["error"]},
            "command": "post-check",
        }]
    })
    runtime = RecordingRuntime([
        ("", "compile failed", 7),
        ("post hook ran", "", 0),
    ])
    client = MockClient([
        mock_tool_call("c1", "run_bash", json.dumps({"command": "compile"})),
        mock_text("reported failure"),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
        process_runtime=runtime,
        shell_backend=ShellBackend("bash", "test"),
    )

    assert agent.send("compile the project") == "reported failure"
    assert len(runtime.specs) == 2
    assert runtime.specs[0].purpose == "run-bash"
    assert runtime.specs[1].argv == ("post-check",)
    tool_message_content = next(
        message.tool_results[0].content for message in client.seen_messages[1]
        if message.role is MessageRole.TOOL
    )
    assert "exit code 7" in tool_message_content
    assert "compile failed" in tool_message_content


def test_stop_hook_failure_returns_to_model_then_passes_after_repair(tmp_path):
    @tool(name="_hook_dirty")
    def dirty() -> str:
        """Mark the turn as having tool activity."""
        return "dirty"

    @tool(name="_hook_fix", risk=Risk.WRITE)
    def fix(ctx: Context) -> str:
        """Create the marker expected by the Stop hook."""
        (ctx.workdir / "fixed.marker").write_text("ok", encoding="utf-8")
        return "fixed"

    check = (
        "from pathlib import Path; import sys; "
        "ok=Path('fixed.marker').exists(); "
        "print('tests passed' if ok else 'compile failed'); sys.exit(0 if ok else 1)"
    )
    write_hooks(tmp_path, {
        "Stop": [{
            "id": "tests-before-stop",
            "match": {"afterTools": ["_hook_dirty", "_hook_fix"]},
            "command": sys.executable,
            "args": ["-c", check],
        }]
    })
    client = MockClient([
        mock_tool_call("c1", "_hook_dirty", "{}"),
        mock_text("draft completion"),
        mock_tool_call("c2", "_hook_fix", "{}"),
        mock_text("verified completion"),
    ])
    store = RecordingStore()
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
        store=store,
    )

    assert agent.send("finish the task") == "verified completion"
    assert (tmp_path / "fixed.marker").is_file()
    stop_feedback = [
        message for message in client.seen_messages[2]
        if message.role is MessageRole.USER and "hook_feedback" in message.text
    ]
    assert stop_feedback
    assert "compile failed" in stop_feedback[-1].text
    assert any(
        message.role is MessageRole.USER and "compile failed" in message.text
        for message in store.saved
    )


def test_unfiltered_stop_hook_runs_for_direct_reply_without_tool_use(tmp_path):
    marker = tmp_path / "stop-ran.marker"
    write_hooks(tmp_path, {
        "Stop": [{
            "id": "always-stop",
            "command": sys.executable,
            "args": [
                "-c",
                "from pathlib import Path; Path('stop-ran.marker').write_text('ok')",
            ],
        }]
    })
    client = MockClient([mock_text("direct reply")])

    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
    )

    assert agent.send("hello") == "direct reply"
    assert marker.read_text(encoding="utf-8") == "ok"


def test_stop_hook_allow_completes_matching_explicit_goal(tmp_path):
    write_hooks(tmp_path, {
        "Stop": [{
            "id": "tests",
            "command": sys.executable,
            "args": ["-c", "print('passed')"],
        }]
    })
    controller = TaskController()
    controller.activate_goal(hook_goal("tests"))
    agent = Agent(
        MockClient([mock_text("Validated completion.")]),
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
        task_controller=controller,
    )

    assert agent.send("finish") == "Validated completion."
    report = controller.completion_report()

    assert report is not None
    assert report.status is CompletionStatus.COMPLETED
    assert report.criteria[0].status is CriterionStatus.PASSED
    assert controller.state.verifications[-1].source == "hook:tests"


def test_pre_and_post_hooks_do_not_satisfy_stop_hook_criterion(tmp_path):
    @tool(name="_hook_evidence_target")
    def target() -> str:
        """Exercise non-Stop Hooks."""
        return "ok"

    write_hooks(tmp_path, {
        "PreToolUse": [{
            "id": "pre-check",
            "command": sys.executable,
            "args": ["-c", "print('pre passed')"],
        }],
        "PostToolUse": [{
            "id": "post-check",
            "command": sys.executable,
            "args": ["-c", "print('post passed')"],
        }],
    })
    controller = TaskController()
    controller.activate_goal(hook_goal("required"))
    agent = Agent(
        MockClient([
            mock_tool_call("c1", "_hook_evidence_target", "{}"),
            mock_text("Done."),
        ]),
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
        task_controller=controller,
    )

    assert agent.send("finish") == "Done."
    report = controller.completion_report()

    assert report is not None
    assert report.status is CompletionStatus.UNCERTAIN
    assert report.criteria[0].status is CriterionStatus.MISSING
    assert controller.state.verifications == []


def test_filtered_stop_hook_skips_direct_reply_without_matching_tool(tmp_path):
    marker = tmp_path / "stop-ran.marker"
    write_hooks(tmp_path, {
        "Stop": [{
            "id": "after-edit",
            "match": {"afterTools": ["edit_file"]},
            "command": sys.executable,
            "args": [
                "-c",
                "from pathlib import Path; Path('stop-ran.marker').write_text('ok')",
            ],
        }]
    })
    client = MockClient([mock_text("direct reply")])

    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
    )

    assert agent.send("hello") == "direct reply"
    assert not marker.exists()


def test_repeated_stop_failure_without_tool_activity_stops_loop(tmp_path):
    @tool(name="_hook_no_fix")
    def dirty() -> str:
        """Create tool activity without repairing the Stop failure."""
        return "dirty"

    write_hooks(tmp_path, {
        "Stop": [{
            "id": "always-fail",
            "match": {"afterTools": ["_hook_no_fix"]},
            "command": sys.executable,
            "args": ["-c", "import sys; print('same failure'); sys.exit(1)"],
        }]
    })
    client = MockClient([
        mock_tool_call("c1", "_hook_no_fix", "{}"),
        mock_text("first draft"),
        mock_text("second draft without repair"),
    ])
    agent = Agent(
        client,
        cfg(),
        workdir=str(tmp_path),
        permissions=full_access(),
    )

    result = agent.send("finish")

    assert "no new repair action" in result
    assert "same failure" in result
    assert len(client.seen_messages) == 3


def test_hook_config_refreshes_only_at_user_turn_boundary(tmp_path):
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg(), workdir=str(tmp_path))
    write_hooks(tmp_path, {
        "PostToolUse": [{"id": "new-hook", "command": "check"}]
    })

    assert agent.send("hello") == "ok"

    request_user = next(
        message for message in client.seen_messages[0]
        if message.role is MessageRole.USER
    )
    assert "<hook_update>" in request_user.text
    assert "PostToolUse/new-hook" in request_user.text
