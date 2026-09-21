#!/bin/bash
# 据わっている Claude Code 本体の実行体から、この設計が依っている契約の逐語を読む(純読み)。
# 先例 = dotfiles agent/tests/check_native_worktree_contract.py(据わっている本体から逐語を読み、
# 契約が動いた日に赤くする計器)。ここは設計段の記録なので判定はせず、逐語をそのまま出す。
set -u
B="${1:-/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe}"
echo "### body: $B"
echo "### version: $(claude --version 2>&1)"
echo "### sha256: $(sha256sum "$B" | cut -d' ' -f1)"
echo
echo "## (1) 設定の家の定義点 — CLAUDE_CONFIG_DIR ?? ~/.claude"
grep -a -o -E 'function s\(\)\{return process\.env\.CLAUDE_CONFIG_DIR\}.{0,90}' "$B" | head -1
echo
echo "## (2) 4 層の記憶の file の座 — User は設定の家の下"
grep -a -o -E 'function dQ\(e\)\{let t=he\(\);switch\(e\)\{case"User":.{0,160}' "$B" | head -1
echo
echo "## (3) user 層の記憶の読みの呼び口(includeExternal は真固定・storageV5 を渡す)"
grep -a -o -E 'Or..userSettings...\{let Pe=dQ..User..;.{0,210}' "$B" | head -1
echo
echo "## (4) ⚠ user 層だけの symlink / hard link の門(ここが反例の座)"
grep -a -o -E 'if\(t==="User"&&!v\)try\{.{0,140}' "$B" | head -1
echo "     v の定義:"
grep -a -o -E 'let v=o&&\(t!=="User"\|\|wgr\(\)\);.{0,60}' "$B" | head -1
echo "     wgr の定義(CN() = CLAUDE_CODE_ENTRYPOINT):"
grep -a -o -E 'function wgr\(\)\{return CN\(\)!=="local-agent"\}' "$B" | head -1
grep -a -o -E 'function CN\(\)\{return o\(\)\.entrypoint\}' "$B" | head -1
echo
echo "## (5) user 層の skills の座 — 設定の家の下 / managed は /etc の下"
grep -a -o -E 'let r=Ah\(Se\(\),"skills"\),o=Ah\(HS\(\),"\.claude","skills"\).{0,120}' "$B" | head -1
echo "     skills の entry は symlink を明示で受ける:"
grep -a -o -E 'if\(!N\.isDirectory\(\)&&!N\.isSymbolicLink\(\)\)return null;.{0,80}' "$B" | head -1
echo
echo "## (6) 除外の鍵 claudeMdExcludes(User / Project / Local に効く)"
grep -a -o -E 'function Sgr\(e,t\)\{if\(t!=="User"&&t!=="Project"&&t!=="Local"\)return!1;.{0,120}' "$B" | head -1
echo
echo "## (7) 退けた候補 A — skillsDirs は CLAUDE_MEMORY_STORES の team mount 専用"
grep -a -o -E 'skillsDirs:k\(us\(\)\.refine\(\(e\)=>e\.split\("/"\)\.at\(-1\)==="skills".{0,110}' "$B" | head -1
grep -a -o -E 'x\(N\.mount,"skillsDirs is team-store only"\).{0,40}' "$B" | head -1
echo
echo "## (8) 退けた候補 B — CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD は Project 層でしか載らない"
grep -a -o -E 'if\(xe\(a\.CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD\)\)\{.{0,170}' "$B" | head -1
echo
echo "### done"
