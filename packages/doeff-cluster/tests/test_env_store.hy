;; 実行環境の root の言い換え(worker/protocol/env_store の env-host — #2467)の観測と名乗りを、準備の process を起こさずに確かめる。
;;   * 完成マーカーの在る root だけを READY と観測し、heartbeat の名乗り(EnvReport)に準備済みのキーと disk の条件を載せる。
;;   * coordinator への口(coordinator-link)は、実行環境を扱う worker の heartbeat に、拍の Program が EnvReport で問うて ReadDesired の欄で
;;     渡した名乗りを載せる。扱わない worker は載せない。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import tests.link_rig [LinkRig LINK-ROUTE])
(import tests.transport_http [transport-http])
(import doeff_cluster.worker.protocol.coordinator_link [coordinator-link])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.worker_model [CodeState EnvReport ReadDesired DesiredJobs DesiredUnreadable])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs])
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host known-roots])
(import doeff_cluster.worker.protocol.env_translation [request-of-json])
(import doeff_cluster.worker.core.env_prepare [carry-source env-marker->json])
(import doeff_cluster.worker.intent.env_prepare_model [CarryFrom EnvMarker PrepareRequest])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import tests.env_fixtures [LOCK env-of])

(val READY-NAME "0123456789abcdef01234567")
(val HALF-NAME "89abcdef0123456789abcdef")


(defk settings-in [tmp]
  {:pre [(: tmp Path)] :post [(: % EnvSettings)]}
  "root の置き場に、完成した root 1 つと、マーカーの無い途中の root 1 つと、名の形の違う dir 1 つを置いた設定を返すため。"
  (val roots (/ tmp "state" "roots"))
  (.mkdir (/ roots READY-NAME) :parents True)
  (.write-text (/ roots READY-NAME ENV-MARKER) (json.dumps {"env" {"project" {}}}))
  (.mkdir (/ roots HALF-NAME))
  (.mkdir (/ roots ".old.broken.1"))
  (EnvSettings :state (str (/ tmp "state")) :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL))


