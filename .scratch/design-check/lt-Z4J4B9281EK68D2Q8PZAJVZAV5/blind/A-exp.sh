#!/bin/sh
# 使い捨ての検の repo を組み、素の hook(scripts/git-hooks/pre-commit の写し)を作業木の根で撃つ。
# 引数: <case 名> <stage する path> <hook-items の files の regex を新しい package まで広げるか(yes|no)>
set -u
SRC=/home/ubuntu/.worktrees/doeff-wt-design-check-staged-semgrep
T=/tmp/dc-vocab-bes750
NAME="$1"; CHANGED="$2"; WIDEN="$3"
R="$T/$NAME"
rm -rf "$R"; mkdir -p "$R/scripts/git-hooks" "$R/scripts/hook_finding_baseline"
git init -q "$R"
cp "$SRC/scripts/git-hooks/pre-commit" "$R/scripts/git-hooks/pre-commit"
cp "$SRC/scripts/precommit_locked.py" "$SRC/scripts/semgrep_locked.py" "$SRC/scripts/hook_finding_baseline.py" "$R/scripts/"
cp "$SRC/.pre-commit-config.yaml" "$R/.pre-commit-config.yaml"
# 将来の変更(rules 側): 業務の語の規則だけを写し、paths.include に 5 つ目の汎用の package を足す。
{ echo "rules:"; sed -n '312,335p' "$SRC/.semgrep.yaml" \
  | sed 's|^        - "/packages/doeff-codex/\*\*"$|&\n        - "/packages/doeff-core-effects/**"|'; } > "$R/.semgrep.yaml"
printf 'version = 1\n\n[[package]]\nname = "semgrep"\nversion = "1.169.0"\n\n[[package]]\nname = "pre-commit"\nversion = "4.6.2"\n' > "$R/uv.lock"
printf '{"version": "1.169.0", "counts": {}}\n' > "$R/scripts/hook_finding_baseline/semgrep.json"
git -C "$R" add -A
git -C "$R" -c user.name=t -c user.email=t@example.invalid commit -qm base --no-verify
if [ "$WIDEN" = yes ]; then
  sed -i "s#files: '^packages/doeff-(cluster|records|claude-code|codex)/'#files: '^packages/doeff-(cluster|records|claude-code|codex|core-effects)/'#" "$R/.pre-commit-config.yaml"
  git -C "$R" add .pre-commit-config.yaml
fi
mkdir -p "$R/$(dirname "$CHANGED")"
printf '宣言の欄(agora の kanban の行)\n' > "$R/$CHANGED"
git -C "$R" add "$CHANGED"
echo "== $NAME: stage = $CHANGED, hook-items を広げた = $WIDEN"
echo "-- 規則の paths.include:"; grep -n '/packages/' "$R/.semgrep.yaml"
echo "-- hook-items の files:"; grep -n "files: '^packages/doeff-" "$R/.pre-commit-config.yaml"
(cd "$R" && env -u VIRTUAL_ENV -u SKIP PRE_COMMIT_HOME="$T/pc-home" timeout 60 sh scripts/git-hooks/pre-commit) > "$T/$NAME.hook.log" 2>&1
echo "素の hook の exit = $?"
echo "-- hook の出力(業務の語の規則と項の行):"
grep -nE 'semgrep|doeff-packages-have-no-application-vocabulary|Passed|Failed|Skipped|no files' "$T/$NAME.hook.log" | cut -c1-200
echo "-- 日次と同じく packages/ を丸ごと撃った時(--config .semgrep.yaml packages/ --error):"
(cd "$R" && timeout 60 uv tool run --python 3.12 --from semgrep==1.169.0 semgrep --metrics=off --disable-version-check --quiet \
   --config .semgrep.yaml packages/ --error --json) > "$T/$NAME.daily.json" 2>/dev/null
echo "日次相当の exit = $?"
uv run --no-project --python 3.12 python -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print([(r["check_id"].split(".")[-1], r["path"]) for r in d["results"]])' "$T/$NAME.daily.json"
