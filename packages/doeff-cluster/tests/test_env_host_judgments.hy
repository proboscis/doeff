;; worker の実行環境の root の言い換え env-host(worker/protocol/env_store)の判断の検 — 準備の子 process を起こさない速い版(#2795)。
;;
;; env-host は本物の file system の答え手(os-file-handler)で tmp の dir の上に root を置き、準備は準備の道具(ENV-TOOL)を子 process として
;; 起こす。ここでは、その起こし方(StartProcess・PollProcess)にだけ同じ process の中の答え手 inline-env-tool を挟む: 起こした頼みを
;; 数え、検が終われと言うまで「走っている」と答え、終わる時に頼みの root へ完成マーカーと答えの file を本物の綴り(env-marker->json・
;; answer-json)で書く。準備の中身(git・uv・bytecode)は準備の Program の検(test_env_prepare.hy — 速い模擬)と、本物の縁の検
;; (test_env_careful.hy)が見る。
;;   6 同じキーの準備を 2 回頼む: 準備の道具は 1 回だけ起き(走っている準備を起こし直さない)、終われば READY
;;   8 先読み: 温める表の env を job の前に準備し、task が来た最初の拍で子を起こす(準備を待たない)— 温めていない env は準備を起こす
;;   9 roots の合計が上限を越える: 固定された root・project ごとの新しい 2 つ(今の版と戻し先の版)・worker が作っていない dir は残り、
;;     固定されていない古い root が消える。合計は root ごとの大きさの和で hardlink を重ねて数える(#3732)。共有の disk の空きが最低を
;;     割っても root は消さず、heartbeat で exhausted を名乗り、準備の頼みに空きの最低を載せる(準備の process が disk-full で断る)
;;   準備の期限(#3515): 進みの印が動いている job の準備は、起こしてから長くても止めない・完成の答えを書いて終わりの処理の途中の準備は
;;     期限の拍で止めない・進みの印が停滞の秒(600 秒)動かない準備は今どおり止める。時計は仮想の時計(sim-time-handler)で、進みの印の
;;     file の時刻は os.utime でその時計の物差しに置く。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import json)
(import os)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.file_effects [ReadText WriteText MakeDirectory file-done])
(import doeff_core_effects.process_effects [StartProcess PollProcess StopProcess ProcessStarted ProcessRunning ProcessExited])
(import doeff_time [Delay SetTime SimClock sim-time-handler sync-time-handler])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailureKind])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout PythonProject RuntimeEnv CHILD-PROTOCOL])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json runtime-env-of-json env-key])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.env_prepare_model [EnvMarker EnvReady])
(import doeff_cluster.worker.core.env_prepare [env-marker->json])
(import doeff_cluster.worker.protocol.env_translation [answer-json])
(import doeff_cluster.worker.protocol.env_store [EnvSettings ENV-TOOL env-host env-root])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs])
(import doeff_cluster.worker.protocol.declared [task-spec])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView PrepareEnv SweepEnvs EnvReport StartJob WarmEnv WorkerPolicy WorldView
  WarmChildView WarmChildMark])
(import doeff_cluster.worker.core.policy [plan])
(import doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.foundation.process_versions [process-versions])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.clock_fixtures [clock-at])

(val PLATFORM "linux-x86_64")
(val FIRST-PID 70000)   ; 準備の道具の偽の pid の始まり(本物の子の pid と取り違えないよう、答え手が立てた物だけを数える)


