;; 子 process の文脈の型(RunContext)が 1 つだけであること(2026-09-26)と、宿の契約(HOST-CONTRACT — 2026-09-27)。
;;
;; 子は `hy -m doeff_cluster.job_entry` で起きるので job_entry は __main__ として読まれる。業務の module が job_entry から
;; runtime-env-of-context を import すると、同じ file が doeff_cluster.job_entry としてもう 1 回読まれ、__main__ の RunContext を渡された
;; :pre の型の検めが「ctx expected RunContext, got RunContext」で必ず落ちた(実験用の namespace で再現)。文脈の型と読みは入口でない
;; module(job_context)に置く。job は Program の値 1 つで、文脈は土台の handler host-reader が Ask "doeff.cluster.run-context" に答える
;; (入口は handler を足さない — ADR-DOE-CLUSTER-001 R2)。この検は本物の入口を subprocess で起こし、Program が自分の並べた host-reader で
;; 文脈と自分の Program の path を読み、job_entry から import した runtime-env-of-context がそれを受けることを確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff_cluster.host_contract [HOST-CONTRACT])
(import doeff_cluster.remote_model [encode-program current-versions])
(import doeff_cluster.runtime_env_model [RepoCheckout PythonProject RuntimeEnv runtime-env->json])
(import tests.fixtures.entry_programs [context-program host-foundation])

(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val PACKAGE (str (. (Path __file__) (resolve) parent parent)))


(defk sample-env []
  {:pre [] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "子へ渡す宣言(repo 1 つ)。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.invalid/app.git" :commit (* "a" 40)))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(deftest test-a-program-in-a-child-reads-its-own-context-through-the-host-reader [tmp-path]
  (val program-file (/ tmp-path "program.json"))
  (.write-text program-file (json.dumps {"blob" (encode-program (context-program host-foundation)) "versions" (current-versions)}) :encoding "utf-8")
  (<- declared RuntimeEnv (sample-env))
  (<- declared-json dict (runtime-env->json declared))
  (val environment (| (dict os.environ)
                      {"PYTHONPATH" (.join ":" [PACKAGE (.get os.environ "PYTHONPATH" "")])
                       "PYTHONDONTWRITEBYTECODE" "1"
                       "DOEFF_WORKER_JOB" "ctx-probe"
                       HOST-CONTRACT.program-env (str program-file)
                       "DOEFF_RUNTIME_ENV" (json.dumps declared-json)
                       "DOEFF_RUNTIME_ENV_KEY" "k"}))
  (val done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" (str program-file)]
                            :cwd (str tmp-path) :env environment :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) (+ done.stdout done.stderr))
  ;; 宣言の repo の名(run-context 経由の runtime env)と、宿が渡した Program の path(HOST-CONTRACT の program-key)。
  (assert (in (.format "が終わった: 'app|{}'" program-file) done.stderr) done.stderr))
