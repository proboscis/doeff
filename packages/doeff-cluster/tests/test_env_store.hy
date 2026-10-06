;; 実行環境の root の言い換え(worker/protocol/env_store の env-host — #2467)の観測と名乗りを、準備の process を起こさずに確かめる。
;;   * 完成マーカーの在る root だけを READY と観測し、heartbeat の名乗り(EnvReport)に準備済みのキーと disk の条件を載せる。
;;   * coordinator への口(coordinator-link)は、実行環境を扱う worker の heartbeat に、拍の Program が EnvReport で問うて ReadDesired の欄で
;;     渡した名乗りを載せる。扱わない worker は載せない。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import dataclasses [replace])
(import os)
(import time)
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
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host known-roots sweep-leftovers])
(import doeff_cluster.worker.core.env_upkeep [CODE-STORE-UNUSED-SECONDS])
(import doeff_cluster.worker.protocol.env_translation [request-of-json])
(import doeff_cluster.worker.core.env_prepare [env-marker->json])
(import doeff_cluster.worker.intent.env_prepare_model [EnvMarker])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
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
  (EnvSettings :state (str (/ tmp "state")) :uv-cache (str (/ tmp "state" "uv-cache")) :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL :roots-cap-bytes (** 2 62)))


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


;; 失敗ケース(#3675 の読み手の条件): 前の worker が書いた完成マーカー(bytecode の欄が compiled・carried・failed の形 — #3858 の前は
;; carried と carrySeconds も在った)の root も、新しい worker の完成した root の列(known-roots)に入る — root の名指しは宣言の欄だけを
;; 読み、数の欄(報告と log の値)に依らない。数の欄を読む形にすると、前の形の印の root が列から落ちて赤。
(deftest test-a-root-marked-with-the-earlier-bytecode-counts-is-still-a-known-root [tmp-path]
  (<- settings EnvSettings (settings-in tmp-path))
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val root (/ tmp-path "state" "roots" READY-NAME))
  (<- raw dict (env-marker->json (EnvMarker :env env :key READY-NAME :platform "test" :stages #() :downloaded 0 :built 0
                                            :interpreter "/usr/bin/python3" :child-protocol 1 :hy-version "1.1.0")))
  (setv (get raw "bytecode") {"compiled" 3 "carried" 1 "failed" 0 "scanSeconds" 0.5 "closureSeconds" 0.75 "carrySeconds" 0.25
                              "compileSeconds" 1.5 "trees" [{"name" "app" "compiled" 3 "carried" 1 "failed" 0}]})
  (.write-text (/ root ENV-MARKER) (json.dumps raw))
  (<- known tuple (with-handlers [os-file-handler] (known-roots settings)))
  (assert (= (tuple (gfor k known (get k "root"))) #((str root))) known))


;; 失敗ケース(#3858): 掃除(sweep-leftovers)は bytecode の保存先(doeff-hy の code_store)の entry のうち、7 日使われない物(使うたびに
;; 時刻を進める)を消し、使った物と、保存先を使わない設定(code-store が None)の時は何も消さない。掃除が保存先を歩かないと、版ごとに
;; 増える entry が消えずに worker の disk を埋める(native の wheel の保存先と同じ 7 日の作法)。
(deftest test-the-sweep-removes-store-entries-unused-for-seven-days [tmp-path]
  (val store (/ tmp-path "code-store"))
  (.mkdir (/ store "ab") :parents True)
  (val stale (/ store "ab" "old.code"))
  (val used (/ store "ab" "new.imports"))
  (.write-bytes stale b"x")
  (.write-bytes used b"y")
  (val now-s (time.time))
  (val past (- now-s CODE-STORE-UNUSED-SECONDS 60))
  (os.utime stale #(past past))
  (<- settings EnvSettings (settings-in tmp-path))
  (<- none-removed tuple (with-handlers [os-file-handler] (sweep-leftovers settings (int (* now-s 1000)))))
  (assert (.exists stale) "保存先を使わない設定(code-store が None)で保存先の file を消した")
  (assert (not-in (str stale) none-removed) none-removed)
  (<- removed tuple (with-handlers [os-file-handler] (sweep-leftovers (replace settings :code-store (str store)) (int (* now-s 1000)))))
  (assert (in (str stale) removed) removed)
  (assert (not (.exists stale)) "7 日使われない entry が残った")
  (assert (.exists used) "使った entry を消した"))
