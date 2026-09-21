"""陽性対照(D10 doeff-agents-host-checkout-layout-is-never-spelled-here)。

宿の checkout の layout を綴る**7 通りの書き方**を 1 行ずつ置く。規則が当たるのは
7〜14 行ちょうど(この docstring の prose と、末尾の陰性対照には当たらない)。
⚠ この file は fixture で、走る code ではない(`make lint-semgrep` は doeff/ と packages/ だけを見る)。
"""
MEMORY = "dotfiles/claude/CLAUDE.md"
ABSOLUTE = "/home/kento/dotfiles/claude/CLAUDE.md"
TILDE = "~/dotfiles/agent/skills"
INTERPOLATED = f"{HOME}/dotfiles/agent/skills"
JOINED = os.path.join(HOME, "dotfiles", "claude", "CLAUDE.md")
DIVIDED = Path(HOME) / "dotfiles" / "agent"
SINGLE_QUOTED = 'dotfiles/claude/CLAUDE.md'
SEGMENT_HEAD = "dotfiles"

# 陰性対照 1(散文): dotfiles agentcli share.py と同じ形 — 註の中の dotfiles/claude は綴りではない
NOT_A_SEGMENT = "my-dotfiles/claude"
ALSO_NOT = "dotfiles-base/claude"
DOTTED = "acp.dotfiles/claude"
# 陰性対照 2(名指しで通す 1 件・card acp:kanban-issue:ki-6f8425e88733)
VERIFY_SCRIPTS_RELDIR = "dotfiles/cron_management"
