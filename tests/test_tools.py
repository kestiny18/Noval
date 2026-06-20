"""工具层测试：自动 schema 生成 + 工具契约。"""
from typing import List, Optional

import pytest

from noval.tools import Risk, ToolError, get_tool, read_file, tool


def test_read_file_schema_auto_generated():
    t = get_tool("read_file")
    assert t is not None
    assert t.parameters["type"] == "object"
    assert t.parameters["properties"]["path"]["type"] == "string"   # 从 path: str 推导
    assert t.parameters["required"] == ["path"]                      # 无默认值 → required
    assert t.risk is Risk.READ


def test_required_inferred_from_defaults():
    @tool(name="_demo_defaults")
    def f(a: str, b: int = 3) -> str:
        """demo"""
        return f"{a}{b}"

    t = get_tool("_demo_defaults")
    assert t.parameters["required"] == ["a"]          # b 有默认值，不是必填
    assert t.parameters["properties"]["b"]["type"] == "integer"


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        @tool(name="read_file")
        def dup() -> str:
            """dup"""
            return ""


def test_override_allows_intentional_replacement():
    @tool(name="_ov")
    def a() -> str:
        """a"""
        return "a"

    @tool(name="_ov", override=True)         # 显式覆盖 → 放行
    def b() -> str:
        """b"""
        return "b"

    assert get_tool("_ov").func() == "b"


def test_read_file_raises_domain_errors(tmp_path):
    with pytest.raises(ToolError):
        read_file(str(tmp_path / "nope.txt"))         # 不存在 → 领域错误
    with pytest.raises(ToolError):
        read_file(str(tmp_path))                       # 是目录 → 领域错误


def test_read_file_happy_path(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    assert read_file(str(f)) == "hello"                # 装饰器返回原函数，可直接调用


def test_schema_handles_generics():
    @tool(name="_generics")
    def f(tags: List[str], note: Optional[str] = None) -> str:
        """泛型参数 schema 测试"""
        return ""

    props = get_tool("_generics").parameters
    assert props["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["properties"]["note"] == {"type": "string"}   # Optional 解包成底层类型
    assert props["required"] == ["tags"]                        # note 有默认值，非必填
