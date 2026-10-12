"""main.hy と process_host.start-job が組む順のまま、実行環境の job(REPLACE)の子の env を組む probe。
引数: --pass-env の文字(雛形の WORKER_PASS_ENV が $(WORKER_PASS_ENV_EXTRA) を展開した後の値)。
この process の os.environ を worker の環境として読む(答え手 = 本物の subprocess-handler)。"""
import json
import sys

import hy  # noqa: F401  (Hy の module を import できるようにする)
from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment
from doeff_cluster.shared.intent.job_model import JobSpec
from doeff_cluster.worker.intent.worker_model import CodeLayout, WorkerPolicy
from doeff_cluster.worker.core.launch import job_launch, CHILD_ENV_ALLOWED, CHILD_ENV_PREFIXES
from doeff_cluster.worker.core.shim_timing import shim_spans
from doeff_cluster.worker.core.boot_timing import BOOT_STARTED_VAR, BOOT_EXEC_VAR
from doeff_cluster.shared.core.run_context_rules import worker_context_environ
from doeff_cluster.worker.entry import main as worker_main

pass_env = sys.argv[1]
# main.hy の machine-env の組み方と同じ(pass-env-names + 世代の file・起動の刻の名)
names = run(worker_main.pass_env_names(pass_env)) + (worker_main.BOOT_FILE_VAR, BOOT_STARTED_VAR, BOOT_EXEC_VAR)
machine_env = run(with_handlers([subprocess_handler], worker_main.machine_environment(names)))
try:
    passed = run(worker_main.passed_environment(pass_env, machine_env))
except Exception as error:  # 前の版の fail-fast(名が無ければ起動を止める)
    print(json.dumps({"pass_env": pass_env, "worker_boot": f"止まる: {type(error).__name__}: {error}"}, ensure_ascii=False))
    sys.exit(0)
host_env = passed | run(worker_context_environ("http://coord:8080", "w1"))
# process_host.start-job: 実行環境の job だけが許可表の名と LC_* を読む
allowed = run(with_handlers([subprocess_handler],
                            ReadEnvironment(tuple(sorted(CHILD_ENV_ALLOWED)), prefixes=CHILD_ENV_PREFIXES)))
runtime = json.dumps({"project": {"repo": "agora-controllers", "path": "."}, "envVars": []})
spec = JobSpec("turn-host-zeus", "controllers.turns.entry", ("service",), "rev1", runtime_env=runtime, env_key="k1")
shim = run(shim_spans(WorkerPolicy()))
plan = run(job_launch(spec, "/roots/env-k1", "1-abc", 1, python="/py", hy_command="/bin/hy", uv="uv",
                      extra_env=host_env, layout=CodeLayout(), allowed_env={e.name: e.value for e in allowed},
                      worker_pid=42, program_path=None, program_env="DOEFF_PROGRAM_FILE", work_dir="/jobs/turn-host",
                      shim_grace_ms=shim.shim_grace_ms, notice_env="DOEFF_NOTICE_FD"))
env = {e.name: e.value for e in plan.env}
marker = "CLAUDE_" + "PROJECT_DIR"  # probe の側の綴り(検の見る dir の外)
print(json.dumps({"pass_env": pass_env, "env_mode": str(plan.env_mode), "marker_in_child_env": marker in env,
                  "marker_value": env.get(marker), "child_env_names": sorted(env)}, ensure_ascii=False))
