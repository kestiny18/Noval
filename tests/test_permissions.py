from noval.permissions import PermissionController, PermissionMode, PermissionState


def test_default_state_requests_dangerous_approval_only():
    permissions = PermissionController()
    assert permissions.mode is PermissionMode.ASK
    assert permissions.requires_approval("read_file", "read") is False
    assert permissions.requires_approval("write_file", "write") is False
    assert permissions.requires_approval("run_bash", "dangerous") is True


def test_full_access_and_tool_grants_are_independent():
    permissions = PermissionController()
    permissions.allow_tool("run_bash")
    assert permissions.requires_approval("run_bash", "dangerous") is False
    assert permissions.requires_approval("other", "dangerous") is True

    permissions.set_mode(PermissionMode.FULL_ACCESS)
    assert permissions.requires_approval("other", "dangerous") is False

    permissions.set_mode(PermissionMode.ASK)
    assert permissions.approved_tools == {"run_bash"}
    assert permissions.requires_approval("other", "dangerous") is True


def test_changes_emit_serializable_snapshots():
    snapshots = []
    permissions = PermissionController(on_change=lambda data: snapshots.append(data))

    permissions.set_mode(PermissionMode.FULL_ACCESS)
    permissions.allow_tool("run_bash")
    permissions.revoke_tool("run_bash")
    permissions.reset()

    assert snapshots[0] == {"mode": "full_access", "approved_tools": []}
    assert snapshots[1]["approved_tools"] == ["run_bash"]
    assert snapshots[-1] == {"mode": "ask", "approved_tools": []}


def test_invalid_persisted_state_falls_back_safely():
    state = PermissionState.from_dict({
        "mode": "unknown",
        "approved_tools": ["run_bash", "", 123],
    })
    assert state.mode is PermissionMode.ASK
    assert state.approved_tools == {"run_bash"}
