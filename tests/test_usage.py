import json
from datetime import date, datetime, timezone

from noval.agent import _format_usage_summary, _handle_usage_command
from noval.client import (
    LLMStreamEvent,
    MockClient,
    ProviderIdentity,
    TokenUsage,
    mock_text,
)
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
    assert event["schema_version"] == 2
    assert event["event_type"] == "model_usage"
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
    assert "skipping corrupt" in caplog.text


def test_store_reads_legacy_usage_events(tmp_path):
    day_dir = tmp_path / "2026-06-30"
    day_dir.mkdir()
    (day_dir / "legacy.jsonl").write_text(
        json.dumps({
            "schema_version": 1,
            "timestamp": NOW.isoformat(),
            "model": "legacy-model",
            "purpose": "agent",
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "total_tokens": 10,
        }) + "\n",
        encoding="utf-8",
    )

    summary = JsonlUsageStore(tmp_path, now=lambda: NOW).summarize()

    assert summary.total.total_tokens == 10
    assert summary.by_model["legacy-model"].total_tokens == 10


def test_analytics_returns_all_time_summaries_and_364_daily_model_buckets(tmp_path):
    current = [datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)]
    store = JsonlUsageStore(tmp_path, "session", now=lambda: current[0])
    store.record("model-a", usage(5, 2))
    store.record_turn("model-a", 8_000)
    current[0] = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    store.record("model-a", usage(100, 20))
    store.record("model-b", usage(50, 10))
    store.record_turn("model-b", 2_500)
    current[0] = NOW
    store.record("model-a", usage(10, 2))
    store.record_turn("model-a", 5_000)

    analytics = store.analytics()

    assert analytics.window_start == "2025-07-02"
    assert analytics.window_end == "2026-06-30"
    assert len(analytics.days) == 364
    assert analytics.days[0].day == analytics.window_start
    assert analytics.days[-1].day == analytics.window_end
    assert analytics.total_tokens == 199
    assert analytics.peak_daily_tokens == 180
    assert analytics.longest_turn_duration_ms == 8_000
    assert [item.model for item in analytics.models] == ["model-a", "model-b"]
    assert analytics.models[0].total_tokens == 139
    assert analytics.models[0].peak_daily_tokens == 120
    assert analytics.models[0].longest_turn_duration_ms == 8_000
    june_29 = next(item for item in analytics.days if item.day == "2026-06-29")
    assert june_29.total_tokens == 180
    assert [(item.model, item.total_tokens) for item in june_29.by_model] == [
        ("model-a", 120),
        ("model-b", 60),
    ]


def test_turn_event_contains_only_safe_analytics_fields(tmp_path):
    path = JsonlUsageStore(tmp_path, "private-session", now=lambda: NOW).record_turn(
        "deepseek-v4-pro",
        1_234.6,
    )

    event = json.loads(path.read_text(encoding="utf-8"))

    assert event == {
        "schema_version": 2,
        "event_type": "turn",
        "timestamp": "2026-06-30T12:00:00+00:00",
        "model": "deepseek-v4-pro",
        "duration_ms": 1235,
    }
    assert "session" not in event
    assert "workdir" not in event


def test_metered_client_records_actual_response_model(tmp_path):
    response = mock_text("ok", usage=usage())
    response.provider = ProviderIdentity("test", "provider-model", "test")
    inner = MockClient([response])
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    client = MeteredLLMClient(inner, store, "configured-model")

    assert client.complete([], []).message.text == "ok"

    assert set(store.summarize().by_model) == {"provider-model"}


def test_metered_client_preserves_streaming_and_records_usage(tmp_path):
    response = mock_text("live", usage=usage())

    class StreamingClient:
        def complete(self, messages, tools):
            raise AssertionError("streaming capability should be selected")

        def stream_complete(self, messages, tools, on_event):
            on_event(LLMStreamEvent("live"))
            return response

    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    client = MeteredLLMClient(StreamingClient(), store, "configured-model")
    deltas = []

    result = client.stream_complete([], [], deltas.append)

    assert result.message.text == "live"
    assert deltas == [LLMStreamEvent("live")]
    assert store.summarize().total.requests == 1


def test_usage_records_and_summarizes_purpose(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("main", usage(), purpose="agent")
    store.record("judge", usage(10, 2), purpose="completion_judge")

    summary = store.summarize()
    text = _format_usage_summary(summary)

    assert set(summary.by_purpose) == {"agent", "completion_judge"}
    assert summary.by_purpose["completion_judge"].requests == 1
    assert "By purpose" in text
    assert "completion_judge" in text


def test_metered_client_records_configured_purpose(tmp_path):
    response = mock_text("ok", usage=usage())
    inner = MockClient([response])
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    client = MeteredLLMClient(
        inner, store, "judge-model", purpose="completion_judge"
    )

    assert client.complete([], []).message.text == "ok"

    event = json.loads(next((tmp_path / "2026-06-30").glob("*.jsonl")).read_text())
    assert event["purpose"] == "completion_judge"


def test_metering_failure_does_not_hide_model_response(caplog):
    class BrokenStore:
        def record(self, model, token_usage):
            raise OSError("disk full")

    response = mock_text("still works", usage=usage())
    client = MeteredLLMClient(MockClient([response]), BrokenStore(), "model")

    assert client.complete([], []).message.text == "still works"
    assert "failed to persist token usage" in caplog.text


def test_usage_format_shows_cache_reasoning_and_multi_model(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("model-b", usage(hit=75, miss=25, reasoning=12))
    store.record("model-a", usage(50, 10, hit=0, miss=50, reasoning=0))

    text = _format_usage_summary(store.summarize())

    assert "Token usage today (2026-06-30)" in text
    assert "Requests: 2" in text
    assert "Cache hits: 75 (50.0%)" in text
    assert "Reasoning: 12" in text
    assert "By model:" in text
    assert text.index("model-a") < text.index("model-b")


def test_usage_command_is_local_exact_and_supports_disabled(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)

    assert "Requests: 0" in _handle_usage_command("/usage", store)
    assert _handle_usage_command("/usage today", store) is None
    assert _handle_usage_command("question", store) is None
    assert _handle_usage_command("/usage", None) == "Token usage tracking is disabled."


def test_single_model_without_optional_details_stays_compact(tmp_path):
    store = JsonlUsageStore(tmp_path, "session", now=lambda: NOW)
    store.record("only-model", usage())

    text = _format_usage_summary(store.summarize())

    assert "Cache hits" not in text
    assert "reasoning" not in text
    assert "By model:" not in text
