;; 実行環境の task の入口が、待ちの子から分かれる道と冷えた道(shim の下で root の venv の hy を直に起こす道)で、同じ root の venv を名で
;; 引くこと(card ki-b884608b96e4・ki-869cdf21ec85)。
;;
;; 前は、冷えた道では uv run が root の venv を PATH の頭と VIRTUAL_ENV に足し、待ちの子から分かれる道では分かれた子 A が頼みの env(許可表の
;; worker の PATH のまま・VIRTUAL_ENV 無し)で置き換えたので、名で hy・python を起こす task が道によって違う venv(worker の boot の venv)を
;; 引いた。今は job-launch が uv run の足していた 2 つを足した環境を作り、冷えた道の env と分かれる道の頼み(entry-env)の両方に置く。
;; 冷えた道は uv run を挟まない(card ki-869cdf21ec85)ので、uv が選ぶ venv との突き合わせは、本物の uv run の下に起きる待ちの子の
;; interpreter の venv で行う。
;;
;; 1 つの宣言(実行環境の task — root の project は symlink でこの checkout を指す・入口 = tests/fixtures/warm_job の inspect)から、本物の
;; process で 2 つの道を起こし、入口の見え方を突き合わせる:
;;   冷えた道     … job-launch の答えの argv(shim の下の <root の project>/.venv/bin/hy -m 入口)を答えの env(許可表で絞り root の venv の
;;                  2 つを足した物・REPLACE)で起こす。
;;   分かれる道   … 宿 warm_host が、本番の判断 warm-launch の起こし方(uv の --project)で本物の uv run の下に起こした待ちの子へ、宿
;;                  process_host と同じ欄(entry-args・cwd・entry-env)で頼む。
;;   前の形       … 同じ待ちの子へ、root の venv の 2 つを外した env で頼む — PATH の頭が worker の venv・VIRTUAL_ENV 無しで冷えた道と
;;                  食い違う(失敗ケース — この検が食い違いを測れている事を示す)。
;; 突き合わせる事(設計検証 lt-MEB74WM4BV22PDN26YAA1JVJMK — docs/design-checks の下):
;;   - 入口が見る PATH(全部)と VIRTUAL_ENV が 2 つの道で同じで、uv が選ぶ project の venv を名指す。
;;   - 入口を走らせる interpreter の venv(sys.prefix の実 path)が 2 つの道で同じで、VIRTUAL_ENV の実 path と同じ — 待ちの子を起こす venv
;;     (warm-launch)と entry-env と冷えた道の root の venv の hy が 1 つ(盲検 A — 3 つは別々に uv の既定に頼る)。実 path で比べるのは、冷えた道の
;;     interpreter が hy の script の shebang(venv を作った時の実 path)から決まり、symlink の綴りを持たないから。
;;   - 分かれた子が見る環境は entry-env と全く同じ — 分かれた子 A は頼みの env に足さず引かない(盲検 B — 待ちの子の環境から名を継ぐ実装を断る)。
;; worker の PATH の頭には本番と同じく worker の venv(boot の venv)が在る形にする: この checkout の venv の package を読む別の venv を
;; 検の置き場に作り、宿と判断が読む worker の環境の PATH の頭にその bin を置く(待ちの子が root の venv の外で起きれば interpreter の venv が
;; 食い違う)。root の project は symlink でこの checkout を指す(venv の名は --project の綴りのまま — 本物の uv が同じ venv で待ちの子を起こす事を実 path で比べる)。
;; 冷えた道の hy -m が入口を読めるよう、作業 dir(cwd)と待ちの子の起動の cwd は package の dir にする(本番の入口は root の venv から読む)。
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import shutil)
(import sys)
(import sysconfig)
(import tempfile)
(import dataclasses [replace])
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [await-handler slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_warm_process [os-warm-process-handler])
(import doeff_core_effects.process_exit [process-exit-handler])
(import doeff_core_effects.file_effects [ReadText WriteText FileFailed file-done])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment RunProcess ProcessOutcome StartProcess ProcessStarted ProcessNotStarted
                                            AwaitProcessExit PollProcess ProcessExited ProcessRunning ProcessNotChild])
