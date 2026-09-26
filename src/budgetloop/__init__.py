"""budgetloop: a minimal agent loop that treats context as a budget, not a bucket.

Built for the "First contact" and "Context engineering" labs: small enough to
read end to end in one sitting, strict enough that its guarantees are testable.
"""

from .context import CompactionEvent, ContextOverflow, ContextWindow, LedgerEntry
from .loop import DEFAULT_SYSTEM_PROMPT, AgentLoop, RunResult, RunStatus, StepRecord
from .messages import (
    Message,
    ModelReply,
    Role,
    ToolCall,
    estimate_tokens,
    system_message,
    tool_message,
    user_message,
)
from .providers import (
    AnthropicProvider,
    EchoProvider,
    Provider,
    ProviderError,
    ScriptedProvider,
    final_reply,
    tool_call_reply,
    to_anthropic_messages,
)
from .tools import (
    ApprovalDenied,
    SandboxViolation,
    Tool,
    ToolError,
    ToolRegistry,
    Workspace,
    default_registry,
    demo_registry,
)

__version__ = "0.1.0"

__all__ = [
    "AgentLoop",
    "AnthropicProvider",
    "ApprovalDenied",
    "CompactionEvent",
    "ContextOverflow",
    "ContextWindow",
    "DEFAULT_SYSTEM_PROMPT",
    "EchoProvider",
    "LedgerEntry",
    "Message",
    "ModelReply",
    "Provider",
    "ProviderError",
    "Role",
    "RunResult",
    "RunStatus",
    "SandboxViolation",
    "ScriptedProvider",
    "StepRecord",
    "Tool",
    "ToolCall",
    "ToolError",
    "ToolRegistry",
    "Workspace",
    "default_registry",
    "demo_registry",
    "estimate_tokens",
    "final_reply",
    "system_message",
    "to_anthropic_messages",
    "tool_call_reply",
    "tool_message",
    "user_message",
    "__version__",
]
