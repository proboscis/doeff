#!/bin/bash
# 盲検 A・B の失敗例を基準の版 e5b5c54c7 で再現する(差分を当てて走らせ、戻す)。
set -u
WT=/home/ubuntu/.worktrees/doeff-at-e5b5c54c7
EV=/home/ubuntu/.worktrees/doeff-wt-design-check-lt-2f7a/docs/design-checks/lt-2F7ATZXHBCYA0WX1F36A6ZC1JE/evidence
cd "$WT"
echo "版: $(git -C /home/ubuntu/.worktrees/doeff-at-e5b5c54c7 rev-parse HEAD)・状態: $(git -C /home/ubuntu/.worktrees/doeff-at-e5b5c54c7 status --short | wc -l) 行の差"
guard() { uv run pytest packages/doeff-cluster/tests/test_worker_pass_env.hy -q -k hook_marker -p no:cacheprovider 2>&1 | grep -E "AssertionError|passed|failed" | sort -u; echo "exit ${PIPESTATUS[0]}"; }
probe() { env NODE_NAME=n1 WORKER_NAME=w1 WORKER_HOST_SYSTEMD_ROOT=/host WORK_DIR=/work KUBERNETES_SERVICE_HOST=10.0.0.1 KUBERNETES_SERVICE_PORT=443 \
          CLAUDE_PROJECT_DIR=/work uv run python "$EV/blind-B/probe/compose_child_env.py" "$1" 2>&1 | tail -1 | cut -c1-200; }
revert() { git -C /home/ubuntu/.worktrees/doeff-at-e5b5c54c7 checkout -q -- packages; echo "戻した後の差: $(git -C /home/ubuntu/.worktrees/doeff-at-e5b5c54c7 status --short packages | wc -l) 行"; }
T="NODE_NAME,WORKER_NAME,WORKER_HOST_SYSTEMD_ROOT,WORK_DIR,KUBERNETES_SERVICE_HOST,KUBERNETES_SERVICE_PORT"
echo "=== 0 基準の版: 守りの検"; guard
echo "=== 0 基準の版: B の probe(雛形の名 + *)"; probe "$T,*"
echo "=== A-Q1 EnvVar がこの名を断る(多層防御)"; git apply "$EV/blind-A/diffs/Q1-envvar-refuses-hook-marker.patch" && git diff --stat | tail -1; guard; revert
echo "=== A-Q2 child-env がどの層からもこの名を外す"; git apply "$EV/blind-A/diffs/Q2-child-env-denies-hook-marker.patch" && git diff --stat | tail -1; guard; revert
echo "=== B --pass-env の * が宣言に置ける名を全部渡す"; git apply "$EV/blind-B/failure.diff" && git diff --stat | tail -1; guard
echo "--- B の probe(雛形の名 + *)"; probe "$T,*"
echo "--- B の probe(雛形の名だけ)"; probe "$T"
revert