(import doeff_core_effects.warm_effects [ForkFromWarm WarmForked WarmRefused AwaitWarmChildExit PollWarmChild WarmExited WarmRunning WarmLost])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.worker.intent.worker_model [CodeLayout WarmLaunch WarmChildView StartWarmChild StopWarmChild StopStage WorkerPolicy])
(import doeff_cluster.worker.protocol.observations [ObserveWarmChildren])
(import doeff_cluster.worker.protocol.warm_host [WarmSettings warm-host])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.warm_rules [WarmPlace warm-place warm-child-ready warm-launch warm-key-of])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-spans])
(import tests.env_fixtures [LOCK env-of])

;; repo の根(root の project の symlink の指す先 — venv は <根>/.venv)と、この package の dir(冷えた道の cwd・待ちの子の起動の cwd —
;; tests.fixtures を読める所)。
(val REPO (get (. (.resolve (Path __file__)) parents) 3))
(val PACKAGE (get (. (.resolve (Path __file__)) parents) 1))
(val ENTRY "tests.fixtures.warm_job")
;; 待ちの子の準備(module の読み込み)を待つ上限(0.05 秒ごとに観測を読み直す回数 — 60 秒)。
(val READY-TRIES 1200)


(defk task-spec [out]
  {:pre [(: out str)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口 warm_job の inspect(見え方を out に書く)を走らせる実行環境の task の宣言を組むため(project = repo app の根 — 宣言の読みは本番の
   runtime-env-of-json が通る形)。"
  (<- env RuntimeEnv (env-of "app" "lib" LOCK))
  (<- value dict (runtime-env->json env))
  (JobSpec "task/venv" ENTRY #("inspect" out) "rev1" :once True :runtime-env (json.dumps value :sort-keys True) :env-key "venv"))


(defk boot-venv-made [boot]
  {:pre [(: boot str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の boot の venv の代わりを boot に作るため: 同じ python の別の venv で、site-packages の .pth がこの checkout の venv の
   site-packages を足す(doeff を読めるが、interpreter の venv は root の venv と別 — 本番の boot の venv と同じ形)。"
  (<- made ProcessOutcome (RunProcess :argv #("uv" "venv" "--quiet" "--python" sys.executable boot)))
  (assert (= made.exit-code 0) (.format "boot の venv の代わりを作れない: {}" made))
  (val site (sysconfig.get-path "purelib" :vars {"base" boot "platbase" boot}))
  (val repo-site (sysconfig.get-path "purelib" :vars {"base" (str (/ REPO ".venv")) "platbase" (str (/ REPO ".venv"))}))
  (<- (file-done (WriteText (+ site "/checkout_venv.pth") (.format "import site; site.addsitedir({!r})\n" repo-site))))
  None)


(defk task-launch [spec root]
  {:pre [(: spec JobSpec) (: root str)] :post [(: % JobLaunch)] :tags {:context "doeff-cluster-test" :role "program"}}
  "task の起こし方を、本番の判断 job-launch で決めるため。許可表の worker の環境は宿と同じく ReadEnvironment で読む(PATH の頭は
   worker-path-led-by が boot の venv にする)。"
  (<- allowed tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec root "1-venv" 1 :python sys.executable :hy-command "hy" :extra-env {}
                                 :layout (CodeLayout) :allowed-env (dfor e allowed e.name e.value) :worker-pid (os.getpid) :program-path None
                                 :program-env HOST-CONTRACT.program-env :work-dir (str PACKAGE) :shim-grace-ms shim.shim-grace-ms
                                 :notice-env HOST-CONTRACT.notice-env))
  plan)


(defk entry-seen [out]
  {:pre [(: out str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入口 warm_job の inspect が書いた見え方(env と interpreter の venv ほか)を読むため。"
  (<- text (ReadText out))
  (assert (not (isinstance text FileFailed)) (.format "入口が見え方を書いていない: {}" text))
  (json.loads text))


(defk cold-path-seen [plan out log]
  {:pre [(: plan JobLaunch) (: out str) (: log str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "冷えた道: 答えの argv(shim の下の uv run … hy -m 入口)を宿 process_host と同じ flag で起こし、終わりを待って、入口の見え方を返すため。"
  (<- started (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv plan.argv :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path log :stderr-path log
                    :process-group True :hold-stdin True :reap-group True))
  (assert (isinstance started ProcessStarted) (.format "冷えた道の子を起こせない: {}" started))
  (<- (AwaitProcessExit started.pid))
  (<- ended (| ProcessExited ProcessRunning ProcessNotChild) (PollProcess started.pid))
  (<- shown (ReadText log))
  (assert (and (isinstance ended ProcessExited) (= ended.exit-code 0)) (.format "冷えた道の子が 0 で終わらない: {} log={}" ended shown))
  (<- seen dict (entry-seen out))
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


(defk venv-dropped [env]
  {:pre [(: env tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "前の形の頼みの env(許可表で絞っただけ — root の venv の 2 つを足す前)を、entry-env から uv-run-environ の足した 2 つを外して作るため:
   VIRTUAL_ENV を落とし、PATH の頭の venv の bin を外す(許可表の PATH は worker-path-led-by が boot の venv の bin で始めるので、残りは空でない)。"
  (tuple (gfor e env :if (!= e.name "VIRTUAL_ENV")
               (if (= e.name "PATH") (EnvEntry :name "PATH" :value (get (.split e.value ":" 1) 1)) e))))


(defk forked-seen [plan env socket out stem]
  {:pre [(: plan JobLaunch) (: env tuple) (: socket str) (: out str) (: stem str)] :post [(: % dict)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子から分かれる道: 宿 process_host と同じ欄(入口の後ろの引数・cwd)と、渡された env で待ちの子(socket)へ頼み、分かれた子の
   終わりを待って、入口の見え方を返すため。"
  (<- forked (| WarmForked WarmRefused)
      (ForkFromWarm :socket-path socket :entry ENTRY :args plan.entry-args :cwd plan.cwd :env env
                    :log-path (+ stem ".log") :exit-path (+ stem ".exit") :grace-seconds 1.0))
  (assert (isinstance forked WarmForked) (.format "待ちの子が分けるのを断った: {}" forked))
  (<- (AwaitWarmChildExit :pid forked.pid :start-ticks forked.start-ticks))
  (<- ended (| WarmExited WarmRunning WarmLost) (PollWarmChild :pid forked.pid :start-ticks forked.start-ticks :exit-path (+ stem ".exit")))
  (<- shown (ReadText (+ stem ".log")))
  (assert (and (isinstance ended WarmExited) (= ended.exit-code 0)) (.format "分かれた子が 0 で終わらない: {} log={}" ended shown))
  (<- seen dict (entry-seen out))
  seen)


(defk both-paths-seen [place warm-dir]
  {:pre [(: place str) (: warm-dir str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 つの宣言で、冷えた道・分かれる道・前の形の分かれる道を順に起こし、#(冷えた道の見え方 分かれる道の見え方 前の形の見え方 boot
   root の project の venv entry-env の名と値)を返すため(入口は同じ out に書くので、1 本ずつ終わりを待ってから読む)。待ちの子は本番の
   判断 warm-launch の起こし方で、宿の置き場の根 warm-dir の下に起こし、終わりに止める。"
  (val out (+ place "/seen.json"))
  (val root (+ place "/root"))  ; root の project = repo app の根 → この checkout への symlink(検の本体が置く — 頭註)
  (<- (boot-venv-made (+ place "/boot")))
  (<- spec JobSpec (task-spec out))
  (val key (warm-key-of spec))
  (<- plan JobLaunch (task-launch spec root))
  (<- cold dict (cold-path-seen plan out (+ place "/cold.log")))
  ;; 待ちの子の起こし方は本番の判断から(uv の --project)。起動の cwd(launch.root)と前もって読む module だけを検の物にする — 入口
  ;; tests.fixtures.warm_job を読めるように。
  (<- declared WarmLaunch (warm-launch key root #(spec) #()))
  (<- (StartWarmChild key (replace declared :root (str PACKAGE) :preload #(ENTRY))))
  (<- (warm-child-readied key))
  (<- warm-at WarmPlace (warm-place warm-dir key))
  (<- warm dict (forked-seen plan plan.entry-env warm-at.socket out (+ place "/forked")))
  (<- old dict (forked-seen plan (venv-dropped plan.entry-env) warm-at.socket out (+ place "/old")))
  (<- (StopWarmChild key StopStage.TERM "検の終わり"))
  #(cold warm old (+ place "/boot/bin") (+ root "/app/.venv") (dfor e plan.entry-env e.name e.value)))


(defhandler worker-path-led-by [#^ str boot-bin]
  ;; 引数に残す理由: boot の venv の代わりの置き場は検ごとの一時の dir。
  ;; 検の差し込み: 宿(warm_host)と判断の材料(task-launch)が読む worker の環境の PATH の頭に boot の venv の bin を置く — 本番の worker の
  ;; PATH の頭は worker の boot の venv(頭註)。
  (ReadEnvironment [names prefixes]
    (<- read tuple (ReadEnvironment names :prefixes prefixes))
    (resume (tuple (gfor e read (if (= e.name "PATH") (EnvEntry :name "PATH" :value (+ boot-bin ":" e.value)) e))))))


(defhandler bytecode-kept-out
  ;; 検の差し込み: 起こす子(冷えた道の shim と待ちの子)の環境に PYTHONDONTWRITEBYTECODE を足してから本物へ渡す。許可表は PYTHON* を継がない
  ;; ので根の conftest の固定が子へ届かず、--project のこの checkout の venv に子が bytecode を書く(test_warm_child_uv_reap.hy が -B を付けるのと
  ;; 同じ訳)。待ちの子から分かれた子は、待ちの子の起動の時の旗を継ぐ。比べる環境(入口が見る env)には触らない。
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
                                  os-warm-process-handler bytecode-kept-out (worker-path-led-by (+ place "/boot/bin"))
                                  (warm-host (WarmSettings :warm-dir warm-dir :log-dir (+ place "/logs") :generation "0b6f5e2a9c1d" :uv "uv"))]
                    program))))


(deftest test-the-entry-sees-the-same-root-venv-on-the-warm-and-the-cold-path
  (val place (tempfile.mkdtemp :prefix "venv-" :dir "/tmp"))  ; socket の path を短く保つ(pytest の tmp-path は長い)
  (val warm-dir (+ place "/warm"))
  (os.makedirs (+ place "/root"))
  (os.symlink (str REPO) (+ place "/root/app"))  ; root の project(repo app の根)はこの checkout を symlink で指す(頭註)
  (var seen #())
  (var real #())  ; 冷えた道と分かれる道の interpreter の venv と VIRTUAL_ENV の実 path(root の symlink を消す前に解く)
  (try
    (:= seen (on-real-processes place warm-dir (both-paths-seen place warm-dir)))
    (:= real (tuple (gfor p #((get (get seen 0) "prefix") (get (get seen 1) "prefix") (get seen 4)) (os.path.realpath p))))
    (finally
      (shutil.rmtree place :ignore-errors True)))
  (val cold (get seen 0))
  (val warm (get seen 1))
  (val old (get seen 2))
  (val boot (get seen 3))
  (val venv (get seen 4))
  (val entry (get seen 5))
  (val cold-env (get cold "env"))
  (val warm-env (get warm "env"))
  ;; 冷えた道: job-launch が root の project の venv を PATH の頭と VIRTUAL_ENV に置く(uv-run-environ — uv run の足し方の写し)。
  (assert (= #((get (.split (get cold-env "PATH") ":") 0) (.get cold-env "VIRTUAL_ENV")) #((+ venv "/bin") venv)) cold-env)
  ;; 分かれる道: 入口が見る PATH(全部)と VIRTUAL_ENV が冷えた道と同じ。
  (assert (= #((get warm-env "PATH") (.get warm-env "VIRTUAL_ENV")) #((get cold-env "PATH") (get cold-env "VIRTUAL_ENV")))
          (.format "warm={} cold={}" #((get warm-env "PATH") (.get warm-env "VIRTUAL_ENV")) #((get cold-env "PATH") (get cold-env "VIRTUAL_ENV"))))
  ;; 入口を走らせる interpreter の venv が 2 つの道で同じで、VIRTUAL_ENV と同じ(待ちの子を起こす venv・entry-env・冷えた道の uv が 1 つ —
  ;; 実 path で比べる・頭註)。
  (assert (= (cut real 0 2) #((get real 2) (get real 2)))
          (.format "sys.prefix の実 path cold={} warm={}・VIRTUAL_ENV の実 path={}" (get real 0) (get real 1) (get real 2)))
  ;; 分かれた子が見る環境は entry-env と全く同じ(分かれた子 A は頼みの env に足さず引かない)。
  (assert (= warm-env entry) (.format "頼みに無い名 {}・頼みから消えた名 {}" (sorted (- (set warm-env) (set entry))) (sorted (- (set entry) (set warm-env)))))
  ;; 前の形(答えの env のまま頼む): PATH の頭が worker の venv・VIRTUAL_ENV 無しで、冷えた道と食い違う。
  (assert (= #((get (.split (get (get old "env") "PATH") ":") 0) (.get (get old "env") "VIRTUAL_ENV")) #(boot None)) old))
