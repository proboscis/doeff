;; 子 process の文脈の型(RunContext)が 1 つだけであること(2026-09-26)。
;;
;; 子は `hy -m doeff_cluster.job_entry` で起きるので job_entry は __main__ として読まれる。env の組み立てが job_entry から
;; runtime-env-of-context を import すると、同じ file が doeff_cluster.job_entry としてもう 1 回読まれ、__main__ の RunContext を渡された
;; :pre の型の検めが「ctx expected RunContext, got RunContext」で必ず落ちた(実験用の namespace で再現)。文脈の型と読みは入口でない
;; module(job_context)に置き、この検は本物の入口を subprocess で起こして、env の組み立てがそれを呼べることを確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff_cluster.runtime_env_model [RepoCheckout PythonProject RuntimeEnv runtime-env->json])

(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val SRC (str (. (Path __file__) (resolve) parent parent (joinpath "src"))))

;; 子の中の業務の module: env の組み立てが job_entry から runtime-env-of-context を import して呼び、読めた宣言の repo の名を file に書く。
(val CHILD-MODULE
  (+ "(require doeff-hy.macros [defk <-])\n"
     "(import pathlib [Path])\n"
     "(import doeff [run])\n"
     "(import doeff_cluster.job_entry [runtime-env-of-context])\n"
     ";; service の本体(すぐ終わる)。\n"
     "(defk program []\n  {:pre [] :post [(: % int)]}\n  0)\n"
     ";; env の組み立て: 自分の文脈から宣言を読み、repo の名を out へ書く。\n"
     "(defn env [config ctx]  ; defk にできない: job_entry が Program の外で呼ぶ組み立ての関数\n"
     "  (setv declared (run (runtime-env-of-context ctx)))\n"
     "  (.write-text (Path (get config \"out\")) (.join \",\" (lfor r declared.repos r.name)) :encoding \"utf-8\")\n"
     "  [])\n"))


(defk sample-env []
  {:pre [] :post [(: % RuntimeEnv)]}
  "子へ渡す宣言(repo 1 つ)。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.invalid/app.git" :commit (* "a" 40)))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(deftest test-an-env-builder-in-a-child-can-read-its-own-runtime-env [tmp-path]
  (.write-text (/ tmp-path "ctxenv.hy") CHILD-MODULE :encoding "utf-8")
  (val out (/ tmp-path "out.txt"))
  (<- declared RuntimeEnv (sample-env))
  (<- declared-json dict (runtime-env->json declared))
  (val environment (| (dict os.environ)
                      {"PYTHONPATH" (.join ":" [(str tmp-path) SRC (.get os.environ "PYTHONPATH" "")])
                       "PYTHONDONTWRITEBYTECODE" "1"
                       "DOEFF_WORKER_JOB" "ctx-probe"
                       "DOEFF_RUNTIME_ENV" (json.dumps declared-json)
                       "DOEFF_RUNTIME_ENV_KEY" "k"}))
  (val done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "service" "--factory" "ctxenv:program" "--env" "ctxenv:env"
                             "--config" (json.dumps {"out" (str out)})]
                            :cwd (str tmp-path) :env environment :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) (+ done.stdout done.stderr))
  (assert (= (.read-text out :encoding "utf-8") "app") (+ done.stdout done.stderr)))
