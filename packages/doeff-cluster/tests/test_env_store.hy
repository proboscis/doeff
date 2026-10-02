;; 実行環境の root の言い換え(worker/protocol/env_store の env-host — #2467)の観測と名乗りを、準備の process を起こさずに確かめる。
;;   * 完成マーカーの在る root だけを READY と観測し、heartbeat の名乗り(EnvReport)に準備済みのキーと disk の条件を載せる。
;;   * coordinator への口(coordinator-link)は、実行環境を扱う worker の heartbeat に、拍の Program が EnvReport で問うて ReadDesired の欄で
;;     渡した名乗りを載せる。扱わない worker は載せない。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import pathlib [Path])
(import doeff [run with-handlers])
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
(import doeff_cluster.worker.protocol.env_store [EnvSettings env-host])

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


(defn #^ object on-envs [#^ EnvSettings settings #^ object program #^ list [inner []]]  ; defk にできない: 検が Program の外から本物の答え手の組で 1 回走らせる入口
  "筋書きの Program を env-host と本物の答え手の下で 1 回の run で回す(inner = env-host の内側に置く handler)。"
  (run (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (env-host settings) #* inner] program)))


(defk observed-and-reported []
  {:pre [] :post [(: % tuple)]}
  "root の観測と heartbeat の名乗りを返すため。"
  (<- views tuple (ObserveEnvs))
  (<- report dict (EnvReport))
  #(views report))


(deftest test-only-roots-with-a-marker-are-ready-and-named [tmp-path]
  (<- settings EnvSettings (settings-in tmp-path))
  (val got (on-envs settings (observed-and-reported)))
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
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" []}))
  ;; 拍の Program と同じく、root の言い換えに EnvReport を問うてから ReadDesired の欄で口へ渡す。
  (val env-link (LinkRig "http://coord" "w" #() 1 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)
                         :handles-envs True))
  (<- settings EnvSettings (settings-in tmp-path))
  (on-envs settings (desired-with-report)
           [(transport-http env-link.transport) (coordinator-link env-link.state env-link.cell LINK-ROUTE env-link.watch-cell)])
  (assert (= (get sent 0 "envs" "ready") [READY-NAME]) sent)
  (assert (in "envCapacity" (get sent 0)) sent)
  ;; 扱わない worker は名乗らず、env-host が無くても heartbeat を送れる。
  (val plain-link (LinkRig "http://coord" "w" #() 1 60000 :task-dir (str (/ tmp-path "plain-tasks")) :transport (httpx.MockTransport handle)))
  (.poll plain-link)
  (assert (not-in "envs" (get sent 1)) sent))
