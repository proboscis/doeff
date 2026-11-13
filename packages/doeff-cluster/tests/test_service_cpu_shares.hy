;; 常駐の job の CPU の取り分(worker/core/job_cgroup の頭の註の「取り分」): 平らな services の層の中では CPU の重みが thread ごとで、
;; 読みの service の答えの thread が一括の常駐(写しの係など)と同じ順番で CPU を待った(2026-11-13 の node-a — 待ちの中央 4.8 ms・
;; p95 49 ms)。job の宣言の欄 :cpu-share が宣言の行 → coordinator → heartbeat の返事 → worker の JobSpec と運ばれ、取り分を名乗る
;; 機体の worker は job を services の層の下のその名の子に置く。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects [ProcessOutcome RunProcess subprocess-handler os-file-handler])
(import doeff_cluster [JobSpec job system-of])
(import doeff_cluster [ClusterJob ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Placement])
(import doeff_cluster._coordinator.core.cluster_policy [jobs-for job-from-json job-to-json])
(import doeff_cluster._coordinator.protocol.replies [spec-json])
(import doeff_cluster._worker.protocol.declared [declared-job-spec])
(import doeff_cluster._worker.core.job_cgroup [placed-argv service-shares-of share-procs shares-argv])
(import doeff_cluster._worker.protocol.process_host [HostSettings ready-shares shim-argv service-procs-of])
(import doeff_cluster._worker.entry.main [SERVICE-SHARES-VAR service-shares-placed])
(import tests.host_rig [host-settings])
(import tests.state_rows [table-from-rows])
(import tests.test_defsystem_form [evaluate refusal])
(import tests.test_service_declaration [TALLY-CALL only-row])
(import tests.fixtures.services [tally-program])
(import tests.fixtures.envs [plain-foundation])

