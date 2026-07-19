"""Tool framework tests for schema generation, Context injection, and overrides."""
from typing import List, Optional

import pytest

from noval.tools import Context, Risk, get_tool, tool


def test_schema_auto_generated_and_required_from_defaults():
    @tool(name="_demo")
    def f(a: str, b: int = 3) -> str:
        """demo"""
        return f"{a}{b}"

    t = get_tool("_demo")
    assert t.parameters["properties"]["a"]["type"] == "string"
    assert t.parameters["properties"]["b"]["type"] == "integer"   # int → integer
    assert t.parameters["required"] == ["a"]                        # b has a default.


def test_context_param_excluded_from_schema():
    @tool(name="_ctx_tool")
    def f(ctx: Context, path: str) -> str:
        """needs ctx"""
        return path

    t = get_tool("_ctx_tool")
    assert t.wants_context is True
    assert "ctx" not in t.parameters["properties"]                  # Framework-injected.
    assert list(t.parameters["properties"].keys()) == ["path"]
    assert t.parameters["required"] == ["path"]


def test_duplicate_registration_rejected():
    @tool(name="_dup")
    def a() -> str:
        """a"""
        return ""

    with pytest.raises(ValueError):
        @tool(name="_dup")
        def b() -> str:
            """b"""
            return ""


def test_override_allows_intentional_replacement():
    @tool(name="_ov")
    def a() -> str:
        """a"""
        return "a"

    @tool(name="_ov", override=True)
    def b() -> str:
        """b"""
        return "b"

    assert get_tool("_ov").func() == "b"


def test_schema_handles_generics():
    @tool(name="_generics")
    def f(tags: List[str], note: Optional[str] = None) -> str:
        """generics"""
        return ""

    props = get_tool("_generics").parameters
    assert props["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["properties"]["note"] == {"type": "string"}        # Optional is unwrapped.
    assert props["required"] == ["tags"]
