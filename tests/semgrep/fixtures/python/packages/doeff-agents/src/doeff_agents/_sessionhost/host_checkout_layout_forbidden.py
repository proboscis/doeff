"""Positive control (D10 doeff-agents-host-checkout-layout-is-never-spelled-here).

Puts **7 ways of spelling** the host checkout layout, one per line. The rule matches exactly
lines 7-14 (not the prose of this docstring, nor the negative controls at the end).
WARNING: this file is a fixture, not code that runs (`make lint-semgrep` only looks at doeff/ and packages/).
"""
MEMORY = "dotfiles/claude/CLAUDE.md"
ABSOLUTE = "/home/user/dotfiles/claude/CLAUDE.md"
TILDE = "~/dotfiles/agent/skills"
INTERPOLATED = f"{HOME}/dotfiles/agent/skills"
JOINED = os.path.join(HOME, "dotfiles", "claude", "CLAUDE.md")
DIVIDED = Path(HOME) / "dotfiles" / "agent"
SINGLE_QUOTED = 'dotfiles/claude/CLAUDE.md'
SEGMENT_HEAD = "dotfiles"

# Negative control 1 (prose): the same shape as dotfiles agentcli share.py — dotfiles/claude inside a comment is not a spelling
NOT_A_SEGMENT = "my-dotfiles/claude"
ALSO_NOT = "dotfiles-base/claude"
DOTTED = "acp.dotfiles/claude"
# Negative control 2 (one case let through by name)
VERIFY_SCRIPTS_RELDIR = "dotfiles/cron_management"
