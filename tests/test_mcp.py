import json
import os

import pytest

from noval.builtins import call_mcp_tool, list_mcp_servers, list_mcp_tools
from noval.mcp import (
    McpRegistry, McpServerInfo, _configured_env, _prepare_stdio_process,
    discover_mcp_servers, mcp_index_context,
)
from noval.process import (
    PreparedProcess, SandboxStatus, SandboxStrength,
)
from noval.tools import Context, Risk, ToolError, get_tool


class FakeMcpClient:
    def __init__(self):
        self.listed = []
        self.called = []
        self.fail = False

    def list_tools(self, server, *, timeout):
        self.listed.append((server.server_id, timeout))
        return [
            {
                "name": "echo",
                "title": "Echo",
                "description": "echo arguments",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
            {
                "name": "sum",
                "description": "add numbers",
                "inputSchema": {"type": "object"},
            },
        ]

    def call_tool(self, server, tool_name, arguments, *, timeout):
        self.called.append((server.server_id, tool_name, arguments, timeout))
        if self.fail:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "bad request"}],
            }
        return {
            "content": [{"type": "text", "text": json.dumps(arguments)}],
            "structuredContent": {"ok": True, "arguments": arguments},
            "isError": False,
        }


class JsonStringMcpClient(FakeMcpClient):
    def call_tool(self, server, tool_name, arguments, *, timeout):
        self.called.append((server.server_id, tool_name, arguments, timeout))
        text = json.dumps({"password": "FAKE_DB_PASSWORD", "normal": "visible"}, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"result": text},
            "isError": False,
        }


