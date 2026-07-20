"""Noval — a small, general-purpose agent core."""
# Import builtins for the side effect of registering built-in @tool functions.
from . import builtins as _builtins  # noqa: F401

from .api import (  # noqa: F401
    API_SCHEMA_VERSION,
    AcceptanceCriterion,
    ActionReceipt,
    ApiFormatError,
    CompletionReport,
    CompletionStatus,
    CriterionReport,
    CriterionStatus,
    ErrorInfo,
    EvidenceOutcome,
    EventType,
    GoalContract,
    NovalError,
    PermissionDecision,
    PermissionRequest,
    PermissionStateView,
    RequestInspection,
    RuntimeEvent,
    RuntimeOptions,
    ReceiptKind,
    ReceiptOutcome,
    SemanticAssessment,
    SessionInfo,
    SessionOptions,
    SessionPersistence,
    StopReason,
    TurnMetrics,
    TurnRequest,
    TurnResult,
    TurnStatus,
    VerificationResult,
)
from .application import (  # noqa: F401
    AgentSession,
    ClientFactory,
    ClientSpec,
    EventSink,
    NovalRuntime,
    PermissionHandler,
)
from .permissions import PermissionMode  # noqa: F401
