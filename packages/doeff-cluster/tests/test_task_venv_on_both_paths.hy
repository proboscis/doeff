;; 実行環境の task の入口が、待ちの子から分かれる道と冷えた道(shim の下の uv run)で、同じ root の venv を名で引くこと(card ki-b884608b96e4)。
;;
;; 前は、冷えた道では uv run が root の venv を PATH の頭と VIRTUAL_ENV に足し、待ちの子から分かれる道では分かれた子 A が頼みの env(許可表の
;; worker の PATH のまま・VIRTUAL_ENV 無し)で置き換えたので、名で hy・python を起こす task が道によって違う venv(worker の boot の venv)を
;; 引いた。今は job-launch が uv run の足す 2 つを足した環境(entry-env)を作り、分かれる道はそれを頼みに載せる。
;;
;; 1 つの job-launch の答え(実行環境の task — --project = この checkout・入口 = tests/fixtures/warm_job の inspect)から、本物の process で
;; 2 つの道を起こし、入口が見た環境の PATH と VIRTUAL_ENV を突き合わせる:
;;   冷えた道     … 答えの argv(shim の下の uv run … hy -m 入口)を答えの env(許可表で絞った物・REPLACE)で起こす — 本物の uv が venv を足す。
;;   分かれる道   … 宿 warm_host が本物の uv run で起こした待ちの子へ、宿 process_host と同じ欄(entry-args・cwd・entry-env)で頼む。
;;   前の形       … 同じ待ちの子へ、答えの env のまま頼む — PATH の頭が worker の venv・VIRTUAL_ENV 無しで冷えた道と食い違う(失敗ケース —
;;                  この検が食い違いを測れている事を示す)。
;; worker の PATH の頭には本番と同じく worker の venv が在る形にする(許可表へ渡す PATH の頭に代わりの dir を置く)。冷えた道の hy -m が
;; 入口を読めるよう、作業 dir(cwd)と待ちの子の root は package の dir にする(本番の入口は root の venv から読む)。
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import shutil)
(import sys)
(import tempfile)
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.handlers [await-handler slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler])
(import doeff_core_effects.process_exit [process-exit-handler])
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment StartProcess ProcessStarted ProcessNotStarted AwaitProcessExit PollProcess
                                            ProcessExited ProcessRunning ProcessNotChild])
(import doeff_core_effects.warm_effects [ForkFromWarm WarmForked WarmRefused AwaitWarmChildExit PollWarmChild WarmExited WarmRunning WarmLost])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout WarmLaunch WarmChildView StartWarmChild StopWarmChild StopStage WorkerPolicy])
(import doeff_cluster.worker.protocol.observations [ObserveWarmChildren])
(import doeff_cluster.worker.protocol.warm_host [WarmSettings warm-host])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.warm_rules [WarmPlace warm-place warm-child-ready])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-spans])

;; repo の根(uv の --project — venv は <根>/.venv)と、この package の dir(冷えた道の cwd・待ちの子の root — tests.fixtures を読める所)。
(val REPO (get (. (.resolve (Path __file__)) parents) 3))
(val PACKAGE (get (. (.resolve (Path __file__)) parents) 1))
(val ENTRY "tests.fixtures.warm_job")
(val KEY "env-venv")
;; 待ちの子の準備(module の読み込み)を待つ上限(0.05 秒ごとに観測を読み直す回数 — 60 秒)。
(val READY-TRIES 1200)


