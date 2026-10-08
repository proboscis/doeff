;; coordinator が worker の heartbeat を受けてから返事を置くまでの遅れを測り、閾(HEARTBEAT-LAG-MS = 5 秒)を越えた時だけ 1 行を出す検
;; (2026-10-07)。
;;
;; 実例: 2026-10-07 13:16〜13:18 JST に本番の worker agent-worker-2 の heartbeat の往復(worker の ReadDesired)が 5.8 秒かかり、
;; coordinator の生死の表で live が lease 10 秒を越えて false に瞬いた。coordinator の log には時刻も遅れの行も無く、遅れが
;; coordinator の側か網かを分けられなかった。だから:
;;   - 返事を置くまでが 5 秒を越えた heartbeat ごとに、時刻・worker の名・かかった ms・いちばん長い区間の名を 1 行で出す
;;   - 受付の箱に並んでいた間(調停ループが前の歩で止まっていた待ち)も数え、その区間の名は "Inbox"
;;   - 速い返事では出さない
;; 土台: 調停ループの 1 歩を、台本の受付(heartbeat 1 件を 1 度だけ渡し、次の取りで止めの合図を立てる)と仮想の時計の下で回す。
;; 遅い保存は SaveState の答え手が仮想の時計で 6 秒待つ形でまねる(本物の sleep は使わない)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff [run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_events [MemoryBroker])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply Request CoordinatorStopRequested])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming SaveState])
(import doeff_cluster.coordinator.core.program [run-coordinator HEARTBEAT-LAG-LOG HEARTBEAT-LAG-MS])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [ObjectWatches kube-unavailable])

(val VERSIONS {"python" "3.14.0" "doeff" "1"})
;; 検の coordinator は k8s を持たない(本番の手元の coordinator と同じ答え手 — 調停ループは毎歩 Deployment と Node の見張りを揃える・
;; #3868・#4070)。
(val NO-KUBE "検の coordinator は k8s を持たない")
;; 遅い区間の秒(閾 5 秒を越え、生死の lease 10 秒より短い)。
(val SLOW-SECONDS 6.0)


(defrecord LagLine
  "heartbeat の返事の遅れの行 1 つの欄。"
  (#^ str at)
  (#^ str worker)
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defclass OneHeartbeat []
  "台本の受付: 最初の取りで heartbeat の要求 1 件を渡し、次の取りで止めの合図を立てて空で返す。slow-save = 保存(SaveState)を仮想の
   時計で SLOW-SECONDS 待たせるか。lags = 出た遅れの行。"
  (defn #^ None __init__ [self #^ Request request #^ bool slow-save]
    (setv self.request request self.slow-save slow-save self.handed False self.done False self.lags [])
    None))


(defhandler one-heartbeat [#^ OneHeartbeat inbox]
  "受付・保存・log の代役: 受付は台本の 1 件、保存は台本が遅いと言う時だけ 6 秒待つ、log は遅れの行だけ覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに要求と保存の遅さの台本を変え、出た行を検へ返す(Ask で区別できない)。
  (NextRequests [timeout-seconds limit]
    (if inbox.handed
        (do (setv inbox.done True)
            (resume []))
        (do (setv inbox.handed True)
            (resume [inbox.request]))))
  (Reply [request status body] (resume None))
  (SaveState [before after]
    (when (and inbox.slow-save (is-not before after))
      (<- (Delay SLOW-SECONDS)))
    (resume None))
  (CoordinatorStopRequested [] (resume inbox.done))
  (SlogEffect []
    (val fields effect.kwargs)
    (when (= effect.msg HEARTBEAT-LAG-LOG)
      (setv inbox.lags (+ inbox.lags [(LagLine :at (get fields "at") :worker (get fields "worker") :elapsed-ms (get fields "elapsed_ms")
                                               :slowest (get fields "slowest") :slowest-ms (get fields "slowest_ms"))])))
    (resume None)))


(defk heartbeat-of [name queued-ms]
  {:pre [(: name str) (: queued-ms int)] :post [(: % Request)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name の POST /heartbeat の要求を作るため(受付の箱に queued-ms 並んでいた事にする)。"
  (! (http-request "POST" "/heartbeat" {} {"name" name "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" VERSIONS}
                   :actor name :peer name :queued-ms queued-ms)))


(defk lags-of [inbox]
  {:pre [(: inbox OneHeartbeat)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "新しい状態の調停ループを台本の受付の上で止めの合図まで回し、出た遅れの行を返すため。"
  ;; 仮想の時計の眠り(Delay)は scheduler の上で進む。
  (run (scheduled ((sim-time-handler :clock (SimClock))
                    ((one-heartbeat inbox)
                      (request-bodies (reply-bodies (with-handlers (memory-notices (MemoryBroker))
                                                      ((kube-unavailable NO-KUBE (ObjectWatches) (ObjectWatches))
                                                        (run-coordinator (ClusterState) (ClusterTiming) (ClusterNaming))))))))))
  inbox.lags)


(deftest test-a-heartbeat-held-by-a-slow-save-is-named-once
  ;; 保存が 6 秒かかった歩の heartbeat は、送り手の名と、いちばん長い区間 SaveState を名指す行をちょうど 1 つ出す。
  (<- lags list (lags-of (OneHeartbeat (! (heartbeat-of "agent-worker-2" 0)) True)))
  (assert (= (len lags) 1) lags)
  (val lag (get lags 0))
  (assert (= lag.worker "agent-worker-2") lag)
  (assert (= lag.slowest "SaveState") lag)
  (assert (>= lag.slowest-ms (* SLOW-SECONDS 1000)) lag)
  (assert (> lag.elapsed-ms HEARTBEAT-LAG-MS) lag)
  (assert (>= lag.elapsed-ms lag.slowest-ms) lag)
  ;; 時刻は ISO の形(日付と時刻の区切り T・timezone つき)。
  (assert (in "T" lag.at) lag)
  (assert (or (.endswith lag.at "+00:00") (.endswith lag.at "Z")) lag))


(deftest test-a-heartbeat-that-waited-in-the-inbox-names-the-inbox
  ;; 受付の箱に 6 秒並んでいた heartbeat は、歩の中が速くても遅れとして名指し、区間の名は Inbox。
  (<- lags list (lags-of (OneHeartbeat (! (heartbeat-of "agent-worker-2" (int (* SLOW-SECONDS 1000)))) False)))
  (assert (= (len lags) 1) lags)
  (val lag (get lags 0))
  (assert (= lag.slowest "Inbox") lag)
  (assert (= lag.slowest-ms (int (* SLOW-SECONDS 1000))) lag))


(deftest test-a-fast-heartbeat-leaves-no-line
  ;; 並びも保存も速い heartbeat は行を出さない。
  (<- lags list (lags-of (OneHeartbeat (! (heartbeat-of "agent-worker-2" 0)) False)))
  (assert (= lags []) lags))
