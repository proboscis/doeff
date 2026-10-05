;; worker の job の子 process の起こし方の判断 job-launch(worker/core/launch — #2464)を、子 process を起こさずに確かめる。
;;   木の job      … 文脈・宣言の environ・PYTHONPATH の上書きの分だけを返し、worker の環境は StartProcess の EXTEND で継ぐ。cwd = 木。
;;   実行環境の job … worker の環境から許可表の名と LC_* だけを継ぎ、宣言の env-vars を重ね、PYTHONPATH を置かず(REPLACE)、cwd = 空の作業 dir。
;;   Program の job … 詰めた Program の file を引数(--program)と宿の契約の環境変数で渡す。
(require doeff-hy.macros [deftest <- val])
(import json)
(import doeff_core_effects.process_effects [EnvEntry EnvMode])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout WorkerPolicy])
(import doeff_cluster.worker.core.launch [job-launch shim-argv JobLaunch])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-spans])

(val BASE {"PATH" "/usr/bin" "HOME" "/home/w" "LC_ALL" "C.UTF-8" "SECRET_TOKEN" "x" "VIRTUAL_ENV" "/venv"})
(val RUNTIME (json.dumps {"project" {"repo" "app" "path" "."} "envVars" [{"name" "DECLARED" "value" "1"}]}))


(deftest test-a-tree-job-runs-on-the-worker-environment-with-the-tree-on-the-path
  (val spec (JobSpec "svc" "app.main" #("service") "rev1" :environ #(#("MODE" "fast"))))
  ;; shim の猶予は本番の方針から導いた値(数を検に写さない — #2940)。
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/cache/rev1" "1-abc" 1 :python "/py" :hy-command "/bin/hy" :uv "uv" :extra-env {"DOEFF_WORKER_NAME" "w1"}
                                 :layout (CodeLayout) :allowed-env {} :worker-pid 42 :program-path None :program-env "DOEFF_PROGRAM_FILE"
                                 :work-dir "/jobs/svc" :shim-grace-ms shim.shim-grace-ms))
  (val env (dfor e plan.env e.name e.value))
  ;; shim の猶予の引数は、shim が読む形(秒の小数 — shim.py の float)で方針の値に戻る。
  (assert (= (float (get plan.argv 4)) (/ shim.shim-grace-ms 1000)) plan.argv)
  ;; job の log は 1 行ごとに壁の時計の刻を付ける(shim の旗 --stamp-lines・#3714)。
  (assert (= (+ (cut plan.argv 0 4) (cut plan.argv 5 None))
             #("/py" "-B" "-m" "doeff_cluster.worker.entry.shim" "--stamp-lines" "--" "/bin/hy" "-m" "app.main" "service"))
          plan.argv)
  (assert (= plan.cwd "/cache/rev1") plan.cwd)
  (assert (= #((get env "PYTHONPATH") (get env "MODE") (get env "DOEFF_WORKER_PID") (get env "DOEFF_WORKER_NAME")) #("/cache/rev1" "fast" "42" "w1")) env)
  ;; worker の環境は持たない — 子は StartProcess の EXTEND で継ぐ。
  (assert (= plan.env-mode EnvMode.EXTEND) plan.env-mode)
  (assert (not-in "SECRET_TOKEN" env) env)
  (assert (= #(plan.work-dir plan.last-used) #(None None)) plan))


(deftest test-a-runtime-env-job-inherits-only-the-allowed-worker-environment
  (val spec (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :runtime-env RUNTIME :env-key "k1"))
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/roots/env-k1" "1-abc" 1 :python "/py" :hy-command "/bin/hy" :uv "uv" :extra-env {}
                                 :layout (CodeLayout) :allowed-env BASE :worker-pid 42 :program-path "/state/programs/s.json"
                                 :program-env "DOEFF_PROGRAM_FILE" :work-dir "/jobs/task_t1" :shim-grace-ms shim.shim-grace-ms))
  (val env (dfor e plan.env e.name e.value))
  ;; 許可表の名と LC_* だけを継ぎ、資格を運びうる名・venv の名・PYTHONPATH は置かない。宣言の env-vars と Program の file を足す。
  (assert (= #((get env "PATH") (get env "LC_ALL") (get env "DECLARED") (get env "DOEFF_PROGRAM_FILE")) #("/usr/bin" "C.UTF-8" "1" "/state/programs/s.json")) env)
  (assert (not (& (set env) #{"SECRET_TOKEN" "VIRTUAL_ENV" "PYTHONPATH"})) env)
  (assert (= plan.env-mode EnvMode.REPLACE) plan.env-mode)
  (assert (= (cut plan.argv 5 7) #("--stamp-lines" "--")) plan.argv)
  (assert (= (cut plan.argv 7 None) #("uv" "run" "--no-sync" "--frozen" "--project" "/roots/env-k1/app" "hy" "-m" "doeff_cluster.worker.entry.job_entry"
                                      "task" "--program" "/state/programs/s.json")) plan.argv)
  (assert (= #(plan.cwd plan.work-dir plan.last-used) #("/jobs/task_t1" "/jobs/task_t1" "/roots/env-k1/.last-used")) plan)
  ;; 環境変数は名の順(StartProcess の env にそのまま渡せる形)。
  (assert (= (lfor e plan.env e.name) (sorted env)) plan.env))


(deftest test-the-job-log-is-stamped-and-the-probe-output-is-carried-as-is
  ;; job の log(人が刻で読む)は shim の旗で 1 行ごとに刻を付け、入口の検め(worker が stdout の行を読んで判じる)は旗なしでそのまま
  ;; 運ぶ(#3714)— 旗は命令の頭の猶予と区切りの間。
  (<- job (get tuple #(str ...)) (shim-argv "/py" 500 :stamp-lines True))
  (<- probe (get tuple #(str ...)) (shim-argv "/py" 500 :stamp-lines False))
  (assert (= job #("/py" "-B" "-m" "doeff_cluster.worker.entry.shim" "0.5" "--stamp-lines" "--")) job)
  (assert (= probe #("/py" "-B" "-m" "doeff_cluster.worker.entry.shim" "0.5" "--")) probe))
