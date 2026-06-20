"""Provider 抽象（接缝1）。

agent 循环只依赖 LLMClient 接口与下面这几个归一化的数据结构，
**永不直接 import openai**。换模型/换厂商 = 换一个实现 LLMClient 的适配器。

注：当前内核以「OpenAI 兼容 wire 格式」为对话历史的载体（DeepSeek 即兼容此格式）。
支持非 OpenAI 格式的 provider 是后续工作（见 DESIGN.md 待办）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .tools import Tool


# --- 归一化的数据结构：循环只跟这些打交道 ---------------------------------
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str   # 原始 JSON 字符串；解析容错由 executor 负责


@dataclass
class LLMResponse:
    content: Optional[str]                 # 助手文本（无工具调用时即最终回复）
    tool_calls: List[ToolCall]             # 本轮请求的工具调用
    assistant_message: Dict[str, Any]      # 要追加进历史的助手消息（provider 原生格式）
    raw: Any = None                        # 原始响应对象，仅供调试/日志


class LLMClient(Protocol):
    def complete(self, messages: List[Dict[str, Any]], tools: List[Tool]) -> LLMResponse:
        ...


def tool_message(call_id: str, content: str) -> Dict[str, Any]:
    """把一次工具结果包装成历史消息（OpenAI 兼容格式）。"""
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _to_openai_tool(tool: Tool) -> Dict[str, Any]:
    """Tool → OpenAI function-calling schema（provider 专属翻译，只在本层出现）。"""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


# --- 真实适配器：OpenAI 兼容端点（DeepSeek） ------------------------------
class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 延迟导入：保证核心逻辑不依赖具体 SDK
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def complete(self, messages: List[Dict[str, Any]], tools: List[Tool]) -> LLMResponse:
        openai_tools = [_to_openai_tool(t) for t in tools] or None
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto" if openai_tools else None,
        )
        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (msg.tool_calls or [])
        ]
        # 回填历史时只保留协议必需字段。msg.model_dump() 会带上 reasoning_content /
        # audio / annotations 等 provider 专属字段，把它们灌回下一轮请求：有的模型
        # (如 deepseek-reasoner) 会直接报错，也违背了 LLMClient 这层「不让 provider
        # 细节外泄」的初衷。所以这里显式重建一个干净的 assistant 消息。
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": msg.content}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            raw=resp,
        )


# --- 测试适配器：脚本化、离线、零成本 -------------------------------------
class MockClient:
    """按预设脚本逐步返回响应，使整条 agent 循环可在不联网下测试。"""

    def __init__(self, script: List[LLMResponse]):
        self._script = list(script)
        self.seen_messages: List[List[Dict[str, Any]]] = []  # 记录每次收到的历史，供断言

    def complete(self, messages: List[Dict[str, Any]], tools: List[Tool]) -> LLMResponse:
        self.seen_messages.append([dict(m) for m in messages])
        if not self._script:
            raise AssertionError("MockClient 脚本已用尽，但循环仍在请求")
        return self._script.pop(0)


# 构造 mock 响应的便捷函数，让测试可读
def mock_text(text: str) -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[],
        assistant_message={"role": "assistant", "content": text},
    )


def mock_tool_call(call_id: str, name: str, arguments_json: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments_json)],
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments_json},
            }],
        },
    )