(val SHIM-ARGV #("python" "-m" "doeff_cluster._worker.entry.shim" "--" "uv" "run" "hy"))


(deftest test-the-worker-reads-its-share-from-the-mounted-room-file-without-a-pod-env-change [#^ Path tmp-path]
  (val room (/ tmp-path "room.json"))
  (val procs (str (/ tmp-path "services" "cgroup.procs")))
  (.mkdir (/ tmp-path "services"))
  (.write-text room "{\"node-a\": {\"memoryMiB\": 100, \"serviceShares\": \"reads=400\"}, \"node-b\": {\"memoryMiB\": 100}}")
  (<- placed tuple (with-handlers [os-file-handler] (service-shares-placed None procs (str room) "node-a")))
  (assert (= placed #((str (/ tmp-path "services")) #(#("reads" 400)) (str (/ tmp-path "services" "_shared" "cgroup.procs")))) placed)
  (assert (= (! (with-handlers [os-file-handler] (service-shares-placed None procs (str room) "node-b"))) #(None #() procs)))
  ;; 明示した環境変数の優先は保つ。別の worker の値を拾わない。
  (<- explicit tuple (with-handlers [os-file-handler] (service-shares-placed "reads=200" procs (str room) "node-a")))
  (assert (= (get explicit 1) #(#("reads" 200)))))


(deftest test-the-worker-refuses-an-unreadable-or-malformed-share-file-instead-of-silently-disabling-it [#^ Path tmp-path]
  (val room (/ tmp-path "room.json"))
  (val procs (str (/ tmp-path "services" "cgroup.procs")))
  (.mkdir (/ tmp-path "services"))
  (with [(pytest.raises ValueError :match "serviceShares")]
    (! (with-handlers [os-file-handler] (service-shares-placed None procs (str room) "node-a"))))
  (for [body #("{" "[]" "{}" "{\"node-a\": []}" "{\"node-a\": {\"serviceShares\": null}}"
                "{\"node-a\": {\"serviceShares\": 400}}" "{\"node-a\": {\"serviceShares\": \"reads=0\"}}")]
    (.write-text room body)
    (with [(pytest.raises ValueError :match "serviceShares")]
      (! (with-handlers [os-file-handler] (service-shares-placed None procs (str room) "node-a"))))))


;; --- 宣言の欄 ------------------------------------------------------------------------------------

(deftest test-defsystem-keeps-the-cpu-share-in-the-static-description-and-refuses-a-bad-name
  ;; :cpu-share は書いた job にだけ静的な記述の cpuShare で載る。名の規則(英小文字か数字で始まる英小文字・数字・-)の外は展開の時に断る。
  ;; 失敗ケース = 書かない job に既定の取り分を足す・_shared(worker の共有の子の名)を job の取り分として通す。
  (<- ns (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem store [foundation]
  (reader (notice foundation) :replicas 1 :peak-mib 900 :settle-mib 400 :cpu-share \"reads\")
  (plain (notice foundation) :replicas 1 :peak-mib 0 :settle-mib 0))
"))
  (val jobs (get (. (get ns "store") __doeff_system__) "jobs"))
  (assert (= (.get (get jobs 0) "cpuShare") "reads") jobs)
  (assert (not-in "cpuShare" (get jobs 1)) jobs)
  (for [bad #("\"_shared\"" "\"Reads\"" "\"\"" "1")]
    (<- message str (refusal (.format "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :cpu-share {}))" bad)))
    (assert (in ":cpu-share は CPU の取り分の名" message) message)))


(deftest test-the-cpu-share-rides-from-the-declaration-to-the-worker-and-is-not-compared
  ;; 宣言の行の cpuShare → coordinator の JobSpec(保存の行にも)→ 返事の job の行 → worker の JobSpec。取り分だけが違う spec は同じ
  ;; process の形(起こし直さない・指紋 spec-hash の外)。書かない job の行には鍵が無い。
  (val bound (job "tally" (tally-program plain-foundation 2) :call TALLY-CALL :replicas 1 :peak-mib 200 :settle-mib 100
                  :cpu-share "reads" :needs #{"cluster-net"}))
  (<- row dict (only-row (system-of "lab" #(bound))))
  (assert (= (get row "cpuShare") "reads") row)
  (val read (job-from-json row))
  (assert (= read.spec.cpu-share "reads") read)
  (assert (= (get (job-to-json read) "cpuShare") "reads"))
  (assert (= (. (job-from-json (job-to-json read)) spec cpu-share) "reads"))
  (val state (ClusterState #(read) (! (table-from-rows {})) (! (table-from-rows {"tally" (Placement "tally" "w1" 1 0)}))))
  (val specs (jobs-for state "w1"))
  (<- spelled dict (spec-json (get specs 0)))
  (assert (= (get spelled "cpuShare") "reads") spelled)
  (<- worker-spec JobSpec (declared-job-spec spelled False))
  (assert (= worker-spec.cpu-share "reads") worker-spec)
  (assert (= worker-spec (replace worker-spec :cpu-share None)))
  ;; 欄の無い返事(旧い coordinator)と名の規則の外の欄は取り分なし。
  (<- old JobSpec (declared-job-spec (| spelled {"cpuShare" "_shared"}) False))
  (assert (is old.cpu-share None) old)
  (val plain (ClusterJob (JobSpec "plain" "m" #() "rev")))
  (assert (not-in "cpuShare" (job-to-json (replace plain :run {})))))


(deftest test-the-coordinator-refuses-a-declaration-with-a-bad-cpu-share
  ;; 送り手の外で組んだ行も同じ規則(job_rules.cpu-share-refusal)で断る(400)。
  (val bound (job "tally" (tally-program plain-foundation 2) :call TALLY-CALL :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"cluster-net"}))
  (<- row dict (only-row (system-of "lab" #(bound))))
  (with [(pytest.raises Exception :match "cpuShare")]
    (job-from-json (| row {"cpuShare" "Bad Name"}))))


;; --- 機体の取り分の宣言 ---------------------------------------------------------------------------

(deftest test-the-machine-share-declaration-is-read-in-the-written-order-or-refused-with-a-reason
  (assert (= (! (service-shares-of "reads=400 bulk=50")) #(#("reads" 400) #("bulk" 50))))
  (for [#(text part) #(#("reads" "<名>=<重み>") #("_shared=100" "<名>=<重み>") #("reads=0" "1〜10000") #("reads=10001" "1〜10000")
                       #("reads=x" "1〜10000") #("reads=1 reads=2" "2 回ある") #("  " "空"))]
    (<- reason (service-shares-of text))
    (assert (and (isinstance reason str) (in part reason)) #(text reason))))


(deftest test-a-resident-job-goes-to-its-share-and-the-rest-to-the-shared-child [#^ Path tmp-path]
  ;; 取り分を名乗る機体: 名乗った取り分の job はその子・名の無い job と名乗っていない名の job は共有の子・task は今どおり jobs の子。
  ;; 取り分を名乗らない機体は今どおり層そのもの(job の欄を読まない)。
  (val layer (str (/ tmp-path "services")))
  (val shared (str (/ tmp-path "services" "_shared" "cgroup.procs")))
  (val reads (str (/ tmp-path "services" "reads" "cgroup.procs")))
  (<- base HostSettings (host-settings tmp-path))
  (val placing (replace base :service-procs shared :service-layer layer :service-shares #(#("reads" 400))))
  (val reader (JobSpec "reader" "m" #() "r" :cpu-share "reads"))
  (val plain (JobSpec "plain" "m" #() "r"))
  (val stranger (JobSpec "stranger" "m" #() "r" :cpu-share "bulk"))
  (assert (= (! (service-procs-of placing reader)) reads))
  (assert (= (! (service-procs-of placing plain)) shared))
  (assert (= (! (service-procs-of placing stranger)) shared))
  (<- placed tuple (shim-argv placing reader SHIM-ARGV))
  (assert (= placed (! (placed-argv reads SHIM-ARGV))) placed)
  (val flat (replace base :service-procs (str (/ tmp-path "services" "cgroup.procs"))))
  (assert (= (! (service-procs-of flat reader)) flat.service-procs))
  (assert (= (! (share-procs None #() "reads" "x")) "x")))


(deftest test-the-share-script-makes-the-children-and-writes-the-weights-once-cpu-is-given [#^ Path tmp-path]
  ;; 本物の sh(cgroup の file の代わりに tmp の file): 子を作り、層の子に cpu を掛け、子ごとに重みを書いて on を出す。
  (val layer (/ tmp-path "services"))
  (.mkdir layer)
  (.write-text (/ layer "cgroup.subtree_control") "")
  (<- argv tuple (shares-argv (str layer) #(#("reads" 400))))
  (<- ran ProcessOutcome (with-handlers [subprocess-handler] (RunProcess :argv argv)))
  (assert (= #(ran.exit-code (.strip ran.stdout)) #(0 "on")) ran)
  (assert (= (.strip (.read-text (/ layer "cgroup.subtree_control"))) "+cpu"))
  (assert (= (.strip (.read-text (/ layer "_shared" "cpu.weight"))) "100"))
  (assert (= (.strip (.read-text (/ layer "reads" "cpu.weight"))) "400")))


(deftest test-the-share-script-defers-while-the-layer-still-holds-processes [#^ Path tmp-path]
  ;; 失敗ケース: 前の世代の process が層そのものに残る間、kernel は cpu の掛けを断る(EBUSY)。script は deferred を出して 0 で終わり、
  ;; 子は作ってある(job は子へ置けて、重みが効かないだけ)— 起こすのを止めない。ここでは書けない subtree_control(dir)で断りを作る。
  (val layer (/ tmp-path "services"))
  (.mkdir (/ layer "cgroup.subtree_control") :parents True)
  (<- argv tuple (shares-argv (str layer) #(#("reads" 400))))
  (<- ran ProcessOutcome (with-handlers [subprocess-handler] (RunProcess :argv argv)))
  (assert (= #(ran.exit-code (.strip ran.stdout)) #(0 "deferred")) ran)
  (assert (.is-dir (/ layer "reads")))
  (assert (not (.exists (/ layer "reads" "cpu.weight")))))


(deftest test-the-worker-makes-the-share-children-at-boot-or-refuses-a-bad-declaration [#^ Path tmp-path]
  ;; 入口: 名乗らない機体は層そのまま。名乗る機体は共有の子と取り分の子を作り、名の無い常駐の置き場を共有の子にする。読めない宣言と、
  ;; services の層の無い機体の宣言は起動を断る(名乗った取り分を黙って捨てない)。
  (val procs (str (/ tmp-path "services" "cgroup.procs")))
  (.mkdir (/ tmp-path "services"))
  (assert (= (! (with-handlers [os-file-handler] (service-shares-placed None procs))) #(None #() procs)))
  (<- placed tuple (with-handlers [os-file-handler] (service-shares-placed "reads=400" procs)))
  (assert (= placed #((str (/ tmp-path "services")) #(#("reads" 400)) (str (/ tmp-path "services" "_shared" "cgroup.procs")))) placed)
  (assert (.is-dir (/ tmp-path "services" "reads")))
  (with [(pytest.raises ValueError :match SERVICE-SHARES-VAR)]
    (! (with-handlers [os-file-handler] (service-shares-placed "reads=0" procs))))
  (with [(pytest.raises ValueError :match "層の名が無い")]
    (! (with-handlers [os-file-handler] (service-shares-placed "reads=400" None)))))


(deftest test-the-host-readies-the-shares-before-a-resident-job-and-does-nothing-without-them [#^ Path tmp-path]
  ;; 宿は常駐の job を起こす前に取り分を整える(本物の sh — 子と重みが書かれる)。取り分を名乗らない機体では何も走らせない。
  (val layer (/ tmp-path "services"))
  (.mkdir layer)
  (.write-text (/ layer "cgroup.subtree_control") "")
  (<- base HostSettings (host-settings tmp-path))
  (assert (is (! (ready-shares base)) None))
  (assert (not (.exists (/ layer "_shared"))))
  (val placing (replace base :service-layer (str layer) :service-shares #(#("reads" 400))))
  (assert (is (! (with-handlers [subprocess-handler] (ready-shares placing))) None))
  (assert (= (.strip (.read-text (/ layer "reads" "cpu.weight"))) "400")))
