from __future__ import annotations

import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from noval import (
    NovalRuntime,
    SessionOptions,
    SessionPersistence,
    TurnRequest,
)
from noval.client import MockClient, mock_text
from noval.config import Config


class DemoClientFactory:
    def __init__(self):
        self._next = iter(("alpha", "beta"))
        self._lock = threading.Lock()

    def __call__(self, spec):
        if spec.purpose == "completion_judge":
            return MockClient([])
        with self._lock:
            label = next(self._next)
        return MockClient([mock_text(f"reply from {label}")])


def demo_config(root: Path) -> Config:
    return Config(
        model="demo-agent",
        judge_model="demo-judge",
        base_url="https://example.invalid",
        api_key_env="UNUSED_DEMO_KEY",
        max_steps=4,
        max_tool_output_chars=2000,
        persist_sessions=False,
        persist_logs=False,
        persist_usage=False,
        sessions_dir_setting=str(root / "sessions"),
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="noval-headless-") as temporary:
        root = Path(temporary)
        alpha_dir = root / "alpha"
        beta_dir = root / "beta"
        alpha_dir.mkdir()
        beta_dir.mkdir()
        events = []
        event_lock = threading.Lock()

        def observe(event):
            with event_lock:
                events.append(event)

        with NovalRuntime(
            demo_config(root),
            client_factory=DemoClientFactory(),
            event_sink=observe,
        ) as runtime:
            alpha = runtime.create_session(SessionOptions(
                workdir=str(alpha_dir),
                persistence=SessionPersistence.EPHEMERAL,
            ))
            beta = runtime.create_session(SessionOptions(
                workdir=str(beta_dir),
                persistence=SessionPersistence.EPHEMERAL,
            ))
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(alpha.run_turn, TurnRequest("hello alpha")),
                    executor.submit(beta.run_turn, TurnRequest("hello beta")),
                ]
                results = [future.result() for future in futures]
            alpha.rename("Alpha demo")
            consumer_state = {
                "session": alpha.info.to_dict(),
                "transcript": alpha.transcript(limit=100).to_dict(),
                "event_replay": alpha.replay_events(limit=100).to_dict(),
            }

        print(json.dumps(
            {
                "results": [result.to_dict() for result in results],
                "events": [event.to_dict() for event in events],
                "consumer_state": consumer_state,
            },
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
