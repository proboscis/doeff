#!/bin/bash
# 盲検 B の主張「責務違反を**綴りの家の中**に置けば静的検査は届かない」を撃つ。
# 共有 source は触らない — scratch の git tree に .semgrep.yaml を複写して撃つ。
set -u
CE="${1:?usage: verify_B_semgrep.sh <workdir>}"
rm -rf "$CE"; mkdir -p "$CE/packages/doeff-agents/src/doeff_agents/sessionhost/impls"
cd "$CE" && git init -q .
cp "$HOME/repos/doeff/.semgrep.yaml" "$CE/.semgrep.yaml"
cat > "$CE/packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy" <<'HY'
(setv CLAUDE-USER-MEMORY-FILE "CLAUDE.md")
(setv CLAUDE-USER-SKILLS-DIR "skills")
(setv CLAUDE-MD-EXCLUDES-SETTING "claudeMdExcludes")
;; ★ 盲検 B の違反: 宿の file layout(戻り先)を据え付け層が持つ
(setv CLAUDE-COMMON-MEMORY-FALLBACKS ["{home}/dotfiles/claude/CLAUDE.md"
                                      "{home}/.claude/CLAUDE.md"])
(defk claude-instruction-candidates [declared shapes]
  (setv out [])
  (when (and (isinstance declared str) (.strip declared)) (.append out (.strip declared)))
  (<- home (env-get "HOME"))
  (when (and (isinstance home str) (.strip home))
    (for [shape shapes] (.append out (.format shape :home home))))
  out)
HY
cat > "$CE/proposed.yaml" <<'YML'
rules:
  - id: doeff-agents-claude-md-excludes-spelling-has-one-home
    languages: [generic]
    severity: ERROR
    message: 家の中の綴りは sessionhost/impls/claude_code.hy の 1 か所。
    pattern-either:
      - pattern-regex: 'claudeMdExcludes'
      - pattern-regex: '"CLAUDE\.md"'
      - pattern-regex: '"skills"'
    paths:
      include: [/packages/doeff-agents/**]
      exclude: ["**/sessionhost/impls/claude_code.hy"]
YML
# 修正(R3a): 宿の checkout の layout の綴りを doeff の src から締め出す。除外を持たない。
cat > "$CE/fix.yaml" <<'YML'
rules:
  - id: doeff-agents-host-checkout-layout-is-never-spelled-here
    languages: [generic]
    severity: ERROR
    message: >
      宿の checkout の layout(dotfiles/…)は機体の参加宣言が名指す値で、doeff が
      知ってよい綴りではない。据え付け層が戻り先として持つと「宣言が 1 鍵も無い宿へ
      誰も宣言していない条文が届く」形になり、log は成功を名乗る。
    patterns:
      - pattern-regex: 'dotfiles'
    paths:
      include: [/packages/doeff-agents/src/**]
YML
git add -A
echo "## (1) 現行の全規則 → 違反例"
semgrep --metrics=off --disable-version-check --config .semgrep.yaml packages/ --error 2>&1 | grep -E "^Ran |findings"
echo "## (2) 当初 §6 が足すと宣言した規則(autoMemoryDirectory と同形)→ 違反例"
semgrep --metrics=off --disable-version-check --config proposed.yaml packages/ --error 2>&1 | grep -E "^Ran |findings"
echo "## (3) 陰性対照 — 同じ綴りを綴りの家の**外**に置くと (2) は発火するか"
mkdir -p "$CE/packages/doeff-agents/src/doeff_agents/sessionhost"
printf '(setv X "CLAUDE.md")\n' > "$CE/packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy"
git add -A
semgrep --metrics=off --disable-version-check --config proposed.yaml packages/ --error 2>&1 | grep -E "^Ran |findings|launch\.hy"
echo "## (4) 修正 R3a の規則 → 違反例(除外を持たないので綴りの家の中でも発火する)"
semgrep --metrics=off --disable-version-check --config fix.yaml packages/ --error 2>&1 | grep -E "^Ran |findings|claude_code\.hy|dotfiles"
echo "## (5) 陽性対照 — 違反の 2 行を消すと (4) は緑か"
python3 - "$CE" <<'PY'
import pathlib, sys, re
p = pathlib.Path(sys.argv[1])/"packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy"
s = p.read_text()
s = re.sub(r'(?s);; ★ 盲検 B の違反.*?"\{home\}/\.claude/CLAUDE\.md"\]\)\n', '', s)
p.write_text(s)
PY
git add -A
semgrep --metrics=off --disable-version-check --config fix.yaml packages/ --error 2>&1 | grep -E "^Ran |findings"
