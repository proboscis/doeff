#!/bin/sh
# 設計検証 lt-Z4J4B9281EK68D2Q8PZAJVZAV5 の場面ごとの正常例・失敗例を、直しの branch(wt/vocab-item-by-rule-paths)の写しで撃つ。
# 本物の uv.lock の版の pre-commit と semgrep・本物の .semgrep.yaml を使う(検の偽の道具は使わない)。写しは /tmp の下に作り、共有の木は触らない。
# 使い方: sh checks.sh <直しの作業木> <証拠の dir>
set -u
SRC="${1:?直しの作業木}"
OUT="${2:?証拠の dir}"
BASE=$(mktemp -d /tmp/dc-checks-XXXXXX)
VOCAB='宣言の欄(agora の kanban の行)'
CLEAN='宣言の欄(#2830)'

fresh() { # fresh <名> — 直しの branch の写し(.venv 無し)を作り、根の path を出す
  r="$BASE/$1"
  git clone -q --shared "$SRC" "$r" || exit 1
  [ -d "$r/.git" ] || exit 1
  git -C "$r" config user.name t
  git -C "$r" config user.email t@example.invalid
  echo "$r"
}

hook() { # hook <根> — git が commit の時に撃つのと同じく、根で素の hook を撃つ
  (cd "$1" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit 2>&1)
}

install_hook() { # install_hook <根> — Pod の準備(worker-prepare.sh の 5.)と同じく hook を写す
  h="$1/$(git -C "$1" rev-parse --git-path hooks)"
  mkdir -p "$h" && cp "$1/scripts/git-hooks/pre-commit" "$h/pre-commit" && chmod +x "$h/pre-commit"
}

head_line() { echo "# $1"; date -u +%FT%TZ; echo "src HEAD = $(git -C "$SRC" rev-parse HEAD)"; }

# --- storage-venv: 作業木に .venv が無い ---
{
  head_line "storage-venv: .venv の無い写しで素の hook を撃つ"
  r=$(fresh venv-positive)
  echo ".venv の有無: $(test -e "$r/.venv" && echo 在る || echo 無い)"
  f=packages/doeff-cluster/src/doeff_cluster/design_check_example.hy
  printf ';; 起き直しの lease(card ki-0)\n(setv value 1)\n' > "$r/$f"; git -C "$r" add "$f"
  echo "## positive: 所見の無い Hy の註を stage"; hook "$r" | tail -8; echo "positive rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit >/dev/null 2>&1; echo $?)"
  r=$(fresh venv-negative)
  printf ';; 起き直しの lease(agora-controllers の card ki-0)\n(setv value 1)\n' > "$r/$f"; git -C "$r" add "$f"
  echo "## negative: 業務の語を足した Hy の註を stage"; out=$(hook "$r"); echo "$out" | tail -12
  echo "negative rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit >/dev/null 2>&1; echo $?)"
  echo "rule in output: $(echo "$out" | grep -c doeff-packages-have-no-application-vocabulary)"
} > "$OUT/check-storage-venv.log" 2>&1

# --- storage-index: stage した断面だけに当てる ---
{
  head_line "storage-index: stage した断面と作業木の中身が違う"
  f=packages/doeff-records/README.md
  r=$(fresh index-positive)
  printf '%s\n' "$CLEAN" >> "$r/$f"; git -C "$r" add "$f"; printf '%s\n' "$VOCAB" >> "$r/$f"
  echo "## positive: 所見の無い行を stage・業務の語の行は stage しない"
  echo "rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit >"$BASE/index-positive.out" 2>&1; echo $?)"; tail -6 "$BASE/index-positive.out"
  echo "hook の後の未 stage の差分(業務の語の行が残る事):"; git -C "$r" diff -- "$f" | grep '^+' | grep -v '^+++'
  r=$(fresh index-negative)
  printf '%s\n' "$VOCAB" >> "$r/$f"; git -C "$r" add "$f"; git -C "$r" show HEAD:"$f" > "$r/$f"
  echo "## negative: 業務の語の行を stage・作業木では消した(未 stage)"
  echo "rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit >"$BASE/index-negative.out" 2>&1; echo $?)"; tail -10 "$BASE/index-negative.out"
  echo "rule in output: $(grep -c doeff-packages-have-no-application-vocabulary "$BASE/index-negative.out")"
} > "$OUT/check-storage-index.log" 2>&1

