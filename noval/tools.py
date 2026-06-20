"""工具地基层。

职责：定义工具的统一数据结构与注册机制，让「加工具」退化成「写一个带类型注解的函数」。
本层**故意与 provider 无关**——这里只描述工具是什么（name/description/JSON schema），
如何把它翻译成某个厂商的 function-calling 格式，是 client.py 适配器的事（接缝1）。

工具契约（写工具的人只需记住）：
  - 成功      → return 原始内容
  - 领域错误  → raise ToolError("带领域信息的好提示")
  - 其余一切（通用异常、超时、截断、确认、日志）由 executor 框架负责。
"""
from __future__ import annotations

import inspect
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Literal, Optional, Union,
    get_args, get_origin, get_type_hints,
)


# ---------------------------------------------------------------------------
# 风险级别：确认门据此决定是否拦截（拦不拦由 config 说了算，不在工具内写 input()）
# ---------------------------------------------------------------------------
class Risk(str, Enum):
    READ = "read"            # 只读，永不拦
    WRITE = "write"          # 改动本地状态
    DANGEROUS = "dangerous"  # 执行任意命令等，默认必须确认


# ---------------------------------------------------------------------------
# 工具主动抛出的错误：框架会把 message 当作「可被模型纠正」的错误回传给模型
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """领域错误。message 应包含能让模型下一步自我修正的具体信息。"""


# ---------------------------------------------------------------------------
# 统一的工具结果：content 给模型，meta 给框架/日志，二者分开
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    content: str                          # 喂回模型的文本
    is_error: bool = False
    truncated: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)  # 耗时/原始长度等，不给模型


# ---------------------------------------------------------------------------
# 工具定义：注册表里存的就是它
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]   # JSON Schema（自动从函数签名生成）
    func: Callable
    risk: Risk = Risk.READ
    # 仅对「跑子进程」的工具有意义；纯 Python 函数无法被安全强杀（见 DESIGN.md 决策4）
    timeout: Optional[float] = None


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, Tool] = {}


def get_tool(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def all_tools() -> List[Tool]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# 类型注解 → JSON Schema 的自动推导
# ---------------------------------------------------------------------------
_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _type_schema(py_type: Any) -> Dict[str, Any]:
    """把一个 Python 类型注解转成 JSON Schema 片段。

    支持裸类型、Optional[X] / X|None、List[X]、Dict、Literal[...]；
    无法识别的类型退化为 string（保守，但至少不会瞎标成错误的具体类型）。
    """
    origin = get_origin(py_type)

    # Optional[X] / Union[X, None] / X | None → 取非 None 的那个；多类型 union 则不限制
    if origin is Union or origin is getattr(types, "UnionType", None):
        non_none = [a for a in get_args(py_type) if a is not type(None)]
        return _type_schema(non_none[0]) if len(non_none) == 1 else {}

    if origin is list:
        args = get_args(py_type)
        return {"type": "array", "items": _type_schema(args[0]) if args else {"type": "string"}}

    if origin is dict:
        return {"type": "object"}

    if origin is Literal:
        return {"enum": list(get_args(py_type))}

    return {"type": _PY_TO_JSON.get(py_type, "string")}


def _build_schema(func: Callable, param_descriptions: Dict[str, str]) -> Dict[str, Any]:
    """从函数的类型注解 + 默认值推导出 JSON Schema。

    - 类型注解 → 参数类型（未注解默认按 string 处理）
    - 无默认值的参数 → required
    - param_descriptions 提供每个参数的自然语言说明（模型很依赖它来正确填参）
    """
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    props: Dict[str, Any] = {}
    required: List[str] = []

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        py_type = hints.get(pname, str)
        prop: Dict[str, Any] = _type_schema(py_type)
        if pname in param_descriptions:
            prop["description"] = param_descriptions[pname]
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {"type": "object", "properties": props, "required": required}


# ---------------------------------------------------------------------------
# @tool 装饰器：加工具的唯一入口
# ---------------------------------------------------------------------------
def tool(
    risk: Risk = Risk.READ,
    *,
    name: Optional[str] = None,
    param_descriptions: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    override: bool = False,
) -> Callable:
    """把一个普通函数注册成工具。

    用法：
        @tool(risk=Risk.READ, param_descriptions={"path": "要读取的文件路径"})
        def read_file(path: str) -> str:
            '''读取指定路径的文件内容。'''
            ...

    description 取自函数 docstring；schema 自动从签名生成。
    返回原函数本身（不包装），因此工具仍可像普通函数一样被直接调用/测试。

    重名默认 raise（fail-fast）：注册表定义了模型可调用的能力，静默覆盖会让
    工具名对应的实现取决于 import 顺序，是更隐蔽的故障。确为有意覆盖
    （插件替换内置工具等）时显式传 override=True。
    """
    def decorator(func: Callable) -> Callable:
        t = Tool(
            name=name or func.__name__,
            description=(func.__doc__ or "").strip(),
            parameters=_build_schema(func, param_descriptions or {}),
            func=func,
            risk=risk,
            timeout=timeout,
        )
        if t.name in _REGISTRY and not override:
            raise ValueError(
                f"工具名 '{t.name}' 已注册；如确为有意覆盖请用 @tool(..., override=True)"
            )
        _REGISTRY[t.name] = t
        return func
    return decorator


# ===========================================================================
# 内置工具
# ===========================================================================
@tool(risk=Risk.READ, param_descriptions={"path": "要读取的文件路径"})
def read_file(path: str) -> str:
    """读取指定路径的文件内容。用于查看文件，不要用于目录。"""
    p = Path(path)
    # 领域错误：我们比框架更清楚「为什么失败」，主动给出可纠错的提示
    if not p.exists():
        raise ToolError(f"file '{path}' not found")
    if p.is_dir():
        raise ToolError(f"'{path}' is a directory, not a file; 用列目录的工具代替")
    # 其余失败（编码错误、权限不足等）交给框架统一兜——这里不写 try/except
    return p.read_text(encoding="utf-8")
