"""agent 循环测试：用 MockClient 跑完整条「工具调用 → 回填 → 最终回复」闭环，离线零成本。"""
import json
import os
from datetime import datetime
from pathlib import Path

from noval.agent import (
    Agent, _to_bash_path, detect_environment, load_project_memory,
)
from noval.client import LLMResponse, MockClient, ToolCall, mock_text, mock_tool_call
from noval.config import Config


def _multi_tool_call(calls):
    """构造一个「一轮返回多个 tool_call」的响应。calls: [(id, name, args_json), ...]"""
    tcs = [ToolCall(id=i, name=n, arguments=a) for i, n, a in calls]
    am = {"role": "assistant", "content": None, "tool_calls": [
        {"id": i, "type": "function", "function": {"name": n, "arguments": a}}
        for i, n, a in calls]}
    return LLMResponse(content=None, tool_calls=tcs, assistant_message=am)


def cfg():
    return Config(
        model="m", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000, auto_approve=["read", "write"],
    )


def test_full_tool_loop(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("机密内容", encoding="utf-8")

    # 脚本：第一轮请求调 read_file，第二轮基于结果给出最终回复
    client = MockClient([
        mock_tool_call("c1", "read_file", json.dumps({"path": str(f)})),
        mock_text("文件里写着：机密内容"),
    ])
    agent = Agent(client, cfg())

    reply = agent.send("看看 doc.txt 写了什么")
    assert reply == "文件里写着：机密内容"

    # 工具结果确实被回填进历史（第二轮请求时模型能看到）
    second_request = client.seen_messages[1]
    tool_msgs = [m for m in second_request if m.get("role") == "tool"]
    assert tool_msgs and "机密内容" in tool_msgs[0]["content"]


def test_no_tool_call_returns_directly():
    client = MockClient([mock_text("你好！")])
    agent = Agent(client, cfg())
    assert agent.send("hi") == "你好！"


def test_multiple_tool_calls_all_backfilled(tmp_path):
    # 一轮多个 tool_call，其中一个失败：回填的 tool 消息数必须 == call 数（否则下一轮 API 拒绝）
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    client = MockClient([
        _multi_tool_call([
            ("c1", "read_file", json.dumps({"path": str(f)})),   # 成功
            ("c2", "read_file", json.dumps({})),                  # 缺 path → 报错
        ]),
        mock_text("done"),
    ])
    agent = Agent(client, cfg(), workdir=str(tmp_path))
    assert agent.send("read two") == "done"
    tool_msgs = [m for m in client.seen_messages[1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}


def test_answer_pending_tool_calls_backfills():
    # 中断后补齐未回填的 tool_call，保持历史合法
    agent = Agent(MockClient([mock_text("x")]), cfg())
    agent.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "t", "arguments": "{}"}},
    ]})
    agent.messages.append({"role": "tool", "tool_call_id": "a", "content": "ok"})  # 只回填了 a
    agent._answer_pending_tool_calls()
    tool_ids = [m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"]
    assert tool_ids == ["a", "b"]                       # b 被补上


def test_workdir_defaults_to_cwd():
    agent = Agent(MockClient([mock_text("hi")]), cfg())
    assert agent.workdir == Path(os.getcwd()).resolve()


def test_workdir_explicit(tmp_path):
    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))
    assert agent.workdir == tmp_path.resolve()


def test_to_bash_path():
    assert _to_bash_path("C:\\Users\\x", "WSL") == "/mnt/c/Users/x"
    assert _to_bash_path("E:/Work/y", "Git Bash") == "/e/Work/y"
    assert _to_bash_path("/already/unix", "WSL") is None        # 非 Windows 路径不转


def test_detect_environment_has_basics(tmp_path):
    env = detect_environment(tmp_path)
    assert "<environment>" in env and "workdir" in env
    assert str(tmp_path) in env
    assert "当前日期" not in env and "当前时间" not in env       # 时间不进 system 前缀


def test_send_stamps_user_message_with_current_time():
    # 时间随回合注入 user 消息，不进 system —— 保前缀稳定 + 每轮刷新「现在」
    client = MockClient([mock_text("ok")])
    agent = Agent(client, cfg())
    agent.send("hello")
    sys_and_user = client.seen_messages[0]
    user_msg = sys_and_user[-1]
    assert user_msg["role"] == "user"
    assert "当前时间" in user_msg["content"]
    assert datetime.now().strftime("%Y-%m-%d") in user_msg["content"]
    assert "hello" in user_msg["content"]
    # system 消息(前缀)里不含时间
    assert "当前时间" not in sys_and_user[0]["content"]


def test_system_prompt_assembly_order():
    # 稳定性从高到低：人设 → 环境 → 项目记忆（缓存前缀尽量长 + 规则先立框）
    agent = Agent(
        MockClient([mock_text("hi")]), cfg(),
        system_prompt="PERSONA",
        env_context="<environment>E</environment>",
        project_memory="<project_instructions>P</project_instructions>",
    )
    c = agent.messages[0]["content"]
    assert c.index("PERSONA") < c.index("<environment>") < c.index("<project_instructions>")


def test_default_system_prompt_when_not_overridden():
    from noval.agent import DEFAULT_SYSTEM_PROMPT
    agent = Agent(MockClient([mock_text("hi")]), cfg())        # 不传任何 system_prompt
    assert agent.messages[0]["content"] == DEFAULT_SYSTEM_PROMPT


# --- 项目记忆 (AGENTS.md / CLAUDE.md) -------------------------------------
def test_project_memory_loads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("用 pnpm；提交前跑测试", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None
    assert 'source="AGENTS.md"' in mem
    assert "用 pnpm" in mem
    assert "不是系统规则" in mem               # 安全边界框架文字在


def test_project_memory_falls_back_to_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("项目约定 X", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert mem is not None and 'source="CLAUDE.md"' in mem and "项目约定 X" in mem


def test_project_memory_prefers_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text("AAA", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("CCC", encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert 'source="AGENTS.md"' in mem and "AAA" in mem and "CCC" not in mem


def test_project_memory_none_when_absent(tmp_path):
    assert load_project_memory(tmp_path) is None


def test_project_memory_truncates_when_huge(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * 50000, encoding="utf-8")
    mem = load_project_memory(tmp_path)
    assert "已截断" in mem and len(mem) < 50000


def test_max_steps_guard_summarizes():
    # 模型每轮都调工具永不收手 → 被 max_steps 截停；触顶时让模型总结现场再停
    script = [mock_tool_call(f"c{i}", "read_file", "{}") for i in range(5)]  # cfg max_steps=5
    script.append(mock_text("进度小结：已查明 X，卡在 Y，下一步 Z"))         # 触顶后的总结
    client = MockClient(script)
    agent = Agent(client, cfg())
    reply = agent.send("loop forever")
    assert "进度小结" in reply                       # 返回模型的现场总结，而非固定句
    # 最后一次请求是「无工具」的总结调用
    assert client.seen_messages[-1][-1]["content"].startswith("已达到最大工具调用步数")
