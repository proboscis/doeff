"""Agent adapters for different coding agents."""

from .base import AgentAdapter, AgentType, InjectionMethod, LaunchConfig, LaunchParams
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter

__all__ = [
    "AgentAdapter",
    "AgentType",
    "ClaudeAdapter",
    "CodexAdapter",
    "GeminiAdapter",
    "InjectionMethod",
    "LaunchParams",
]
