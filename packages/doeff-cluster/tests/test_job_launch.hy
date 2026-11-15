;; worker の job の子 process の起こし方の判断 job-launch(worker/core/launch — #2464)を、子 process を起こさずに確かめる。
;;   木の job      … 文脈・宣言の environ・PYTHONPATH の上書きの分だけを返し、worker の環境は StartProcess の EXTEND で継ぐ。cwd = 木。
;;   実行環境の job … worker の環境から許可表の名と LC_* だけを継ぎ、宣言の env-vars を重ね、PYTHONPATH を置かず(REPLACE)、cwd = 空の作業 dir。
;;   Program の job … 詰めた Program の file を引数(--program)と宿の契約の環境変数で渡す。
(require doeff-hy.macros [deftest <- val])
(import json)
(import dataclasses [replace])
(import doeff_core_effects [EnvEntry EnvMode])
(import doeff_cluster [JobSpec])
(import doeff_cluster [CodeLayout WorkerPolicy])
(import doeff_cluster._worker.core.launch [job-launch shim-argv uv-run-environ venv-of-marker JobLaunch])
(import doeff_cluster._shared.core.runtime_env [root-venv])
(import doeff_cluster [shim-spans]
        doeff_cluster._worker.core.shim_timing [ShimSpans])

(val BASE {"PATH" "/usr/bin" "HOME" "/home/w" "LC_ALL" "C.UTF-8" "SECRET_TOKEN" "x" "VIRTUAL_ENV" "/venv"})
(val RUNTIME (json.dumps {"project" {"repo" "app" "path" "."} "envVars" [{"name" "DECLARED" "value" "1"}]}))


(deftest test-a-tree-job-runs-on-the-worker-environment-with-the-tree-on-the-path
  (val spec (JobSpec "svc" "app.main" #("service") "rev1" :environ #(#("MODE" "fast"))))
  ;; shim の猶予は本番の方針から導いた値(数を検に写さない — #2940)。
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/cache/rev1" "1-abc" 1 :entry spec.entry :venv None :python "/py" :hy-command "/bin/hy" :extra-env {"DOEFF_WORKER_NAME" "w1"}
                                 :layout (CodeLayout) :allowed-env {} :worker-pid 42 :program-path None :program-env "DOEFF_PROGRAM_FILE"
                                 :work-dir "/jobs/svc" :shim-grace-ms shim.shim-grace-ms :notice-env "DOEFF_NOTICE_FD"))
  (val env (dfor e plan.env e.name e.value))
  ;; shim の猶予の引数は、shim が読む形(秒の小数 — shim.py の float)で方針の値に戻る。
  (assert (= (float (get plan.argv 4)) (/ shim.shim-grace-ms 1000)) plan.argv)
  ;; job の log は 1 行ごとに壁の時計の刻を付ける(shim の旗 --stamp-lines・#3714)。
  (assert (= (+ (cut plan.argv 0 4) (cut plan.argv 5 None))
             #("/py" "-B" "-m" "doeff_cluster._worker.entry.shim" "--stamp-lines" "--notice-env" "DOEFF_NOTICE_FD" "--" "/bin/hy" "-m" "app.main"
               "service"))
          plan.argv)
  (assert (= plan.cwd "/cache/rev1") plan.cwd)
  (assert (= #((get env "PYTHONPATH") (get env "MODE") (get env "DOEFF_WORKER_PID") (get env "DOEFF_WORKER_NAME")) #("/cache/rev1" "fast" "42" "w1")) env)
  ;; worker の環境は持たない — 子は StartProcess の EXTEND で継ぐ。
  (assert (= plan.env-mode EnvMode.EXTEND) plan.env-mode)
  (assert (not-in "SECRET_TOKEN" env) env)
  (assert (= #(plan.work-dir plan.last-used) #(None None)) plan))


(deftest test-a-runtime-env-job-inherits-only-the-allowed-worker-environment
  (val spec (JobSpec "task/t1" "doeff_cluster._worker.entry.job_entry" #("task") "rev1" :runtime-env RUNTIME :env-key "k1"))
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/roots/env-k1" "1-abc" 1 :entry spec.entry :venv "/roots/env-k1/.venv" :python "/py" :hy-command "/bin/hy" :extra-env {}
                                 :layout (CodeLayout) :allowed-env BASE :worker-pid 42 :program-path "/state/programs/s.json"
                                 :program-env "DOEFF_PROGRAM_FILE" :work-dir "/jobs/task_t1" :shim-grace-ms shim.shim-grace-ms
                                 :notice-env "DOEFF_NOTICE_FD"))
  (val env (dfor e plan.env e.name e.value))
  ;; 許可表の名と LC_* だけを継ぎ、資格を運びうる名・PYTHONPATH は置かない。宣言の env-vars と Program の file を足し、root の venv の 2 つ
  ;; (PATH の頭・VIRTUAL_ENV — uv run が足していた物)を足す(worker の VIRTUAL_ENV は継がない)。
  (assert (= #((get env "PATH") (get env "VIRTUAL_ENV") (get env "LC_ALL") (get env "DECLARED") (get env "DOEFF_PROGRAM_FILE"))
             #("/roots/env-k1/.venv/bin:/usr/bin" "/roots/env-k1/.venv" "C.UTF-8" "1" "/state/programs/s.json")) env)
  (assert (not (& (set env) #{"SECRET_TOKEN" "PYTHONPATH"})) env)
  (assert (= plan.env-mode EnvMode.REPLACE) plan.env-mode)
  (assert (= (cut plan.argv 5 9) #("--stamp-lines" "--notice-env" "DOEFF_NOTICE_FD" "--")) plan.argv)
  ;; 本体は uv run を挟まずに root の venv(project の木の外 — 呼び手が完成の印から読んで渡す)の hy を直に起こす。
  (assert (= (cut plan.argv 9 None) #("/roots/env-k1/.venv/bin/hy" "-m" "doeff_cluster._worker.entry.job_entry"
                                      "task" "--program" "/state/programs/s.json")) plan.argv)
  (assert (= #(plan.cwd plan.work-dir plan.last-used) #("/jobs/task_t1" "/jobs/task_t1" "/roots/env-k1/.last-used")) plan)
  ;; 環境変数は名の順(StartProcess の env にそのまま渡せる形)。
  (assert (= (lfor e plan.env e.name) (sorted env)) plan.env)
  ;; 待ちの子から分ける道の環境(entry-env)は shim の下の道の env と同じ値— 2 つの道が
  ;; 同じ root の venv を名で引く。
  (assert (= plan.entry-env plan.env) plan.entry-env))


(deftest test-a-tree-job-has-no-entry-env-because-it-never-forks-from-a-warm-child
  ;; 木の job は待ちの子から分けない(warm_rules.forks-from-warm-child)ので、分ける道の環境を持たない。
  (val spec (JobSpec "task/t2" "app.main" #("task") "rev1" :once True))
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/cache/rev1" "1-abc" 1 :entry spec.entry :venv None :python "/py" :hy-command "/bin/hy" :extra-env {}
                                 :layout (CodeLayout) :allowed-env BASE :worker-pid 42 :program-path None :program-env "DOEFF_PROGRAM_FILE"
                                 :work-dir "/jobs/task_t2" :shim-grace-ms shim.shim-grace-ms :notice-env "DOEFF_NOTICE_FD"))
  (assert (= plan.entry-env #()) plan.entry-env))


(deftest test-uv-run-environ-puts-the-root-venv-at-the-head-like-uv
  ;; uv の足し方(本物の uv との突き合わせは tests/test_task_venv_on_both_paths.hy): PATH の頭に root の venv の bin を足す。PATH が
  ;; 無ければ bin だけ。他の名と、在った VIRTUAL_ENV は venv で上書き。root の venv の path は root を字面で正規化する(末尾の / と . を
  ;; 落とす・symlink は解かない — runtime_env.root-venv)。
  (<- venv str (root-venv "/roots/env-k1/./"))
  (assert (= venv "/roots/env-k1/.venv") venv)
  (<- with-path dict (uv-run-environ venv {"PATH" "/boot/.venv/bin:/usr/bin" "VIRTUAL_ENV" "/boot/.venv" "HOME" "/home/w"}))
  (assert (= with-path {"PATH" "/roots/env-k1/.venv/bin:/boot/.venv/bin:/usr/bin" "VIRTUAL_ENV" "/roots/env-k1/.venv" "HOME" "/home/w"})
          with-path)
  (<- without-path dict (uv-run-environ venv {"HOME" "/home/w"}))
  (assert (= without-path {"PATH" "/roots/env-k1/.venv/bin" "VIRTUAL_ENV" "/roots/env-k1/.venv" "HOME" "/home/w"}) without-path))


(deftest test-the-root-venv-is-read-from-the-marker-and-an-old-root-keeps-its-venv-in-the-tree
  ;; 子・待ちの子・入口の検めが引く venv は root の完成の印から読む: 今の準備の印(欄 venv)は project の木の外の <root>/.venv・欄の無い
  ;; 前の形の印は印の宣言の project の木の中の venv(次の掃除で消えるまで使う)・印の読めない root は木の外の venv。
  (val declared {"project" {"repo" "app" "path" "svc"}})
  (val rows
    #(#("今の印" (json.dumps {"env" declared "venv" ".venv"}) "/roots/env-k1/.venv")
      #("前の形の印(欄 venv が無い)" (json.dumps {"env" declared}) "/roots/env-k1/app/svc/.venv")
      #("前の形の印(project の path が .)" (json.dumps {"env" {"project" {"repo" "app" "path" "."}}}) "/roots/env-k1/app/.venv")
      #("印が無い" None "/roots/env-k1/.venv")
      #("印が壊れている" "{" "/roots/env-k1/.venv")))
  (for [#(label text expected) rows]
    (<- venv str (venv-of-marker "/roots/env-k1" text))
    (assert (= venv expected) #(label venv))))


(deftest test-the-job-log-is-stamped-and-the-probe-output-is-carried-as-is
  ;; job の log(人が刻で読む)は shim の旗で 1 行ごとに刻を付け、入口の検め(worker が stdout の行を読んで判じる)は旗なしでそのまま
  ;; 運ぶ(#3714)— 旗は命令の頭の猶予と区切りの間。job は退きの知らせの pipe を受け(--notice-env と環境変数の名 — #3672)、検めは受けない。
  (<- job (get tuple #(str ...)) (shim-argv "/py" 500 :stamp-lines True :notice-env "DOEFF_NOTICE_FD"))
  (<- probe (get tuple #(str ...)) (shim-argv "/py" 500 :stamp-lines False :notice-env None))
  (assert (= job #("/py" "-B" "-m" "doeff_cluster._worker.entry.shim" "0.5" "--stamp-lines" "--notice-env" "DOEFF_NOTICE_FD" "--")) job)
  (assert (= probe #("/py" "-B" "-m" "doeff_cluster._worker.entry.shim" "0.5" "--")) probe))


(deftest test-only-a-handoff-service-with-a-retired-lifetime-outlives-the-worker
  ;; worker の消失を退きとして寿命の上限まで待つ旗 --orphan-seconds は、退きの寿命 retired-ms を宣言した handoff の
  ;; service だけに付く(値は寿命の秒)。寿命を宣言しない handoff・recreate の service・task は付かない(消失ですぐ止める)。
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (val service (JobSpec "svc" "app.main" #("service") "rev1"))
  (val rows
    #(#("寿命つきの handoff の service" (replace service :handoff True :retired-ms 18000000) #("--orphan-seconds" "18000.0"))
      #("寿命の無い handoff の service" (replace service :handoff True) #())
      #("recreate の service" service #())
      #("寿命つきの handoff の task" (replace service :handoff True :retired-ms 18000000 :once True) #())))
  (for [#(label spec expected) rows]
    (<- plan JobLaunch (job-launch spec "/cache/rev1" "1-abc" 1 :entry spec.entry :venv None :python "/py" :hy-command "/bin/hy" :extra-env {}
                                   :layout (CodeLayout) :allowed-env {} :worker-pid 42 :program-path None
                                   :program-env "DOEFF_PROGRAM_FILE" :work-dir "/jobs/svc" :shim-grace-ms shim.shim-grace-ms
                                   :notice-env "DOEFF_NOTICE_FD"))
    (val head (cut plan.argv 0 (.index plan.argv "--")))
    (val flag (if (in "--orphan-seconds" head)
                  (cut head (.index head "--orphan-seconds") (+ (.index head "--orphan-seconds") 2))
                  #()))
    (assert (= flag expected) (.format "{}: {}" label plan.argv))))
