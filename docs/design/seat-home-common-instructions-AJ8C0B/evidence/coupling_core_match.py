#!/usr/bin/env python3
"""この設計が触ると宣言した path を、dotfiles の機械可読の 2 節と突き合わせる(読みだけ)。
書式の正本 = ACP rule R-watchlist-machine-section-93be(fence の info 文字列で選ぶ・
1 行 = 1 fnmatch glob・`#` = コメント・`!` = 核ではない宣言)。"""
import fnmatch, pathlib, sys

WATCHLIST = pathlib.Path.home() / "dotfiles" / "docs" / "coupling-core-watchlist.md"

TOUCHED = {
    "doeff": [
        "packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.pyi",
        "packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py",
        "packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py",
        "packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/headless.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy",
        ".semgrep.yaml",
        "docs/adr/enforcement-ledger.json",
        "packages/doeff-agents/tests/sessionhost_seat_home_instructions_deftests.hy",
        # 盲検 A の反例を受けて増えた面(R1 / R2 — substrate の語彙と atomic write)
        "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy",
        "packages/doeff-agents/src/doeff_agents/sessionhost/effects.pyi",
        "packages/doeff-agents/tests/sessionhost_launch_deftests.hy",
        "packages/doeff-agents/tests/sessionhost_substrate_deftests.hy",
        "packages/doeff-agents/conformance/test_s12_claude_trust_preseed.py",
    ],
    "dotfiles": [
        "cron_management/acp-single-mac.toml",
        "cron_management/acp-proboscis-mbp.toml",
        "agent/tests/test_acp_single_mac.py",
        "agent/tests/check_native_claude_home_contract.py",
        "agent/tests/test_check_native_claude_home_contract.py",
    ],
    "agent-control-plane": [
        "deploy/acp-control/agentd-pool-join.yaml",
    ],
}


def patterns(info: str) -> list[str]:
    text, out, inside = WATCHLIST.read_text(encoding="utf-8"), [], False
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                break
            inside = line[3:].strip() == info
            continue
        if not inside:
            continue
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        out.append(s)
    return out


def main() -> int:
    core, fleet = patterns("coupling-core-paths"), patterns("coupling-core-fleet-paths")
    if not core or not fleet:
        print("計器の不成立: fence が読めない", file=sys.stderr)
        return 2
    print(f"# watchlist: {WATCHLIST}")
    print(f"# coupling-core-paths: {len(core)} pattern / coupling-core-fleet-paths: {len(fleet)} pattern")
    hits = 0
    for repo, paths in TOUCHED.items():
        print(f"\n## {repo}")
        for p in paths:
            core_hit = [g for g in core if fnmatch.fnmatch(p, g)]
            fleet_hit = [g for g in fleet if fnmatch.fnmatch(p, g)]
            mark = "HIT" if (core_hit or fleet_hit) else "-  "
            hits += bool(core_hit or fleet_hit)
            extra = f"  core={core_hit} fleet={fleet_hit}" if (core_hit or fleet_hit) else ""
            print(f"  {mark} {p}{extra}")
    print(f"\n# 当たり: {hits} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