(defk on-envs [settings program [inner []]]
  {:pre [(: settings EnvSettings) (: program Program) (: inner list)] :post [(: % (| tuple DesiredJobs DesiredUnreadable))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を env-host と本物の答え手の下で回し、その答えを返すため(inner = env-host の内側に置く handler)。"
  (<- answer (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (env-host settings) #* inner] program))
  answer)


(defk observed-and-reported []
  {:pre [] :post [(: % tuple)]}
  "root の観測と heartbeat の名乗りを返すため。"
  (<- views tuple (ObserveEnvs))
  (<- report dict (EnvReport))
  #(views report))


(deftest test-only-roots-with-a-marker-are-ready-and-named [tmp-path]
  (<- settings EnvSettings (settings-in tmp-path))
  (val got (! (on-envs settings (observed-and-reported))))
  (val views (get got 0))
  (val report (get got 1))
  (assert (= (lfor v views #(v.revision v.state)) [#((+ "env-" READY-NAME) CodeState.READY)]) views)
  (assert (= (. (get views 0) path) (str (/ tmp-path "state" "roots" READY-NAME))) views)
  (assert (= (get report "ready") [READY-NAME]) report)
  (assert (= #((get report "preparing") (get report "failed")) #([] [])) report)
  (assert (in "capacity" report) report))


(defk desired-with-report []
  {:pre [] :post [(: % (| DesiredJobs DesiredUnreadable))] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍の Program(worker/core/program の worker-tick)と同じく、root の姿を問うて宣言の読みに渡すため。"
  (<- report (EnvReport))
  (<- desired (ReadDesired :env-report report))
  desired)


(deftest test-the-heartbeat-carries-the-env-report-only-for-env-workers [tmp-path]
  (val sent [])
  (defn #^ httpx.Response handle [#^ httpx.Request request]
    (.append sent (json.loads request.content))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  ;; 拍の Program と同じく、root の言い換えに EnvReport を問うてから ReadDesired の欄で口へ渡す。
  (val env-link (LinkRig "http://coord" "w" #() 1 0 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)
                         :handles-envs True))
  (<- settings EnvSettings (settings-in tmp-path))
  (<- (on-envs settings (desired-with-report)
               [(transport-http env-link.transport) (coordinator-link env-link.state env-link.cell LINK-ROUTE env-link.watch-cell)]))
  (assert (= (get sent 0 "envs" "ready") [READY-NAME]) sent)
  (assert (in "envCapacity" (get sent 0)) sent)
  ;; 扱わない worker は名乗らず、env-host が無くても heartbeat を送れる。
  (val plain-link (LinkRig "http://coord" "w" #() 1 0 60000 :task-dir (str (/ tmp-path "plain-tasks")) :transport (httpx.MockTransport handle)))
  (.poll plain-link)
  (assert (not-in "envs" (get sent 1)) sent))


;; 失敗ケース(#3675 の読み手の条件): #3675 より前の worker が書いた完成マーカー(bytecode の欄が compiled・carried・failed の形)の root も、
;; 新しい worker の完成した root の列(known-roots)に入り、準備の要求の JSON を通して引き継ぎ元の選び(carry-source)が選べる — 置き場の
;; 名指しは宣言の欄だけを読み、数の欄(報告と log の値)に依らない。数の欄を読む形にすると、前の形の印の root が列から落ちて赤。
(deftest test-a-root-marked-with-the-earlier-bytecode-counts-is-still-a-carry-source [tmp-path]
  (<- settings EnvSettings (settings-in tmp-path))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val root (/ tmp-path "state" "roots" READY-NAME))
  (<- raw dict (env-marker->json (EnvMarker :env env :key READY-NAME :platform "test" :stages #() :downloaded 0 :built 0
                                            :interpreter "/usr/bin/python3" :child-protocol 1 :hy-version HY-VERSION)))
  (setv (get raw "bytecode") {"compiled" 3 "carried" 1 "failed" 0 "scanSeconds" 0.5 "closureSeconds" 0.75 "carrySeconds" 0.25
                              "compileSeconds" 1.5 "trees" [{"name" "app" "compiled" 3 "carried" 1 "failed" 0}]})
  (.write-text (/ root ENV-MARKER) (json.dumps raw))
  (<- picked (| CarryFrom None) (carry-source-through-store settings tmp-path))
  (assert (= picked (CarryFrom :tree (.format "{}/app" root) :commit (. (get env.repos 0) commit))) picked))


;; 新しい root の venv の Hy の compiler の版(引き継ぎ元の候補を比べる版 — #3706)。
(val HY-VERSION "1.1.0")


(defk carry-source-through-store [settings tmp]
  {:pre [(: settings EnvSettings) (: tmp Path)] :post [(: % (| CarryFrom None))]}
  "worker の完成した root の列(known-roots)を準備の要求の JSON に通して、app-2 の宣言の引き継ぎ元の選び(carry-source・新しい root の
   Hy の版は HY-VERSION)の答えを返すため — 置き場に完成した root が 1 つ在る事も確かめる。"
  (<- known tuple (with-handlers [os-file-handler] (known-roots settings)))
  (assert (= (tuple (gfor k known (get k "root"))) #((str (/ tmp "state" "roots" READY-NAME)))) known)
  (<- next-env RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- declared dict (runtime-env->json next-env))
  (<- request PrepareRequest (request-of-json {"env" declared "key" "k" "platform" "test" "root" (str (/ tmp "new")) "known" known}))
  (<- picked (| CarryFrom None) (carry-source request.known request.env "app" HY-VERSION))
  picked)


;; 失敗ケース(#3706): Hy の版の欄(hyVersion)の無い前の印の root と、別の Hy の版の印の root は、完成した root の列には入るが
;; (置き場の名指しは新しい欄を読まない)、引き継ぎ元には選ばれない(版の分からない compiler で焼いた .pyc を持ち越さない)。
;; 同じ版の印の root は、lock が違っても(依存を 1 本上げた)選ばれる — lock の一致を条件に残すと赤。
(deftest test-the-carry-source-reads-the-hy-version-of-the-marker [tmp-path]
  (<- settings EnvSettings (settings-in tmp-path))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" (+ LOCK "rich==13.9.4 top=rich\n")))
  (val marker-path (/ tmp-path "state" "roots" READY-NAME ENV-MARKER))
  (<- raw dict (env-marker->json (EnvMarker :env env :key READY-NAME :platform "test" :stages #() :downloaded 0 :built 0
                                            :interpreter "/usr/bin/python3" :child-protocol 1 :hy-version HY-VERSION)))
  (.write-text marker-path (json.dumps raw))
  (<- same (| CarryFrom None) (carry-source-through-store settings tmp-path))
  (assert (= same (CarryFrom :tree (.format "{}/app" (/ tmp-path "state" "roots" READY-NAME)) :commit (. (get env.repos 0) commit)))
          same)
  (.write-text marker-path (json.dumps (dfor #(k v) (.items raw) :if (!= k "hyVersion") k v)))
  (<- unknown (| CarryFrom None) (carry-source-through-store settings tmp-path))
  (assert (is unknown None) unknown)
  (.write-text marker-path (json.dumps (| raw {"hyVersion" "1.2.0"})))
  (<- other (| CarryFrom None) (carry-source-through-store settings tmp-path))
  (assert (is other None) other))
