"""Provider 抽象（接缝1）。

agent 循环只依赖 LLMClient 接口与下面这几个归一化的数据结构，
**永不直接 import openai**。换模型/换厂商 = 换一个实现 LLMClient 的适配器。

注：当前内核以「OpenAI 兼容 wire 格式」为对话历史的载体（DeepSeek 即兼容此格式）。
支持非 OpenAI 格式的 provider 是后续工作（见 DESIGN.md 待办）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .tools import Tool


# --- 归一化的数据结构：循环只跟这些打交道 ---------------------------------
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str   # 原始 JSON 字符串；解析容错由 executor 负责


@dataclass(frozen=True)
class TokenUsage:
    """一次成功模型请求返回的标准化 token 用量。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


@dataclass
class LLMResponse:
    content: Optional[str]                 # 助手文本（无工具调用时即最终回复）
    tool_calls: List[ToolCall]             # 本轮请求的工具调用
    assistant_message: Dict[str, Any]      # Provider 构造的、可安全回放的历史消息
    raw: Any = None                        # 原始响应对象，仅供调试/日志
    meta: Dict[str, Any] = field(default_factory=dict)  # 耗时等框架元数据，不给模型
    usage: Optional[TokenUsage] = None      # Provider 返回的实际用量；缺失时不估算


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


def _value(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _optional_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _normalize_usage(raw_usage: Any) -> Optional[TokenUsage]:
    """归一化 OpenAI 兼容 usage；核心计数缺失时不做猜测。"""
    if raw_usage is None:
        return None
    prompt_tokens = _optional_int(_value(raw_usage, "prompt_tokens"))
    completion_tokens = _optional_int(_value(raw_usage, "completion_tokens"))
    total_tokens = _optional_int(_value(raw_usage, "total_tokens"))
    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None

    completion_details = _value(raw_usage, "completion_tokens_details")
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cache_hit_tokens=_optional_int(_value(raw_usage, "prompt_cache_hit_tokens")),
        cache_miss_tokens=_optional_int(_value(raw_usage, "prompt_cache_miss_tokens")),
        reasoning_tokens=_optional_int(_value(completion_details, "reasoning_tokens")),
    )


# --- 真实适配器：OpenAI 兼容端点（DeepSeek） ------------------------------
class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 2,
    ):
        from openai import OpenAI  # 延迟导入：保证核心逻辑不依赖具体 SDK
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = model

    def complete(self, messages: List[Dict[str, Any]], tools: List[Tool]) -> LLMResponse:
        openai_tools = [_to_openai_tool(t) for t in tools] or None
        started = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto" if openai_tools else None,
        )
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (msg.tool_calls or [])
        ]
        reasoning_content = getattr(msg, "reasoning_content", None)
        # 白名单重建回放消息，不直接 model_dump()。DeepSeek 思考模式有一个例外：
        # 发生工具调用时 reasoning_content 是后续请求的必需协议状态，必须保留；
        # 普通最终回复则无需回传，避免无意义地扩大历史与会话文件。
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": msg.content}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in tool_calls
            ]
            if reasoning_content is not None:
                assistant_message["reasoning_content"] = reasoning_content

        usage = _normalize_usage(getattr(resp, "usage", None))
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
            meta={
                "thinking_enabled": reasoning_content is not None,
                "duration_ms": duration_ms,
            },
            usage=usage,
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
def mock_text(
    text: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
    usage: Optional[TokenUsage] = None,
) -> LLMResponse:
    return LLMResponse(
        content=text,
        tool_calls=[],
        assistant_message={"role": "assistant", "content": text},
        meta=dict(meta or {}),
        usage=usage,
    )


def mock_tool_call(
    call_id: str,
    name: str,
    arguments_json: str,
    *,
    reasoning_content: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    usage: Optional[TokenUsage] = None,
) -> LLMResponse:
    assistant_message: Dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments_json},
        }],
    }
    if reasoning_content is not None:
        assistant_message["reasoning_content"] = reasoning_content
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments_json)],
        assistant_message=assistant_message,
        meta=dict(meta or {}),
        usage=usage,
    )
