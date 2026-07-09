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
    TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Tuple, Union,
    get_args, get_origin, get_type_hints,
)

from .permissions import PermissionController

if TYPE_CHECKING:
    from .confinement import ConfinementPolicy
    from .mcp import McpRegistry
    from .process import ProcessRuntime
    from .shell import ShellBackend
    from .skills import SkillRegistry


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
# 执行上下文：框架注入给「声明了 ctx: Context 首参」的工具，不进 schema
# ---------------------------------------------------------------------------
@dataclass
class ReadRecord:
    """一次文件读取的记录，供 write/edit 做「改前须先 read」+ staleness 校验。"""
    mtime: float
    content: str          # 归一化(\r\n→\n)后的全文，仅 full read 有意义
    is_partial: bool      # 带 offset/limit 的局部读 → True，不满足「先 read」要求
    read_ranges: List[Tuple[int, int]] = field(default_factory=list)  # 模型实际看过的闭区间行号
    total_lines: Optional[int] = None  # 已知总行数；读到 EOF 或整读预算截断时可得


@dataclass
class Context:
    """per-invocation 的执行上下文。workdir 决定相对路径与子进程 cwd；
    read_state 是跨工具调用共享的文件读取状态机（read-tracker）；
    permissions 集中管理当前会话的权限模式与工具授权。"""
    workdir: Path
    read_state: Dict[str, ReadRecord] = field(default_factory=dict)
    permissions: PermissionController = field(default_factory=PermissionController)
    shell_backend: Optional["ShellBackend"] = None
    process_runtime: Optional["ProcessRuntime"] = None
    confinement: Optional["ConfinementPolicy"] = None
    skills: Optional["SkillRegistry"] = None
    skills_auto_refresh: bool = False
    mcp: Optional["McpRegistry"] = None
    mcp_auto_refresh: bool = False


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
    # 工具是否声明了 ctx: Context 首参（由框架注入，不进 schema）
    wants_context: bool = False
    # 可选：按参数动态评估风险（如 run_bash 把只读命令降级为 READ）。
    # 风险在「这次调用」里，不总在「工具」上——返回值覆盖静态 risk。
    risk_assessor: Optional[Callable[[Dict[str, Any]], Risk]] = None


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
        if py_type is Context:          # 框架注入的上下文，不暴露给模型
            continue
        prop: Dict[str, Any] = _type_schema(py_type)
        if pname in param_descriptions:
            prop["description"] = param_descriptions[pname]
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {"type": "object", "properties": props, "required": required}


def _wants_context(func: Callable) -> bool:
    """工具是否把第一个参数声明为 ctx: Context（据此决定是否注入）。"""
    params = list(inspect.signature(func).parameters.values())
    if not params:
        return False
    hints = get_type_hints(func)
    return hints.get(params[0].name) is Context


# ---------------------------------------------------------------------------
# @tool 装饰器：加工具的唯一入口
# ---------------------------------------------------------------------------
def tool(
    risk: Risk = Risk.READ,
    *,
    name: Optional[str] = None,
    param_descriptions: Optional[Dict[str, str]] = None,
    override: bool = False,
    risk_assessor: Optional[Callable[[Dict[str, Any]], Risk]] = None,
) -> Callable:
    """把一个普通函数注册成工具。

    用法：
        @tool(risk=Risk.READ, param_descriptions={"path": "要读取的文件路径"})
        def read_file(path: str) -> str:
            '''读取指定路径的文件内容。'''
            ...

    description 取自函数 docstring；schema 自动从签名生成。
    返回原函数本身（不包装），因此工具仍可像普通函数一样被直接调用/测试。

    重名默认 raise（fail-fast）：注册表定义了模型的「感官」，静默覆盖会让
    模型可见的工具实现会取决于 import 顺序，是更隐蔽的故障。确为有意覆盖
    （插件替换内置工具等）时显式传 override=True。
    """
    def decorator(func: Callable) -> Callable:
        t = Tool(
            name=name or func.__name__,
            description=(func.__doc__ or "").strip(),
            parameters=_build_schema(func, param_descriptions or {}),
            func=func,
            risk=risk,
            wants_context=_wants_context(func),
            risk_assessor=risk_assessor,
        )
        if t.name in _REGISTRY and not override:
            raise ValueError(
                f"工具名 '{t.name}' 已注册；如确为有意覆盖请用 @tool(..., override=True)"
            )
        _REGISTRY[t.name] = t
        return func
    return decorator


# 内置工具实现在 noval/builtins.py（与框架分文件）。
