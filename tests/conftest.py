"""Isolate tests by snapshotting and restoring the process-global tool registry.

Temporary tools registered by tests must not leak into later cases or collide
with registrations when the suite is rerun in the same process.
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
