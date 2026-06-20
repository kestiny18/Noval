"""测试隔离：每个用例前后快照/还原全局工具注册表。

工具注册表是进程级全局状态。测试里 @tool 注册的临时工具(_big/_danger 等)若不清理，
会污染后续用例，且在同进程内重跑会撞上「重复注册」的 ValueError。这里自动兜住。
"""
import pytest

from noval import tools


@pytest.fixture(autouse=True)
def _isolate_tool_registry():
    snapshot = dict(tools._REGISTRY)
    try:
        yield
    finally:
        tools._REGISTRY.clear()
        tools._REGISTRY.update(snapshot)
