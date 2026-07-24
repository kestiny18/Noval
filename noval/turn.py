"""Immutable model bindings captured for one admitted Turn."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .client import LLMClient
from .messages import ReplayScope


@dataclass(frozen=True)
class TurnModelBinding:
    configured_model_id: str
    connection_id: str
    adapter: str
    provider_model: str
    transport_revision: int
    configuration_revision: int
    replay_scope: ReplayScope


@dataclass(frozen=True)
class TurnExecution:
    """Clients and safe identities that remain fixed for a complete Turn."""

    agent: TurnModelBinding
    judge: TurnModelBinding
    agent_client: LLMClient = field(repr=False, compare=False)
    judge_client: LLMClient = field(repr=False, compare=False)
    release_transports: Callable[[], None] = field(
        repr=False,
        compare=False,
    )
