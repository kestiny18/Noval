"""执行管道测试：错误归一化、截断、确认门。"""
import json

from noval.config import Config
from noval.executor import execute_tool_call
from noval.tools import Risk, tool

BASE = dict(
    model="m", base_url="u", api_key_env="K", max_steps=5,
    max_tool_output_chars=100, auto_approve=["read", "write"],
    system_prompt="s",
)


def cfg(**over):
    d = dict(BASE)
    d.update(over)
    return Config(**d)


def test_unknown_tool_lists_available():
    r = execute_tool_call("nope", "{}", cfg())
    assert r.is_error
    assert "可用工具" in r.content


def test_invalid_json_is_error():
    r = execute_tool_call("read_file", "{not json", cfg())
    assert r.is_error and "JSON" in r.content


def test_missing_required_param():
    r = execute_tool_call("read_file", "{}", cfg())
    assert r.is_error and "缺少必填参数" in r.content


def test_tool_error_surfaced(tmp_path):
    r = execute_tool_call("read_file", json.dumps({"path": str(tmp_path / "x")}), cfg())
    assert r.is_error and "not found" in r.content      # ToolError 被转成可纠错结果


def test_truncation():
    @tool(name="_big")
    def big() -> str:
        """big"""
        return "x" * 500

    r = execute_tool_call("_big", "{}", cfg(max_tool_output_chars=100))
    assert r.truncated
    assert len(r.content) < 500 and "省略" in r.content
    assert r.meta["original_chars"] == 500


def test_internal_typeerror_not_mislabeled():
    @tool(name="_internal_te")
    def boom() -> str:
        """工具内部抛 TypeError"""
        return len(None)  # type: ignore[arg-type]

    r = execute_tool_call("_internal_te", "{}", cfg())
    assert r.is_error
    assert "签名不匹配" not in r.content       # 不能误报成参数错误
    assert "执行异常" in r.content             # 应走通用兜底


def test_signature_mismatch_reported():
    @tool(name="_needs_x")
    def f(x: str) -> str:
        """需要 x"""
        return x

    # 多传了签名里没有的参数 y → 绑定失败，应明确报「签名不匹配」
    r = execute_tool_call("_needs_x", '{"x": "a", "y": "b"}', cfg())
    assert r.is_error and "签名不匹配" in r.content


def test_confirmation_gate_denies_without_approver():
    @tool(name="_danger", risk=Risk.DANGEROUS)
    def danger() -> str:
        """danger"""
        return "did it"

    # dangerous 不在 auto_approve，且没传 approver → 默认拒绝
    r = execute_tool_call("_danger", "{}", cfg())
    assert r.is_error and "拒绝" in r.content

    # 传入放行的 approver → 执行成功
    r2 = execute_tool_call("_danger", "{}", cfg(), approver=lambda t, a: True)
    assert not r2.is_error and r2.content == "did it"
