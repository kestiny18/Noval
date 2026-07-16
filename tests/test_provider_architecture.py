from pathlib import Path


CORE_FILES = (
    "agent.py",
    "context.py",
    "session.py",
    "task.py",
    "usage.py",
)
WIRE_KEYS = (
    '"assistant_message"',
    ".assistant_message",
    "tool_call_id",
    "reasoning_content",
    '"tool_calls"',
)


def test_core_modules_do_not_reference_provider_wire_keys():
    root = Path(__file__).parents[1] / "noval"
    violations = []
    for name in CORE_FILES:
        text = (root / name).read_text(encoding="utf-8")
        for key in WIRE_KEYS:
            if key in text:
                violations.append(f"{name}: {key}")

    assert violations == []