(defk task-launch [out boot]
  {:pre [(: out str) (: boot str)] :post [(: % JobLaunch)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口 warm_job の inspect(見た環境を out に書く)を走らせる実行環境の task の起こし方を、本番の判断 job-launch で決めるため。許可表の
   worker の環境はこの検の process から読み、PATH の頭に boot(worker の venv の代わり)を置く。"
  (<- allowed tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
  (val worker-env (dfor e allowed e.name e.value))
  (val base (| worker-env {"PATH" (+ boot ":" (.get worker-env "PATH" ""))}))
  (val spec (JobSpec "task/venv" ENTRY #("inspect" out) "rev1" :once True
                     :runtime-env (json.dumps {"project" {"repo" REPO.name "path" "."}}) :env-key "venv"))
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec (str REPO.parent) "1-venv" 1 :python sys.executable :hy-command "hy" :uv "uv" :extra-env {}
                                 :layout (CodeLayout) :allowed-env base :worker-pid (os.getpid) :program-path None
                                 :program-env HOST-CONTRACT.program-env :work-dir (str PACKAGE) :shim-grace-ms shim.shim-grace-ms
                                 :notice-env HOST-CONTRACT.notice-env))
  plan)


(defk entry-env-seen [out]
  {:pre [(: out str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口 warm_job の inspect が書いた見え方から、入口が見た環境変数を読むため。"
  (<- text (ReadText out))
  (assert (not (isinstance text FileFailed)) (.format "入口が見え方を書いていない: {}" text))
  (get (json.loads text) "env"))


(defk cold-path-env [plan out log]
  {:pre [(: plan JobLaunch) (: out str) (: log str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "冷えた道: 答えの argv(shim の下の uv run … hy -m 入口)を宿 process_host と同じ flag で起こし、終わりを待って、入口が見た環境を返すため。"
  (<- started (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv plan.argv :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path log :stderr-path log
                    :process-group True :hold-stdin True :reap-group True))
  (assert (isinstance started ProcessStarted) (.format "冷えた道の子を起こせない: {}" started))
  (<- (AwaitProcessExit started.pid))
  (<- ended (| ProcessExited ProcessRunning ProcessNotChild) (PollProcess started.pid))
  (<- shown (ReadText log))
  (assert (and (isinstance ended ProcessExited) (= ended.exit-code 0)) (.format "冷えた道の子が 0 で終わらない: {} log={}" ended shown))
  (<- seen dict (entry-env-seen out))
  seen)


(defk warm-child-readied [key]
  {:pre [(: key str)] :post [(: % WarmChildView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宿 warm_host が起こした待ちの子が準備済みになるまで、宿の観測を読み直すため(準備完了の印の file には出来事の口が無い — worker の拍と
   同じく観測で読む)。準備の前に終わったら、その訳(出力の最後の 1 行)で落ちる。"
  (var view None)
  (var tries 0)
  (while (not (warm-child-ready view))
    (<- views tuple (ObserveWarmChildren))
    (:= view (next (gfor v views :if (= v.key key) v) None))
    (assert (or (is view None) (is view.exit-code None)) (.format "待ちの子が準備の前に終わった: {}" view))
    (when (not (warm-child-ready view))
      (assert (< tries READY-TRIES) (.format "待ちの子が {} 回読み直しても準備済みにならない: {}" READY-TRIES view))
      (<- (Delay 0.05))
      (:= tries (+ tries 1))))
  view)


(defk forked-env [plan env socket out stem]
  {:pre [(: plan JobLaunch) (: env tuple) (: socket str) (: out str) (: stem str)] :post [(: % dict)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子から分かれる道: 宿 process_host と同じ欄(入口の後ろの引数・cwd)と、渡された env で待ちの子(socket)へ頼み、分かれた子の
   終わりを待って、入口が見た環境を返すため。"
  (<- forked (| WarmForked WarmRefused)
      (ForkFromWarm :socket-path socket :entry ENTRY :args plan.entry-args :cwd plan.cwd :env env
                    :log-path (+ stem ".log") :exit-path (+ stem ".exit") :grace-seconds 1.0))
  (assert (isinstance forked WarmForked) (.format "待ちの子が分けるのを断った: {}" forked))
  (<- (AwaitWarmChildExit :pid forked.pid :start-ticks forked.start-ticks))
  (<- ended (| WarmExited WarmRunning WarmLost) (PollWarmChild :pid forked.pid :start-ticks forked.start-ticks :exit-path (+ stem ".exit")))
  (<- shown (ReadText (+ stem ".log")))
  (assert (and (isinstance ended WarmExited) (= ended.exit-code 0)) (.format "分かれた子が 0 で終わらない: {} log={}" ended shown))
  (<- seen dict (entry-env-seen out))
  seen)


(defk both-paths-seen [place warm-dir]
  {:pre [(: place str) (: warm-dir str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 つの job-launch の答えで、冷えた道・分かれる道・前の形の分かれる道を順に起こし、入口が見た環境を #(冷えた道 分かれる道 前の形 boot)
   で返すため(入口は同じ out に書くので、1 本ずつ終わりを待ってから読む)。待ちの子は宿の置き場の根 warm-dir の下に居て、終わりに止める。"
  (val out (+ place "/seen.json"))
  (val boot (+ place "/boot/.venv/bin"))
  (<- plan JobLaunch (task-launch out boot))
  (<- cold dict (cold-path-env plan out (+ place "/cold.log")))
  (<- (StartWarmChild KEY (WarmLaunch :root (str PACKAGE) :project (str REPO) :preload #(ENTRY))))
  (<- (warm-child-readied KEY))
  (<- warm-at WarmPlace (warm-place warm-dir KEY))
  (<- warm dict (forked-env plan plan.entry-env warm-at.socket out (+ place "/forked")))
  (<- old dict (forked-env plan plan.env warm-at.socket out (+ place "/old")))
  (<- (StopWarmChild KEY StopStage.TERM "検の終わり"))
  #(cold warm old boot))


(defhandler bytecode-kept-out
  ;; 検の差し込み: 起こす子(冷えた道の shim と待ちの子)の環境に PYTHONDONTWRITEBYTECODE を足してから本物へ渡す。許可表は PYTHON* を継がない
  ;; ので根の conftest の固定が子へ届かず、--project のこの checkout の venv に子が bytecode を書く(test_warm_child_uv_reap.hy が -B を付けるのと
  ;; 同じ訳)。待ちの子から分かれた子は、待ちの子の起動の時の旗を継ぐ。比べる PATH と VIRTUAL_ENV には触らない。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- answer (StartProcess :argv argv :cwd cwd :env (+ (or env #()) #((EnvEntry :name "PYTHONDONTWRITEBYTECODE" :value "1")))
                             :env-mode env-mode :env-drop env-drop :stdout-path stdout-path :stderr-path stderr-path
                             :process-group process-group :hold-stdin hold-stdin :reap-group reap-group))
    (resume answer)))


(deff on-real-processes [#^ str place #^ str warm-dir #^ Program program]  ; defk にできない: 検が自分の scheduler と本物の event loop(子の終わると読める fd)で回す入口
  {:pre [(: place str) (: warm-dir str) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を本物の答え手の組(外側が先 — 宿の表の state・event loop・実時間・file・子 process・子の終わりの待ち・待ちの子・宿 warm_host)の
   上で回すため。待ちの子の置き場の根は warm-dir・log は place の下(socket の path は unix socket の上限 107 byte の内)。"
  (run (scheduled (with-handlers [(state) (await-handler) (async-time-handler) slog-handler os-file-handler subprocess-handler process-exit-handler
                                  os-warm-process-handler bytecode-kept-out
                                  (warm-host (WarmSettings :warm-dir warm-dir :log-dir (+ place "/logs") :generation "0b6f5e2a9c1d" :uv "uv"))]
                    program))))


(deftest test-the-entry-sees-the-same-root-venv-on-the-warm-and-the-cold-path
  (val place (tempfile.mkdtemp :prefix "venv-" :dir "/tmp"))  ; socket の path を短く保つ(pytest の tmp-path は長い)
  (val warm-dir (+ place "/warm"))
  (var seen #())
  (try
    (:= seen (on-real-processes place warm-dir (both-paths-seen place warm-dir)))
    (finally
      (shutil.rmtree place :ignore-errors True)))
  (val cold (get seen 0))
  (val warm (get seen 1))
  (val old (get seen 2))
  (val boot (get seen 3))
  (val venv (str (/ REPO ".venv")))
  ;; 冷えた道: 本物の uv が root の venv を PATH の頭と VIRTUAL_ENV に置く(uv-run-environ が写す uv の足し方)。
  (assert (= #((get (.split (get cold "PATH") ":") 0) (.get cold "VIRTUAL_ENV")) #((+ venv "/bin") venv)) cold)
  ;; 分かれる道: 入口が見る PATH(全部)と VIRTUAL_ENV が冷えた道と同じ。
  (assert (= #((get warm "PATH") (.get warm "VIRTUAL_ENV")) #((get cold "PATH") (get cold "VIRTUAL_ENV")))
          (.format "warm={} cold={}" #((get warm "PATH") (.get warm "VIRTUAL_ENV")) #((get cold "PATH") (get cold "VIRTUAL_ENV"))))
  ;; 前の形(答えの env のまま頼む): PATH の頭が worker の venv・VIRTUAL_ENV 無しで、冷えた道と食い違う。
  (assert (= #((get (.split (get old "PATH") ":") 0) (.get old "VIRTUAL_ENV")) #(boot None)) old))
