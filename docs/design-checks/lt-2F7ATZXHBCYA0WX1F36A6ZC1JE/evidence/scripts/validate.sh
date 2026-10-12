#!/bin/bash
# 直した版(作業木の HEAD)に、正常例と失敗例の変更を 1 つずつ当てて守りの検を走らせ、当てた分だけを逆当てで戻す。
set -u
WT=/home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a
EV=$WT/docs/design-checks/lt-2F7ATZXHBCYA0WX1F36A6ZC1JE/evidence
P=packages/doeff-cluster
M=CLAUDE_PROJECT_DIR
cd "$WT"
echo "版: $(git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a rev-parse HEAD)"
guard() { uv run pytest $P/tests/test_worker_pass_env.hy $P/tests/test_job_launch.hy -q -p no:cacheprovider 2>&1 | grep -E "^E +AssertionError|^E +assert|passed|failed" | sort -u | head -6; echo "exit ${PIPESTATUS[0]}"; }
undo() { git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a diff -- $P > "$1"; git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a apply -R "$1"; echo "戻した後の差: $(git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a status --short $P | wc -l) 行"; }
probe() { env NODE_NAME=n1 WORKER_NAME=w1 WORKER_HOST_SYSTEMD_ROOT=/host WORK_DIR=/work KUBERNETES_SERVICE_HOST=10.0.0.1 KUBERNETES_SERVICE_PORT=443 \
          $M=/work uv run python "$EV/blind-B/probe/compose_child_env.py" "$1" 2>&1 | tail -1 | grep -o '"pass_env": "[^"]*"\|"env_mode": "[^"]*"\|"marker_in_child_env": [a-z]*\|"worker_boot": "[^"]*"' | tr '\n' ' '; echo; }
T="NODE_NAME,WORKER_NAME,WORKER_HOST_SYSTEMD_ROOT,WORK_DIR,KUBERNETES_SERVICE_HOST,KUBERNETES_SERVICE_PORT"
mkdir -p "$EV/mutations"

echo "=== P0 直した版のまま"; guard

echo "=== P-hw 機体の dir を足す(この名を書かない env の patch)"
mkdir -p $P/deploy/k8s/nodes/zz-probe && printf -- '- op: add\n  path: /spec/template/spec/containers/0/env/-\n  value: { name: WORKER_CAPACITY, value: "2" }\n' > $P/deploy/k8s/nodes/zz-probe/env.yaml
guard; rm -r $P/deploy/k8s/nodes/zz-probe
echo "=== N-hw 機体の dir を足す(この名を env に書く patch)"
mkdir -p $P/deploy/k8s/nodes/zz-probe && printf -- '- op: add\n  path: /spec/template/spec/containers/0/env/-\n  value: { name: %s, value: /work }\n' $M > $P/deploy/k8s/nodes/zz-probe/env.yaml
guard; rm -r $P/deploy/k8s/nodes/zz-probe

echo "=== N-tmpl worker.yaml の Pod の env にこの名を置く"
sed -i "s|            - { name: ROLE, value: worker }|            - { name: ROLE, value: worker }\n            - { name: $M, value: /work }|" $P/deploy/k8s/worker/worker.yaml
guard; undo "$EV/mutations/N-tmpl.patch"
echo "=== N-boot boot.sh で export する"
sed -i "0,/^set -eu\$/s||set -eu\nexport $M=\"\${WORK_DIR:-/work}\"|" $P/deploy/boot.sh
guard; undo "$EV/mutations/N-boot.patch"

echo "=== P-eff 許可表に別の名を足す"
sed -i 's|"DOEFF_JOB_LOG_OTLP" "DOEFF_JOB_LOG_ATTRIBUTES")))|"DOEFF_JOB_LOG_OTLP" "DOEFF_JOB_LOG_ATTRIBUTES" "XDG_CACHE_HOME")))|' $P/src/doeff_cluster/worker/core/launch.hy
guard; undo "$EV/mutations/P-eff-allow.patch"
echo "=== P-eff2 許可表を頭 CLAUDE_ で広げる(この名は外される)"
sed -i 's|(setv CHILD-ENV-PREFIXES #("LC_"))|(setv CHILD-ENV-PREFIXES #("LC_" "CLAUDE_"))|' $P/src/doeff_cluster/worker/core/launch.hy
guard; undo "$EV/mutations/P-eff-prefix.patch"
echo "=== N-eff child-environment の外しを抜く"
sed -i 's|  (dfor #(k v) (.items merged) :if (not-in k WITHHELD-ENV-NAMES) k v))|  merged)|' $P/src/doeff_cluster/worker/core/launch.hy
git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a diff --stat -- $P | tail -1; guard; undo "$EV/mutations/N-eff-unwithhold-replace.patch"
echo "=== N-conc 木の job(EXTEND)の足す分の外しを抜く"
sed -i 's|                              :if (not-in k WITHHELD-ENV-NAMES) k v))|                              k v))|' $P/src/doeff_cluster/worker/core/launch.hy
git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a diff --stat -- $P | tail -1; guard; undo "$EV/mutations/N-conc-unwithhold-tree.patch"
echo "=== N-own 置き場の外の src がこの名を綴る(worker の文脈に置く)"
sed -i "s|(defk child-environment \[base extra declared worker\]|(val EXTRA-CONTEXT {\"$M\" \"/work\"})\n(defk child-environment [base extra declared worker]|" $P/src/doeff_cluster/worker/core/launch.hy
guard; undo "$EV/mutations/N-own-other-src.patch"

echo "=== B(盲検 B の差分)を直した版に当てる"
git apply "$EV/blind-B/failure.diff" && git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a diff --stat -- $P | tail -1
guard
echo "--- B の probe(雛形の名 + *)"; probe "$T,*"
undo "$EV/mutations/B-on-fixed.patch"
echo "=== A-Q1(盲検 A の多層防御 — 宣言の側で断る)を、置き場の定数を読む形で直した版に当てる"
uv run --no-project python - $P/src/doeff_cluster/shared/intent/runtime_env_model.hy <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
old = '''    (when (not (isinstance self.value str))
      (raise (TypeError (.format "環境変数 {} の値は文字列: {!r}" self.name self.value)))))'''
assert s.count(old) == 1
new = old[:-1] + '''
    (when (in self.name WITHHELD-ENV-NAMES)
      (raise (RuntimeEnvInvalid InvalidKind.RESERVED-ENV-VAR (.format "worker が子へ置かない名は宣言に置けない: {}" self.name)))))'''
p.write_text(s.replace(old, new))
PY
git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a diff --stat -- $P | tail -1
uv run --no-project python -c 'print()' >/dev/null
uv run python -c "
import hy
from doeff_cluster.shared.intent.runtime_env_model import EnvVar
print('EnvVar の断り:', EnvVar.environ_refusal({'$M': '/work'}))
print('ほかの名:', EnvVar.environ_refusal({'MODE': 'fast'}))" 2>&1 | tail -2
guard; undo "$EV/mutations/A-Q1-on-fixed.patch"
echo "=== 終わり: 作業木の差"; git -C /home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a status --short $P
