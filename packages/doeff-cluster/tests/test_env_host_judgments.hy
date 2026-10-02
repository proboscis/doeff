;; worker の実行環境の root の言い換え env-host(worker/protocol/env_store)の判断の検 — 準備の子 process を起こさない速い版(#2795)。
;;
;; env-host は本物の file system の答え手(os-file-handler)で tmp の dir の上に root を置き、準備は準備の道具(ENV-TOOL)を子 process として
;; 起こす。ここでは、その起こし方(StartProcess・PollProcess)にだけ同じ process の中の答え手 inline-env-tool を挟む: 起こした頼みを
;; 数え、検が終われと言うまで「走っている」と答え、終わる時に頼みの root へ完成マーカーと答えの file を本物の綴り(env-marker->json・
;; answer-json)で書く。準備の中身(git・uv・bytecode)は準備の Program の検(test_env_prepare.hy — 速い模擬)と、本物の縁の検
;; (test_env_careful.hy)が見る。
;;   6 同じキーの準備を 2 回頼む: 準備の道具は 1 回だけ起き(走っている準備を起こし直さない)、終われば READY
;;   8 先読み: 温める表の env を job の前に準備し、task が来た最初の拍で子を起こす(準備を待たない)— 温めていない env は準備を起こす
;;   9 空きが下限を切る: 固定された root・project ごとの最新・worker が作っていない dir は残り、固定されていない古い root が消える
(require doeff-hy.macros [deftest defk defhandler <- val var])
(import json)
(import os)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.file_effects [ReadText WriteText MakeDirectory file-done])
(import doeff_core_effects.process_effects [StartProcess PollProcess ProcessStarted ProcessRunning ProcessExited])
(import doeff_time [sync-time-handler])
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
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView PrepareEnv SweepEnvs StartJob WarmEnv WorkerPolicy WorldView])
(import doeff_cluster.worker.core.policy [plan])
(import doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.foundation.process_versions [current-versions])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val PLATFORM "linux-x86_64")
(val FIRST-PID 70000)   ; 準備の道具の偽の pid の始まり(本物の子の pid と取り違えないよう、答え手が立てた物だけを数える)


(defclass ToolRuns []
  "inline-env-tool の記録: launches = 起こした準備の頼みの file の path(起こした順)・running = 偽の pid → #(頼みの file 答えの file)・
   released = 走っている準備を終わらせてよいか(検が立てる)。"
  (defn #^ None __init__ [self]
    (setv #^ (get tuple #(str ...)) self.launches #())
    (setv #^ (get dict #(int (get tuple #(str str)))) self.running {})
    (setv #^ bool self.released False)
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
            (setv runs.launches (+ runs.launches #(request)))
            (setv runs.running (| runs.running {pid #(request result)}))
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
        (do (val paths (get runs.running pid))
            (<- (finish-preparation (get paths 0) (get paths 1)))
            (setv runs.running (dfor #(k v) (.items runs.running) :if (!= k pid) k v))
            (resume (ProcessExited :pid pid :exit-code 0)))
      True (resume (ProcessRunning :pid pid)))))


(defk settings-at [base [sweep-floor-bytes None]]
  {:pre [(: base Path) (: sweep-floor-bytes (| int None))] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "program"}}
  "tmp の dir の上の env-host の設定(準備の道具は inline-env-tool が答えるので起きない・uv の cache の prune は何もしない true)。"
  (EnvSettings :state (str (/ base "state")) :hy-command "hy" :platform PLATFORM :code-prepare PREPARE-TOOL :uv "true"
               :sweep-floor-bytes sweep-floor-bytes))


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
                                             :code-prepare PREPARE-TOOL :uv "true"))]
        (two-keys key text other-key other-text)))
  (assert (= (len other-runs.launches) 2) other-runs.launches))


;; --- 8 先読み ---------------------------------------------------------------------------------------

(defk warm-then-first-tick [runs warm spec cold-spec]
  {:pre [(: runs ToolRuns) (: warm WarmEnv) (: spec JobSpec) (: cold-spec JobSpec)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の判断(plan)を env-host の観測の上で回す筋: job の無い拍の判断・先読みを頼んで終わらせた後の観測・task が来た最初の拍の
   判断・温めていない env の task の最初の拍の判断を返すため。"
  (val policy (WorkerPolicy))
  (<- before tuple (ObserveEnvs))
  (val warming (plan 0 #() (WorldView before #()) {} policy :warm #(warm)))
  (<- (PrepareEnv warm.key warm.runtime-env :warm True))
  (setv runs.released True)
  (<- after tuple (ObserveEnvs))
  (val first (plan 1 #(spec) (WorldView after #()) {} policy :warm #(warm)))
  (val cold-first (plan 2 #(cold-spec) (WorldView after #()) {} policy :warm #(warm)))
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
  (<- spec (task-spec {"id" "t8" "revision" "" "versions" (current-versions) "program" SAMPLE-TASK-PROGRAM "runtimeEnv" declared-1}
                      tasks))
  (<- cold RuntimeEnv (declared 2))
  (<- declared-2 dict (runtime-env->json cold))
  (<- cold-spec (task-spec {"id" "t9" "revision" "" "versions" (current-versions) "program" SAMPLE-TASK-PROGRAM "runtimeEnv" declared-2}
                           tasks))
  (<- seen tuple (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                                 (env-host settings)]
                   (warm-then-first-tick runs warm spec cold-spec)))
  (assert (= (get seen 0) #((PrepareEnv warm.key warm.runtime-env :warm True))) (get seen 0))
  (<- view (| CodeView None) (view-of (get seen 1) key))
  (<- root str (env-root settings key))
  (assert (and (is-not view None) (= view.state CodeState.READY) (= view.path root)) (get seen 1))
  (assert (= (get seen 2) #((StartJob spec 1 root))) (get seen 2))
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


(deftest test-scenario-9-the-sweep-keeps-pinned-latest-and-foreign-dirs [tmp-path]
  ;; 同じ project の root 3 つ: a(固定・最も古い)・b・c(最後に作った = project の最新)。worker が作っていない dir(キーの形の名で
  ;; 完成マーカーの無い dir と、別の名の dir)。空きの下限を空きより上に置く(下限を切った状態)。
  (val runs (ToolRuns))
  (<- settings EnvSettings (settings-at tmp-path :sweep-floor-bytes (** 2 62)))
  (<- env-1 RuntimeEnv (declared 1))
  (<- env-2 RuntimeEnv (declared 2))
  (<- env-3 RuntimeEnv (declared 3))
  (<- a Path (made-root settings "aaaaaaaaaaaaaaaaaaaaaaaa" env-1 100 1000))
  (<- b Path (made-root settings "bbbbbbbbbbbbbbbbbbbbbbbb" env-2 200 2000))
  (<- c Path (made-root settings "cccccccccccccccccccccccc" env-3 300 3000))
  (val foreign (/ (Path settings.state) "roots" "0123456789abcdef01234567"))
  (.mkdir foreign)
  (.write-text (/ foreign "keep.txt") "not ours\n")
  (val notes (/ (Path settings.state) "roots" "notes"))
  (.mkdir notes)
  (<- (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (inline-env-tool runs)
                      (env-host settings)]
        (SweepEnvs (frozenset #((+ "env-" a.name))))))
  (assert (.exists a) "固定された root は残る")
  (assert (not (.exists b)) "固定されていない古い root は消える")
  (assert (.exists c) "project ごとの最新の root(bytecode の引き継ぎ元)は残る")
  (assert (and (.exists foreign) (.exists notes)) "worker が作っていない dir は消さない")
  (assert (= runs.launches #()) runs.launches))