def _mcp_file(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _fake_registry(tmp_path, client=None):
    server = McpServerInfo(
        server_id="project.mcp:demo",
        name="demo",
        source="project.mcp",
        command="python",
        args=["server.py"],
        env={"TOKEN": "SECRET"},
        cwd=tmp_path,
        location=str(tmp_path / ".noval" / "mcp.json"),
    )
    return McpRegistry([server], client=client or FakeMcpClient())


def test_discover_mcp_servers_from_user_and_project_config(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    _mcp_file(home / ".noval" / "mcp.json", {
        "mcpServers": {
            "global-github": {
                "command": "node",
                "args": ["github.js"],
                "env": {"GITHUB_TOKEN": "SECRET_TOKEN"},
            }
        }
    })
    _mcp_file(workdir / ".noval" / "mcp.json", {
        "mcpServers": {
            "local-db": {
                "command": "python",
                "args": ["server.py"],
                "cwd": "tools/mcp",
            }
        }
    })

    servers, errors = discover_mcp_servers(workdir, home=home)

    assert errors == []
    assert [item.server_id for item in servers] == [
        "user.mcp:global-github",
        "project.mcp:local-db",
    ]
    assert servers[1].cwd == (workdir / "tools" / "mcp").resolve()
    registry = McpRegistry(servers)
    index = mcp_index_context(registry)
    assert "<available_mcp_servers>" in index
    assert "global-github" in index
    assert "GITHUB_TOKEN" in index
    assert "SECRET_TOKEN" not in index


def test_project_root_mcp_json_is_not_discovered(tmp_path):
    workdir = tmp_path / "work"
    _mcp_file(workdir / ".mcp.json", {
        "mcpServers": {"legacy-root": {"command": "python", "args": ["legacy.py"]}}
    })
    _mcp_file(workdir / ".noval" / "mcp.json", {
        "mcpServers": {"project-local": {"command": "python", "args": ["server.py"]}}
    })

    servers, errors = discover_mcp_servers(workdir, home=tmp_path / "home")

    assert errors == []
    assert [item.server_id for item in servers] == ["project.mcp:project-local"]


def test_mcp_snapshot_detects_config_changes(tmp_path):
    workdir = tmp_path / "work"
    config = workdir / ".noval" / "mcp.json"
    _mcp_file(config, {"mcpServers": {"demo": {"command": "python", "args": ["a.py"]}}})
    before = McpRegistry.discover(workdir, home=tmp_path / "home").snapshot()

    _mcp_file(config, {"mcpServers": {"demo": {"command": "python", "args": ["b.py"]}}})
    after = McpRegistry.discover(workdir, home=tmp_path / "home").snapshot()

    diff = before.diff(after)
    assert diff.changed == ["project.mcp:demo"]
    assert not diff.added and not diff.removed


def test_list_mcp_servers_reads_config_without_starting_server(tmp_path):
    fake = FakeMcpClient()
    ctx = Context(workdir=tmp_path, mcp=_fake_registry(tmp_path, fake))

    payload = json.loads(list_mcp_servers(ctx, query="demo"))

    assert payload["servers"][0]["id"] == "project.mcp:demo"
    assert payload["servers"][0]["env_keys"] == ["TOKEN"]
    assert "SECRET" not in json.dumps(payload, ensure_ascii=False)
    assert fake.listed == []
    assert fake.called == []


def test_list_mcp_tools_and_call_mcp_tool_use_configured_client(tmp_path):
    fake = FakeMcpClient()
    ctx = Context(workdir=tmp_path, mcp=_fake_registry(tmp_path, fake))

    listed = json.loads(list_mcp_tools(ctx, server="demo", query="echo", timeout=7))
    assert listed["tools"][0]["name"] == "echo"
    assert fake.listed == [("project.mcp:demo", 7)]

    called = json.loads(call_mcp_tool(ctx, server="demo", tool="echo", arguments={"text": "hi"}, timeout=9))
    assert called["structured_content"]["arguments"] == {"text": "hi"}
    assert called["content"][0] == {"type": "json", "value": {"text": "hi"}}
    assert fake.called == [("project.mcp:demo", "echo", {"text": "hi"}, 9)]


def test_call_mcp_tool_parses_json_strings_in_content_and_structured_content(tmp_path):
    fake = JsonStringMcpClient()
    ctx = Context(workdir=tmp_path, mcp=_fake_registry(tmp_path, fake))

    called = json.loads(call_mcp_tool(ctx, server="demo", tool="config", timeout=9))

    expected = {"password": "FAKE_DB_PASSWORD", "normal": "visible"}
    assert called["content"][0] == {"type": "json", "value": expected}
    assert called["structured_content"]["result"] == expected


def test_call_mcp_tool_surfaces_mcp_tool_errors(tmp_path):
    fake = FakeMcpClient()
    fake.fail = True
    ctx = Context(workdir=tmp_path, mcp=_fake_registry(tmp_path, fake))

    with pytest.raises(ToolError, match="bad request"):
        call_mcp_tool(ctx, server="demo", tool="echo")


def test_mcp_execution_tools_are_dangerous():
    assert get_tool("list_mcp_servers").risk is Risk.READ
    assert get_tool("list_mcp_tools").risk is Risk.DANGEROUS
    assert get_tool("call_mcp_tool").risk is Risk.DANGEROUS


def test_mcp_stdio_launch_is_prepared_by_process_runtime(tmp_path):
    class RecordingRuntime:
        def __init__(self):
            self.specs = []

        def prepare(self, spec):
            self.specs.append(spec)
            return PreparedProcess(
                argv=("sandbox-wrapper", "--", *spec.argv),
                cwd=spec.cwd,
                env=spec.env,
                timeout=spec.timeout,
                purpose=spec.purpose,
                sandbox=SandboxStatus("fake", SandboxStrength.HARD),
            )

    server = _fake_registry(tmp_path).servers[0]
    runtime = RecordingRuntime()

    prepared = _prepare_stdio_process(server, runtime, 17)

    assert runtime.specs[0].argv == ("python", "server.py")
    assert runtime.specs[0].purpose == "mcp:project.mcp:demo"
    assert prepared.argv == ("sandbox-wrapper", "--", "python", "server.py")


def test_mcp_configured_env_does_not_copy_unrelated_parent_secrets(monkeypatch):
    monkeypatch.setenv("NOVAL_MCP_EXPLICIT", "visible")
    monkeypatch.setenv("NOVAL_MCP_UNRELATED_SECRET", "must-not-leak")
    reference = "%NOVAL_MCP_EXPLICIT%" if os.name == "nt" else "$NOVAL_MCP_EXPLICIT"

    env = _configured_env({"TOKEN": reference})

    assert env == {"TOKEN": "visible"}
    assert "NOVAL_MCP_UNRELATED_SECRET" not in env
