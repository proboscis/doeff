;;; 模擬の環境で各 service の入口の組み立てを 1 回ずつ回す検(agora-redesign #2542・doeff-linter DOEFF136)。本番の組み立て(入口の
;;; Program を handler の組の上で回す口)はそのままに、handler の組だけを模擬の物へ差し替える:
;;;
;;;   coordinator   sim-cluster の coordinator の Pod(coordinator.entry.main の load-state と handler_sets の emulated-handlers)
;;;   worker        sim-cluster の worker の世代(doeff_cluster.main の worker-on を偽の宿の組 sim-host の上で)
;;;   record-store  record_store.entry.main の record-store-on を handler_sets の emulated-handlers(まねた受付の箱と memory の file system)
;;;                 の上で
;;;
;;; 走らせ方 = この dir の conftest.py(`uv run pytest packages/doeff-cluster/src/doeff_cluster/sim/test_entries_on_sim.hy`)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import json)
(import doeff [with-handlers])
(import doeff_time [Delay sim-time-handler])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.foundation.coordinator_inbox [StopState ReplySlot RawRequest])
(import doeff_cluster.record_store.entry.main [record-store-on])
(import doeff_cluster.record_store.entry.handler_sets [ScriptedRecordInbox emulated-handlers])
(import doeff_cluster.sim.local [sim-cluster SimCoordinatorRun CoordinatorRuns ReadCoordinator])

;; 仮想の時計の起点(sim-cluster の既定の起点と同じ桁の、どこかの日)。
(val START-MS 1767225600000)
;; job の無い系 — 入口の組み立てが起き、worker が coordinator に名乗るところまでを見る。
(val EMPTY (System :name "entries-on-sim" :jobs #()))
(val RECORDS-ROOT "/records")


(defrecord Started
  "筋書きが読んだ起動の後の姿: runs = coordinator の Pod の一生の列(SimCoordinatorRun の tuple)・view = 既定の worker の coordinator から
   見た姿(/workers/sim-worker の本文)。"
  (#^ tuple runs)
  (#^ dict view))


(defk read-after-start []
  {:pre [] :post [(: % Started)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 5 秒待ってから、coordinator の Pod の一生の列と、既定の worker(sim-worker)の coordinator から見た姿を読むため。"
  (<- (Delay 5.0))
  (<- runs tuple (CoordinatorRuns))
  (<- view dict (ReadCoordinator "/workers/sim-worker"))
  (Started :runs runs :view view))


(deftest test-the-coordinator-entry-runs-on-the-emulated-handlers
  ;; coordinator の Pod は入口の load-state と emulated-handlers の上で調停ループを回し、5 秒の間 止まらずに要求へ答える。
  (<- seen Started (sim-cluster EMPTY (read-after-start) :start-ms START-MS))
  (assert (= (len seen.runs) 1) seen.runs)
  (val life (get seen.runs 0))
  (assert (isinstance life SimCoordinatorRun) life)
  (assert (is life.ended-ms None) life)
  (assert (= life.started-ms START-MS) life)
  (assert (= (get seen.view "name") "sim-worker") seen.view))


(deftest test-the-worker-entry-runs-on-the-sim-host
  ;; worker の世代は入口の worker-on を偽の宿の組の上で回し、heartbeat で coordinator に名乗る(coordinator は生きていると数える)。
  (<- seen Started (sim-cluster EMPTY (read-after-start) :start-ms START-MS))
  (assert (get seen.view "alive") seen.view))


(deftest test-the-record-store-entry-runs-on-the-emulated-handlers
  ;; 置き場の入口の record-store-on を、まねた受付の箱と memory の file system の組の上で回す: 追記を受けて読みに答え、並べた要求を
  ;; 答え終えると停止の合図で抜ける(答えた数 = 並べた数)。
  (val stop (StopState))
  (val appended (ReplySlot))
  (val read (ReplySlot))
  (val inbox (ScriptedRecordInbox [(RawRequest "POST" "/append" {} {"service" "svc" "run" "r1" "chunk" 0 "lines" ["{\"n\": 1}"]}
                                               appended None "sim")
                                   (RawRequest "GET" "/runs/svc/r1" {} None read None "sim")]
                                  stop))
  (<- handlers list (emulated-handlers inbox stop RECORDS-ROOT (MemoryFiles :dirs #(RECORDS-ROOT))))
  (<- served int (with-handlers [(sim-time-handler :start-time (datetime-of-epoch-ms START-MS))]
                   (record-store-on handlers 86400000 900000)))
  (assert (= served 2) served)
  (assert stop.requested stop)
  (assert (= appended.status 200) appended.data)
  (assert (= (json.loads appended.data) {"appended" 1}) appended.data)
  (assert (= read.status 200) read.data)
  (assert (in "{\"n\": 1}" (.decode read.data "utf-8")) read.data))
