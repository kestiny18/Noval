import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from noval.agent import _format_usage_summary, _handle_usage_command
from noval.client import MockClient, TokenUsage, mock_text
from noval.usage import JsonlUsageStore, MeteredLLMClient


NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def usage(prompt=100, completion=20, *, hit=None, miss=None, reasoning=None):
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        reasoning_tokens=reasoning,
    )


def test_jsonl_store_aggregates_daily_usage_and_models(tmp_path):
    first = JsonlUsageStore(tmp_path, "session-a", now=lambda: NOW)
    second = JsonlUsageStore(tmp_path, "session-b", now=lambda: NOW)
    first.record("deepseek-v4-pro", usage(hit=75, miss=25, reasoning=12))
    second.record("deepseek-chat", usage(50, 10, hit=0, miss=50, reasoning=0))

    summary = first.summarize(date(2026, 6, 30))

    assert summary.total.requests == 2
    assert summary.total.prompt_tokens == 150
    assert summary.total.completion_tokens == 30
    assert summary.total.total_tokens == 180
    assert summary.total.cache_hit_tokens == 75
    assert summary.total.cache_miss_tokens == 75
    assert summary.total.reasoning_tokens == 12
    assert set(summary.by_model) == {"deepseek-v4-pro", "deepseek-chat"}
    assert set(summary.by_purpose) == {"agent"}
    assert len(list((tmp_path / "2026-06-30").glob("*.jsonl"))) == 2
    event = json.loads(next((tmp_path / "2026-06-30").glob("*.jsonl")).read_text())
    assert event["schema_version"] == 1
    assert event["purpose"] == "agent"
    assert "workdir" not in event
    assert "session" not in event


def test_store_uses_actual_event_day_and_skips_corrupt_lines(tmp_path, caplog):
    current = [datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc)]
    store = JsonlUsageStore(tmp_path, "session", now=lambda: current[0])
    first_path = store.record("model", usage())
    current[0] = datetime(2026, 7, 1, 0, 1, tzinfo=timezone.utc)
    store.record("model", usage(10, 2))
    with first_path.open("a", encoding="utf-8") as file:
        file.write("not-json\n")
        file.write(json.dumps({"schema_version": 1, "model": "bad",
                               "prompt_tokens": -1, "completion_tokens": 2,
                               "total_tokens": 1}) + "\n")
        file.write(json.dumps({"schema_version": 1, "model": [],
                               "prompt_tokens": 1, "completion_tokens": 2,
                               "total_tokens": 3}) + "\n")

    june = store.summarize(date(2026, 6, 30))
    july = store.summarize(date(2026, 7, 1))

    assert june.total.requests == 1
    assert july.total.requests == 1
    assert "跳过损坏" in caplog.text


def test_metered_client_records_actual_response_model(tmp_path):
    response = mock_text("ok", usage=usage())
    response.raw = SimpleNamespace(model="provider-model")
    inner = MockClient([response])
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    client = MeteredLLMClient(inner, store, "configured-model")

    assert client.complete([], []).content == "ok"

    assert set(store.summarize().by_model) == {"provider-model"}


def test_usage_records_and_summarizes_purpose(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("main", usage(), purpose="agent")
    store.record("judge", usage(10, 2), purpose="completion_judge")

    summary = store.summarize()
    text = _format_usage_summary(summary)

    assert set(summary.by_purpose) == {"agent", "completion_judge"}
    assert summary.by_purpose["completion_judge"].requests == 1
    assert "按用途" in text
    assert "completion_judge" in text


def test_metered_client_records_configured_purpose(tmp_path):
    response = mock_text("ok", usage=usage())
    inner = MockClient([response])
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    client = MeteredLLMClient(
        inner, store, "judge-model", purpose="completion_judge"
    )

    assert client.complete([], []).content == "ok"

    event = json.loads(next((tmp_path / "2026-06-30").glob("*.jsonl")).read_text())
    assert event["purpose"] == "completion_judge"


def test_metering_failure_does_not_hide_model_response(caplog):
    class BrokenStore:
        def record(self, model, token_usage):
            raise OSError("disk full")

    response = mock_text("still works", usage=usage())
    client = MeteredLLMClient(MockClient([response]), BrokenStore(), "model")

    assert client.complete([], []).content == "still works"
    assert "持久化失败" in caplog.text


def test_usage_format_shows_cache_reasoning_and_multi_model(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("model-b", usage(hit=75, miss=25, reasoning=12))
    store.record("model-a", usage(50, 10, hit=0, miss=50, reasoning=0))

    text = _format_usage_summary(store.summarize())

    assert "今日 Token 使用 (2026-06-30)" in text
    assert "请求次数: 2" in text
    assert "缓存命中: 75 (50.0%)" in text
    assert "其中 reasoning: 12" in text
    assert "按模型:" in text
    assert text.index("model-a") < text.index("model-b")


def test_usage_command_is_local_exact_and_supports_disabled(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)

    assert "请求次数: 0" in _handle_usage_command("/usage", store)
    assert _handle_usage_command("/usage today", store) is None
    assert _handle_usage_command("question", store) is None
    assert _handle_usage_command("/usage", None) == "Token 统计已关闭。"


def test_single_model_without_optional_details_stays_compact(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("only-model", usage())

    text = _format_usage_summary(store.summarize())

    assert "缓存命中" not in text
    assert "reasoning" not in text
    assert "按模型:" not in text
