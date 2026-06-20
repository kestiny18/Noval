"""agent 循环测试：用 MockClient 跑完整条「工具调用 → 回填 → 最终回复」闭环，离线零成本。"""
import json
import os
from pathlib import Path

from noval.agent import Agent
from noval.client import MockClient, mock_text, mock_tool_call
from noval.config import Config


def cfg():
    return Config(
        model="m", base_url="u", api_key_env="K", max_steps=5,
        max_tool_output_chars=8000, auto_approve=["read", "write"],
        system_prompt="sys",
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


def test_workdir_defaults_to_cwd():
    agent = Agent(MockClient([mock_text("hi")]), cfg())
    assert agent.workdir == Path(os.getcwd()).resolve()


def test_workdir_explicit(tmp_path):
    agent = Agent(MockClient([mock_text("hi")]), cfg(), workdir=str(tmp_path))
    assert agent.workdir == tmp_path.resolve()


def test_max_steps_guard():
    # 模型每轮都调工具，永不收手 → 必须被 max_steps 截停而不是无限循环
    script = [mock_tool_call(f"c{i}", "read_file", "{}") for i in range(10)]
    client = MockClient(script)
    agent = Agent(client, cfg())
    reply = agent.send("loop forever")
    assert "最大工具调用步数" in reply