(defrecord ToolRun
  "走っている準備の道具 1 本に env-host が渡した file(argv から読む): request = 頼みの file・result = 答えの file・progress = 進みの印の file。"
  (#^ str request)
  (#^ str result)
  (#^ str progress))


(defclass ToolRuns []
  "inline-env-tool の記録: launches = 起こした準備の頼みの file の path(起こした順)・running = 偽の pid → ToolRun・
   released = 走っている準備を終わらせてよいか(検が立てる)・stopped = env-host が止めた準備の偽の pid(止めた順)。"
  (defn #^ None __init__ [self]
    (setv #^ (get tuple #(str ...)) self.launches #())
    (setv #^ (get dict #(int ToolRun)) self.running {})
    (setv #^ bool self.released False)
    (setv #^ (get tuple #(int ...)) self.stopped #())
    None))


(defk finish-preparation [request-path result-path]
  {:pre [(: request-path str) (: result-path str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "頼みの root に完成マーカーと、成功の答えの file を本物の綴りで書くため(準備の道具が全部の処理ステージを通った時と同じ形)。"
  (<- text str (ReadText request-path))
  (val request (json.loads text))
  (<- env RuntimeEnv (runtime-env-of-json (get request "env")))
  (val root (get request "root"))
  (<- (file-done (MakeDirectory root)))
  (<- marker dict (env-marker->json (EnvMarker :env env :key (get request "key") :platform (get request "platform") :stages #()
                                               :downloaded 0 :built 0 :interpreter "" :child-protocol CHILD-PROTOCOL)))
  (<- (file-done (WriteText (+ root "/" ENV-MARKER) (json.dumps marker :ensure-ascii False))))
  (<- answer dict (answer-json (EnvReady :env env :key (get request "key") :root root :stages #() :downloaded 0 :built 0
                                         :interpreter "")))
  (<- (file-done (WriteText result-path (json.dumps answer :ensure-ascii False))))
  None)


(defhandler inline-env-tool [#^ ToolRuns runs]
  ;; 引数に残す理由: 検ごとに別の記録(起こした数と、終わらせてよいかの印)を並べる。
  ;; 準備の道具(argv に ENV-TOOL)の起こし方だけに答え、ほかの子 process(uv の cache の prune)は外側の本物の答え手へ渡す。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (if (in ENV-TOOL argv)
        (do (val pid (+ FIRST-PID (len runs.launches)))
            (val request (get argv (+ (.index argv "--request") 1)))
            (val result (get argv (+ (.index argv "--result") 1)))
            (val progress (get argv (+ (.index argv "--progress") 1)))
            (setv runs.launches (+ runs.launches #(request)))
            (setv runs.running (| runs.running {pid (ToolRun :request request :result result :progress progress)}))
            (resume (ProcessStarted :pid pid)))
        (do (<- answer (StartProcess :argv argv :cwd cwd :env env :env-mode env-mode :env-drop env-drop :stdout-path stdout-path
                                     :stderr-path stderr-path :process-group process-group :hold-stdin hold-stdin
                                     :reap-group reap-group))
            (resume answer))))
  (PollProcess [pid]
    (cond
      (not-in pid runs.running)
        (do (<- answer (PollProcess pid))
            (resume answer))
      runs.released
        (do (val run (get runs.running pid))
            (<- (finish-preparation run.request run.result))
            (setv runs.running (dfor #(k v) (.items runs.running) :if (!= k pid) k v))
            (resume (ProcessExited :pid pid :exit-code 0)))
      True (resume (ProcessRunning :pid pid))))
  (StopProcess [pid stop-grace]
    ;; 期限で止めた準備を数え、終わった子として答える(SIGTERM の終わり)。ほかの子は外側の本物の答え手へ渡す。
    (if (in pid runs.running)
        (do (setv runs.stopped (+ runs.stopped #(pid)))
            (setv runs.running (dfor #(k v) (.items runs.running) :if (!= k pid) k v))
            (resume (ProcessExited :pid pid :exit-code -15)))
        (do (<- answer (StopProcess :pid pid :stop-grace stop-grace))
            (resume answer)))))


(val NO-CAP (** 2 62))   ; roots の合計の上限を掃除の起きない大きさに置く(掃除を見ない検)


(defk settings-at [base [roots-cap-bytes NO-CAP] [min-free-bytes 0]]
  {:pre [(: base Path) (: roots-cap-bytes int) (: min-free-bytes int)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "program"}}
  "tmp の dir の上の env-host の設定(準備の道具は inline-env-tool が答えるので起きない・uv の cache の prune は何もしない true)。"
  (EnvSettings :state (str (/ base "state")) :hy-command "hy" :platform PLATFORM :code-prepare PREPARE-TOOL :uv "true"
               :roots-cap-bytes roots-cap-bytes :min-free-bytes min-free-bytes))


(defk declared [n]
  {:pre [(: n int)] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "program"}}
  "project app(同じ url と path)の n 番目の commit の宣言。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "file:///remotes/app.git" :commit (* (str n) 40)))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(defk declared-text [env]
  {:pre [(: env RuntimeEnv)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "PrepareEnv に渡す宣言の JSON の文字列(worker の判断が渡すのと同じ — 鍵の順を揃える)。"
  (<- body dict (runtime-env->json env))
  (json.dumps body :sort-keys True :ensure-ascii False))


(defk env-key-of [env]
  {:pre [(: env RuntimeEnv)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宣言の env のキー(env-<キー> — worker の観測と PrepareEnv の鍵)。"
  (<- key str (env-key env PLATFORM))
  (+ "env-" key))


(defk view-of [views key]
  {:pre [(: views tuple) (: key str)] :post [(: % (| CodeView None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "観測の列から key の観測を引くため(無ければ None)。"
  (next (gfor v views :if (= v.revision key) v) None))


;; --- 6 同じキーの準備を 2 回 ------------------------------------------------------------------------

(defk twice-then-release [runs key text]
  {:pre [(: runs ToolRuns) (: key str) (: text str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "同じキーの準備を 2 回頼んで観測し、終わらせてからもう 1 回観測するため。答え = #(走っている間の観測 終わった後の観測)。"
  (<- (PrepareEnv key text))
  (<- (PrepareEnv key text))
  (<- during tuple (ObserveEnvs))
  (setv runs.released True)
  (<- after tuple (ObserveEnvs))
  #(during after))


(defk two-keys [key text other-key other-text]
  {:pre [(: key str) (: text str) (: other-key str) (: other-text str)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "反例の筋書き: 別のキーの準備を 1 回ずつ頼んで観測するため(準備は 2 本起きる)。"
  (<- (PrepareEnv key text))
  (<- (PrepareEnv other-key other-text))
  (<- views tuple (ObserveEnvs))
  views)


(deftest test-scenario-6-two-requests-of-one-key-launch-one-preparation [tmp-path]
  ;; 準備の道具は 1 回だけ起きる(走っている準備を起こし直さない)・走っている間は PREPARING・終われば root の path を持つ READY。
  ;; 反例: 別のキーの頼みは別の準備を起こす(数え方が頼みの回数を数えていることの確かめ)。
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- env RuntimeEnv (declared 1))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (<- seen tuple (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (twice-then-release runs key text)))
  (assert (= (len runs.launches) 1) runs.launches)
  (<- during (| CodeView None) (view-of (get seen 0) key))
  (assert (and (is-not during None) (= during.state CodeState.PREPARING)) seen)
  (<- after (| CodeView None) (view-of (get seen 1) key))
  (<- root str (env-root settings key))
  (assert (and (is-not after None) (= after.state CodeState.READY) (= after.path root)) seen)
  ;; 反例: 別のキー
  (val other-runs (ToolRuns))
  (<- other RuntimeEnv (declared 2))
  (<- other-key str (env-key-of other))
  (<- other-text str (declared-text other))
  (<- (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool other-runs)
                      (env-host (EnvSettings :state (str (/ tmp-path "other")) :hy-command "hy" :platform PLATFORM
                                             :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes NO-CAP))]
        (two-keys key text other-key other-text)))
  (assert (= (len other-runs.launches) 2) other-runs.launches))


;; --- 8 先読み ---------------------------------------------------------------------------------------

(defk warm-then-first-tick [runs warm spec cold-spec]
  {:pre [(: runs ToolRuns) (: warm WarmEnv) (: spec JobSpec) (: cold-spec JobSpec)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の判断(plan)を env-host の観測の上で回す筋: job の無い拍の判断・先読みを頼んで終わらせた後の観測・task が来た最初の拍の
   判断・温めていない env の task の最初の拍の判断を返すため。温めた root には待ちの子も起きている(温める表の root は待ちの子を
   起こす — #3646)ので、task の来た拍の観測には、その root の準備済みの待ちの子を置く。"
  (val policy (WorkerPolicy))
  (<- before tuple (ObserveEnvs))
  (val warming (! (plan 0 #() (WorldView before #()) {} policy :warm #(warm))))
  (<- (PrepareEnv warm.key warm.runtime-env :warm True))
  (setv runs.released True)
  (<- after tuple (ObserveEnvs))
  (val warmed (WorldView after #() :warm-children #((WarmChildView :key warm.key :pid 1 :started-ms 0
                                                                   :mark (WarmChildMark :threads 1 :vm-live #(0 0 0))))))
  (val first (! (plan 1 #(spec) warmed {} policy :warm #(warm))))
  (val cold-first (! (plan 2 #(cold-spec) warmed {} policy :warm #(warm))))
  #(warming after first cold-first))


(deftest test-scenario-8-a-warmed-root-starts-the-task-on-the-first-tick [tmp-path]
  ;; 温める表を受けた worker は job が無くても準備を起こす(先読み)・整った root は置き場の path を持つ READY・task が来た最初の拍で
  ;; 子を起こす(PrepareEnv を挟まない)。反例: 温めていない env の task は、最初の拍で準備を起こす(準備が task の待ちに入る)。
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- env RuntimeEnv (declared 1))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (val warm (WarmEnv :key key :runtime-env text))
  (val tasks (/ tmp-path "tasks"))
  (<- declared-1 dict (runtime-env->json env))
  (<- spec (task-spec {"id" "t8" "revision" "" "versions" (! (process-versions os.environ)) "program" SAMPLE-TASK-PROGRAM "runtimeEnv" declared-1}
                      tasks))
  (<- cold RuntimeEnv (declared 2))
  (<- declared-2 dict (runtime-env->json cold))
  (<- cold-spec (task-spec {"id" "t9" "revision" "" "versions" (! (process-versions os.environ)) "program" SAMPLE-TASK-PROGRAM "runtimeEnv" declared-2}
                           tasks))
  (<- seen tuple (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (warm-then-first-tick runs warm spec cold-spec)))
  (assert (= (get seen 0) #((PrepareEnv warm.key warm.runtime-env :warm True))) (get seen 0))
  (<- view (| CodeView None) (view-of (get seen 1) key))
  (<- root str (env-root settings key))
  (assert (and (is-not view None) (= view.state CodeState.READY) (= view.path root)) (get seen 1))
  (assert (= (get seen 2) #((StartJob spec 1 root :warm-key key))) (get seen 2))
  (assert (is-not cold-spec.runtime-env None) cold-spec)
  (assert (= (get seen 3) #((PrepareEnv (code-key cold-spec) cold-spec.runtime-env))) (get seen 3))
  (assert (= (len runs.launches) 1) runs.launches))


;; --- 9 掃除 -----------------------------------------------------------------------------------------

(defk made-root [settings name env made-s used-s]
  {:pre [(: settings EnvSettings) (: name str) (: env RuntimeEnv) (: made-s int) (: used-s int)] :post [(: % Path)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "準備の済んだ root を置き場に置くため(完成マーカーは本物の綴り・作った時刻 = マーカーの時刻・使った時刻 = .last-used の時刻)。"
  (val root (/ (Path settings.state) "roots" name))
  (.mkdir root :parents True)
  (<- marker dict (env-marker->json (EnvMarker :env env :key name :platform PLATFORM :stages #() :downloaded 0 :built 0
                                               :interpreter "" :child-protocol CHILD-PROTOCOL)))
  (.write-text (/ root ENV-MARKER) (json.dumps marker :ensure-ascii False))
  (os.utime (/ root ENV-MARKER) #(made-s made-s))
  (.write-text (/ root ".last-used") "")
  (os.utime (/ root ".last-used") #(used-s used-s))
  root)


;; 掃除の数えと消しはループの外の task で走る(#3715)— 筋書きは SweepEnvs を短い間を置いて撃ち続ける(worker の拍の代わり)。
(val SWEEP-ROUNDS 50)
(val SWEEP-PAUSE-SECONDS 0.02)


(defk swept-rounds [pinned]
  {:pre [(: pinned frozenset)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で SweepEnvs を SWEEP-ROUNDS 回、SWEEP-PAUSE-SECONDS の間を置いて撃つため(掃除 1 回が数え・選び・消しまで進む)。"
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs pinned))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  None)


(deftest test-scenario-9-the-sweep-keeps-pinned-recent-two-and-foreign-dirs [tmp-path]
  ;; 同じ project の root 4 つ: a(固定・最も古く使った)・b・c(1 つ前に動いていた版 = 戻し先)・d(今の版)。worker が作っていない dir
  ;; (キーの形の名で完成マーカーの無い dir と、別の名の dir)。roots の合計の上限を 0 に置く(越えた状態)。
  ;; 反例 = 直す前の形(project ごとに完成の時刻の最新 1 つだけを守る)は戻し先の c を消す — 下の c の断言が赤。
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path :roots-cap-bytes 0))
  (<- env-1 RuntimeEnv (declared 1))
  (<- env-2 RuntimeEnv (declared 2))
  (<- env-3 RuntimeEnv (declared 3))
  (<- env-4 RuntimeEnv (declared 4))
  (<- a Path (made-root settings "aaaaaaaaaaaaaaaaaaaaaaaa" env-1 100 1000))
  (<- b Path (made-root settings "bbbbbbbbbbbbbbbbbbbbbbbb" env-2 200 2000))
  (<- c Path (made-root settings "cccccccccccccccccccccccc" env-3 300 3000))
  (<- d Path (made-root settings "dddddddddddddddddddddddd" env-4 400 4000))
  (val foreign (/ (Path settings.state) "roots" "0123456789abcdef01234567"))
  (.mkdir foreign)
  (.write-text (/ foreign "keep.txt") "not ours\n")
  (val notes (/ (Path settings.state) "roots" "notes"))
  (.mkdir notes)
  (<- (scheduled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (swept-rounds (frozenset #((+ "env-" a.name)))))))
  (assert (.exists a) "固定された root は残る")
  (assert (not (.exists b)) "固定されていない古い root は消える")
  (assert (.exists c) "1 つ前に動いていた版の root(戻し先)は残る")
  (assert (.exists d) "今の版の root は残る")
  (assert (and (.exists foreign) (.exists notes)) "worker が作っていない dir は消さない")
  (assert (= runs.launches #()) runs.launches))


(val LIB-BYTES 2000)   ; 合計の上限の検で root ごとに置く file の大きさ(完成マーカーより大きく — 下の前提の断言)


(defk tree-bytes [root]
  {:pre [(: root Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "root の下の file の大きさの和を、掃除の数え(MeasureTree — hardlink は重ねて数える)と同じ物差しで返すため(上限の値を検の中で決める)。"
  (sum (gfor #(top _ files) (os.walk root) name files (. (os.lstat (os.path.join top name)) st-size))))


(deftest test-roots-over-the-cap-lose-the-oldest-unpinned-root-and-hardlinks-count-twice [tmp-path]
  ;; 同じ project の root 4 つ(最後に使った時刻 a < b < c < d)に LIB-BYTES の file を 1 つずつ置き、d の file は c の file の hardlink
  ;; (root どうしが木と .pyc を共有する形 — #3671・#3727)。固定は無い。上限 = 重ねて数えた合計から a の半分を引いた値:
  ;;   - 重ねて数えると合計は上限を越え、古い a を 1 つ消せば上限の内へ戻る — a だけが消え、b・c・d は残る
  ;;   - inode を 1 度だけ数える合計(c と d の共有を 1 度)は上限の内(前提の断言)— 重ねて数える形は、それより早めに消す
  (val runs (ToolRuns))
  (<- probe EnvSettings (settings-at tmp-path))
  (<- env-1 RuntimeEnv (declared 1))
  (<- env-2 RuntimeEnv (declared 2))
  (<- env-3 RuntimeEnv (declared 3))
  (<- env-4 RuntimeEnv (declared 4))
  (<- a Path (made-root probe "aaaaaaaaaaaaaaaaaaaaaaaa" env-1 100 1000))
  (<- b Path (made-root probe "bbbbbbbbbbbbbbbbbbbbbbbb" env-2 200 2000))
  (<- c Path (made-root probe "cccccccccccccccccccccccc" env-3 300 3000))
  (<- d Path (made-root probe "dddddddddddddddddddddddd" env-4 400 4000))
  (for [root #(a b c)]
    (.write-bytes (/ root "lib.py") (* b"x" LIB-BYTES)))
  (os.link (/ c "lib.py") (/ d "lib.py"))
  (var sizes #())
  (for [root #(a b c d)]
    (<- size int (tree-bytes root))
    (:= sizes (+ sizes #(size))))
  (val counted (sum sizes))
  (val cap (- counted (// (get sizes 0) 2)))
  (assert (<= (- counted LIB-BYTES) cap) (.format "前提: inode を 1 度だけ数える合計 {} は上限 {} の内" (- counted LIB-BYTES) cap))
  (<- settings EnvSettings (settings-at tmp-path :roots-cap-bytes cap))
  (<- (scheduled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (swept-rounds (frozenset)))))
  (assert (not (.exists a)) "上限を越えた合計は、固定されていない最も古い root から消える")
  (assert (and (.exists b) (.exists c) (.exists d)) "上限の内へ戻ったら、それより新しい root は消さない")
  (assert (= (. (os.stat (/ d "lib.py")) st-nlink) 2) "共有の file は残る c と d の間で hardlink のまま"))


(defk report-after-sweeps [key text]
  {:pre [(: key str) (: text str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "掃除の係を回してから新しい env の準備を頼み、heartbeat で名乗る root の姿を返すため。"
  (<- (swept-rounds (frozenset)))
  (<- (PrepareEnv key text))
  (<- report dict (EnvReport))
  report)


(deftest test-a-shared-disk-below-the-free-minimum-refuses-preparation-and-keeps-roots [tmp-path]
  ;; 共有の disk の空きが最低を割る(最低を disk の大きさより上に置く)が、roots の合計は上限の内。root は消さない(以前は空きの下限で
  ;; 消し続けた)・heartbeat は exhausted を名乗る・準備の頼みは空きの最低を載せる(準備の process の stage-disk がこの値で disk-full と
  ;; 断る — test_env_prepare の disk-full の検)。反例 = 直す前の形(空きが下限を切れば固定されていない root を消す)は a と b を消す — 赤。
  (val runs (ToolRuns))
  (val floor (** 2 62))
  (<- settings EnvSettings (settings-at tmp-path :min-free-bytes floor))
  (<- env-1 RuntimeEnv (declared 1))
  (<- env-2 RuntimeEnv (declared 2))
  (<- env-3 RuntimeEnv (declared 3))
  (<- a Path (made-root settings "aaaaaaaaaaaaaaaaaaaaaaaa" env-1 100 1000))
  (<- b Path (made-root settings "bbbbbbbbbbbbbbbbbbbbbbbb" env-2 200 2000))
  (<- c Path (made-root settings "cccccccccccccccccccccccc" env-3 300 3000))
  (<- fresh RuntimeEnv (declared 5))
  (<- key str (env-key-of fresh))
  (<- text str (declared-text fresh))
  (<- report dict (scheduled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler
                                             (inline-env-tool runs) (env-host settings)]
                               (report-after-sweeps key text))))
  (assert (and (.exists a) (.exists b) (.exists c)) "共有の disk の空きが最低を割っても root は消さない")
  (assert (= (get report "capacity") "exhausted") report)
  (assert (= (len runs.launches) 1) runs.launches)
  (val request (json.loads (.read-text (Path (get runs.launches 0)))))
  (assert (= (get request "minFreeBytes") floor) request))


;; --- 準備の期限(#3515)------------------------------------------------------------------------------
;; 実例: 温い job の準備が 300 秒の期限(起こした時刻から数える合計)に掛かり、bytecode の処理ステージ(267.9 秒)の後の確かめを終えて
;; 完成の答えを書いた 0.4 秒後に prepare-timeout で止められた。期限は進みの印の時刻から数える停滞(600 秒)だけで、完成の答えを
;; 書いた準備は止めない。

(val CLOCK-START-MS 1760000000000)   ; 仮想の時計の起点(epoch ミリ秒 — 進みの印の file の時刻もこの物差しで置く)
(val ONE-SECOND-MS 1000)


(defrecord DeadlineSeen
  "期限の筋書きの観測: views = 期限を確かめた拍ごとの ObserveEnvs の答え(拍の順)・after = 子を終わらせた後の観測(終わらせない筋書きは #())。"
  (#^ (get tuple #((get tuple #(CodeView ...)) ...)) views)
  (#^ (get tuple #(CodeView ...)) after))


(defk at-second [seconds]
  {:pre [(: seconds int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "仮想の時計の起点から seconds 秒後の時刻(epoch ミリ秒)を返すため(筋書きの拍と進みの印の時刻を同じ物差しで書く)。"
  (+ CLOCK-START-MS (* seconds ONE-SECOND-MS)))


(defk move-clock [seconds]
  {:pre [(: seconds int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "仮想の時計を起点から seconds 秒後へ進めるため(env-host の今の時刻 = GetTime の答え)。"
  (<- at int (at-second seconds))
  (<- (SetTime (datetime-of-epoch-ms at)))
  None)


(defk running-tool [runs]
  {:pre [(: runs ToolRuns)] :post [(: % ToolRun)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "走っている準備の道具 1 本の file を返すため(1 本でなければ — env-host が止めた時など — 止めた pid を添えて落ちる)。"
  (val running (tuple (.values runs.running)))
  (assert (= (len running) 1) (.format "走っている準備の道具が 1 本でない(env-host が止めた pid: {})" runs.stopped))
  (get running 0))


(defk touch-progress [runs seconds]
  {:pre [(: runs ToolRuns) (: seconds int)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "走っている準備の道具(1 本)の代わりに進みの印を触るため — 中身は処理ステージの名のまま、時刻を起点から seconds 秒後に置く
   (bytecode の処理ステージが repo の木ごとに印を触るのと同じ形)。"
  (<- run ToolRun (running-tool runs))
  (<- at int (at-second seconds))
  (.write-text (Path run.progress) "bytecode\n")
  (os.utime run.progress #((/ at 1000) (/ at 1000)))
  None)


(defk answer-while-running [runs]
  {:pre [(: runs ToolRuns)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "走っている準備の道具(1 本)の代わりに、子を走らせたまま完成マーカーと完成の答えを書くため(答えを書き終えて終わりの処理の途中)。"
  (<- run ToolRun (running-tool runs))
  (<- (finish-preparation run.request run.result))
  None)


(defk observe-at [seconds]
  {:pre [(: seconds int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "仮想の時計を起点から seconds 秒後へ進めて観測するため。"
  (<- (move-clock seconds))
  (<- views tuple (ObserveEnvs))
  views)


(defk deadline-on [settings runs program]
  {:pre [(: settings EnvSettings) (: runs ToolRuns) (: program Program)] :post [(: % DeadlineSeen)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "期限の筋書き program を、仮想の時計(起点 CLOCK-START-MS)と tmp の dir の上の env-host の下で走らせるため。"
  (<- clock SimClock (clock-at CLOCK-START-MS))
  (<- seen DeadlineSeen (with-handlers [(state) (sim-time-handler :clock clock) slog-handler os-file-handler subprocess-handler
                                        (inline-env-tool runs) (env-host settings)]
                          program))
  seen)


(defk warm-job-root [settings]
  {:pre [(: settings EnvSettings)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "同じ lock と Python の完成済みの root を 1 つ置くため(次の準備は温い job の準備 — 依存と bytecode を引き継げる)。"
  (<- env RuntimeEnv (declared 1))
  (<- (made-root settings "aaaaaaaaaaaaaaaaaaaaaaaa" env 100 100))
  None)


;; (a) 完成の答えを書いた準備は、期限の拍で止めない。

(defk answered-scenario [runs key text]
  {:pre [(: runs ToolRuns) (: key str) (: text str)] :post [(: % DeadlineSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job の準備を起こし、子を走らせたまま完成の答えを書き、進みの印を触らずに起点から 2000 秒後(停滞の 600 秒も、前の冷たい期限の
   1800 秒も越える)に観測し、子を終わらせてもう 1 回観測するため。"
  (<- (PrepareEnv key text))
  (<- (answer-while-running runs))
  (<- during tuple (observe-at 2000))
  (setv runs.released True)
  (<- after tuple (observe-at 2001))
  (DeadlineSeen :views #(during) :after after))


(deftest test-a-preparation-that-wrote-ready-is-not-stopped-at-the-deadline [tmp-path]
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- env RuntimeEnv (declared 2))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (<- seen DeadlineSeen (deadline-on settings runs (answered-scenario runs key text)))
  (<- during (| CodeView None) (view-of (get seen.views 0) key))
  (assert (= runs.stopped #()) (.format "完成の答えを書いた準備を止めた: {}" during))
  (assert (and (is-not during None) (= during.state CodeState.PREPARING)) during)
  (<- after (| CodeView None) (view-of seen.after key))
  (<- root str (env-root settings key))
  (assert (and (is-not after None) (= after.state CodeState.READY) (= after.path root)) after))


;; (b) 進みの印が動いている job の準備は、起こしてから 300 秒・600 秒・1800 秒を越えても止めない。

(defk moving-scenario [runs key text]
  {:pre [(: runs ToolRuns) (: key str) (: text str)] :post [(: % DeadlineSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "温い job の準備を起こし、進みの印を触りながら(290 秒・690 秒・1790 秒)、前の温い期限 300 秒・停滞の 600 秒・前の冷たい期限
   1800 秒を越えた拍(301 秒・700 秒・1801 秒)で観測するため。"
  (<- (PrepareEnv key text))
  (<- (touch-progress runs 290))
  (<- past-warm tuple (observe-at 301))
  (<- (touch-progress runs 690))
  (<- past-stall tuple (observe-at 700))
  (<- (touch-progress runs 1790))
  (<- past-cold tuple (observe-at 1801))
  (DeadlineSeen :views #(past-warm past-stall past-cold) :after #()))


(deftest test-a-moving-job-preparation-is-not-stopped-however-long-it-runs [tmp-path]
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- (warm-job-root settings))
  (<- env RuntimeEnv (declared 2))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (<- seen DeadlineSeen (deadline-on settings runs (moving-scenario runs key text)))
  (assert (= runs.stopped #()) (.format "進んでいる準備を止めた: {}" seen.views))
  (for [views seen.views]
    (<- view (| CodeView None) (view-of views key))
    (assert (and (is-not view None) (= view.state CodeState.PREPARING)) view)))


;; (c) 進みの印が停滞の秒(600 秒)動かない準備は、今どおり止める(止めなさすぎにしない)。

(defk stalled-scenario [runs key text]
  {:pre [(: runs ToolRuns) (: key str) (: text str)] :post [(: % DeadlineSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "温い job の準備を起こし、100 秒後に進みの印を 1 回だけ触り、それから 601 秒後(起点から 701 秒)に観測するため。"
  (<- (PrepareEnv key text))
  (<- (touch-progress runs 100))
  (<- stalled tuple (observe-at 701))
  (DeadlineSeen :views #(stalled) :after #()))


(deftest test-a-preparation-whose-mark-stands-still-for-the-stall-is-stopped [tmp-path]
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path))
  (<- (warm-job-root settings))
  (<- env RuntimeEnv (declared 2))
  (<- key str (env-key-of env))
  (<- text str (declared-text env))
  (<- seen DeadlineSeen (deadline-on settings runs (stalled-scenario runs key text)))
  (assert (= runs.stopped #(FIRST-PID)) runs.stopped)
  (<- view (| CodeView None) (view-of (get seen.views 0) key))
  (assert (and (is-not view None) (= view.state CodeState.FAILED) (is-not view.failure None)
               (= view.failure.kind EnvFailureKind.PREPARE-TIMEOUT))
          view))