# --- effects-tool: 道具を走らせられない ---
{
  head_line "effects-tool: uv を PATH から外す"
  f=packages/doeff-records/README.md
  r=$(fresh tool-positive)
  printf '%s\n' "$CLEAN" >> "$r/$f"; git -C "$r" add "$f"
  echo "## positive: 普段の PATH(uv = $(command -v uv))"
  echo "rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 sh scripts/git-hooks/pre-commit >"$BASE/tool-positive.out" 2>&1; echo $?)"; tail -4 "$BASE/tool-positive.out"
  r=$(fresh tool-negative)
  printf '%s\n' "$CLEAN" >> "$r/$f"; git -C "$r" add "$f"
  echo "## negative: PATH=/usr/bin:/bin(uv が無い)"
  echo "rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP PATH=/usr/bin:/bin timeout 120 sh scripts/git-hooks/pre-commit >"$BASE/tool-negative.out" 2>&1; echo $?)"; cat "$BASE/tool-negative.out"
} > "$OUT/check-effects-tool.log" 2>&1

# --- distribution-rules: 規則に package を足し、項と Pod の hook の写しは変えない ---
{
  head_line "distribution-rules: 規則の paths.include に package を足す(項・hook の写しは不変)"
  for case in negative positive outside; do
    r=$(fresh "rules-$case")
    install_hook "$r"
    sed -i 's|^        - "/packages/doeff-codex/\*\*"$|&\n        - "/packages/doeff-example-generic/**"|' "$r/.semgrep.yaml"
    git -C "$r" commit -q --no-verify -am "規則に package を足す(準備 — 検の対象ではない)"
    echo "## $case: 規則の include = $(grep -c '/packages/doeff-example-generic/' "$r/.semgrep.yaml") 行 / 項の files = $(grep -n "files: '^(doeff|packages)/'" "$r/.pre-commit-config.yaml" | cut -d: -f1) 行目"
    case $case in
      negative) f=packages/doeff-example-generic/README.md; t="$VOCAB" ;;
      positive) f=packages/doeff-example-generic/README.md; t="$CLEAN" ;;
      outside) f=packages/doeff-example-app/README.md; t="$VOCAB" ;;
    esac
    mkdir -p "$r/$(dirname "$f")"; printf '%s\n' "$t" > "$r/$f"; git -C "$r" add "$f"
    before=$(git -C "$r" rev-list --count HEAD)
    rc=$(cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 git commit -qm "$case" >"$BASE/rules-$case.out" 2>&1; echo $?)
    echo "stage = $f / 文 = $t / git commit rc=$rc / HEAD の数 $before → $(git -C "$r" rev-list --count HEAD)"
    grep -E "semgrep\(|doeff-packages-have-no-application-vocabulary|Failed|Passed" "$BASE/rules-$case.out" | cut -c1-200
  done
} > "$OUT/check-distribution-rules.log" 2>&1

# --- concurrency: 同じ clone の 2 つの作業木で同時に commit ---
{
  head_line "concurrency: 同じ clone の 2 つの作業木(hook の写しは共有の 1 つ)で同時に git commit"
  r=$(fresh concurrency)
  install_hook "$r"
  git -C "$r" worktree add -q -b other "$BASE/concurrency-other" HEAD || echo "worktree add に失敗"
  o="$BASE/concurrency-other"
  echo "hook の置き場: $(git -C "$r" rev-parse --git-path hooks) / $(git -C "$o" rev-parse --git-path hooks)"
  run_pair() { # run_pair <名> <r の文> <o の文>
    f1=packages/doeff-records/README.md; f2=packages/doeff-cluster/README.md
    git -C "$r" reset -q --hard; git -C "$o" reset -q --hard
    printf '%s\n' "$2" >> "$r/$f1"; git -C "$r" add "$f1"
    mkdir -p "$o/packages/doeff-cluster"; printf '%s\n' "$3" >> "$o/$f2"; git -C "$o" add "$f2"
    b1=$(git -C "$r" rev-list --count HEAD); b2=$(git -C "$o" rev-list --count HEAD)
    (cd "$r" && env -u VIRTUAL_ENV -u SKIP timeout 120 git commit -qm "$1 r" >"$BASE/$1-r.out" 2>&1; echo $? > "$BASE/$1-r.rc") &
    (cd "$o" && env -u VIRTUAL_ENV -u SKIP timeout 120 git commit -qm "$1 o" >"$BASE/$1-o.out" 2>&1; echo $? > "$BASE/$1-o.rc") &
    wait
    echo "## $1: r(文 = $2)rc=$(cat "$BASE/$1-r.rc") HEAD $b1 → $(git -C "$r" rev-list --count HEAD) / o(文 = $3)rc=$(cat "$BASE/$1-o.rc") HEAD $b2 → $(git -C "$o" rev-list --count HEAD)"
    grep -h "doeff-packages-have-no-application-vocabulary" "$BASE/$1-r.out" "$BASE/$1-o.out" | head -2
  }
  run_pair positive "$CLEAN" "$CLEAN"
  run_pair negative "$VOCAB" "$CLEAN"
} > "$OUT/check-concurrency.log" 2>&1

echo "写しの置き場: $BASE"
