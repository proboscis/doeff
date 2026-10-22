;; coordinator の heartbeat の返事の遅れの計りが、壁の時計が歩の途中で動いても遅い区間を名指す事の検(card ki-3d02b3888acb — worker の拍の遅れ #3825 の同型)。
;;
;; 実例の形: 歩の区間の長さ(Respond・Rollout・SaveState・Reply)を壁の時計(GetTime)の差で計ると、NTP などで時刻が戻った時に
;; 区間が負になり、実際に 6 秒かかった保存が遅れの行を出さない・いちばん長い区間と別の区間を名指す・進んだ時に速い歩を遅いと名指す。
;; 区間の計りは単調な時計(GetMonotonic)で読み、判断の now だけ壁の時計のまま。
;; 台本: 仮想の時計の下で、受付の代役が保存(SaveState)の待ちの最中に壁の時計だけを動かす(effect StepWallClock — 手前の handler が
;; GetTime の答えをずらし、GetMonotonic はそのまま仮想の時計が答える。SetTime では壁も単調も一緒に動いてしまう)。動かす契機を時刻の
;; 読みの回数や経過秒にせず、保存の待ちの最中に置く — 歩が壁の時計を何回読んでも、どの順で読んでも同じ台本で回る。
;; 受付の箱の並びの ms(queued-ms)は、もとから箱が単調な時計で測る — その事も下の検で押さえる。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import time)
(import unittest.mock [patch])
(import doeff [run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply Request CoordinatorStopRequested])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox RawRequest ReplySlot])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming SaveState])
(import doeff_cluster.coordinator.core.program [run-coordinator HEARTBEAT-LAG-LOG])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [kube-unavailable])

(val VERSIONS {"python" "3.14.0" "doeff" "1"})
;; 検の coordinator は k8s を持たない(本番の手元の coordinator と同じ答え手 — 調停ループは毎歩 Deployment と Node の見張りを揃える)。
(val NO-KUBE "検の coordinator は k8s を持たない")


(defrecord LagLine
  "heartbeat の返事の遅れの行 1 つの欄。"
  (#^ str at)
  (#^ str worker)
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defrecord StepScript
  "歩の台本: save-seconds = 保存の待ち(仮想の時計)・reply-seconds = 返事を置く待ち・wall-back-seconds = 保存の待ちの最中に壁の時計を
   戻す秒(負 = 進める・0 = 動かさない)。"
  (#^ float save-seconds)
  (#^ float reply-seconds)
  (#^ float wall-back-seconds))


(defrecord StepSeen
  "歩 1 つの見え方: lags = 出た遅れの行・started = heartbeat を渡した刻(壁の時計の epoch ms — 歩の頭と同じ仮想の刻)。"
  (#^ tuple lags)
  (#^ int started))


(defeffect StepWallClock
  "壁の時計だけを seconds 戻す(負 = 進める)— 検だけの操作。GetMonotonic は動かさない。"
  {:fields [(: seconds float)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped
  "外の世界の時計の手前に立つ代役: StepWallClock の分だけ GetTime の答えをずらす(壁の時計だけ動く — GetMonotonic は通す)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var back-seconds 0.0)
  (StepWallClock [seconds]
    (:= back-seconds (+ back-seconds seconds))
    (resume None))
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (- now (timedelta :seconds back-seconds)))))


(defclass StepInbox []
  "台本の受付: 最初の取りで heartbeat の要求 1 件を渡し、次の取りで止めの合図を立てて空で返す。保存と返事は台本の秒だけ仮想の時計で
   待たせ、最初の保存の待ちの最中に壁の時計を動かす。lags = 出た遅れの行・started = heartbeat を渡した刻。"
  (defn #^ None __init__ [self #^ Request request #^ StepScript script]
    (setv self.request request self.script script self.handed False self.done False self.saved False self.lags [] self.started 0)
    None))


(defhandler stepped-heartbeat [#^ StepInbox inbox]
  "受付・保存・返事・log の代役: 受付は台本の 1 件、保存と返事は台本の秒だけ待たせ、保存の待ちの最中に壁の時計を動かす。log は遅れの行だけ覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに要求と待ちと壁の時計の動きの台本を変え、出た行を検へ返す(Ask で区別できない)。
  (NextRequests [timeout-seconds limit]
    (if inbox.handed
        (do (setv inbox.done True)
            (resume []))
        (do (setv inbox.handed True)
            (<- started int (now-epoch-ms))
            (setv inbox.started started)
            (resume [inbox.request]))))
  (Reply [request status body]
    (when (> inbox.script.reply-seconds 0.0)
      (<- (Delay inbox.script.reply-seconds)))
    (resume None))
  (SaveState [before after]
    ;; 止める時の保存(状態が変わる 2 回目)には台本を当てない。
    (when (and (not inbox.saved) (is-not before after))
      (setv inbox.saved True)
      (when (> inbox.script.save-seconds 0.0)
        (<- (Delay inbox.script.save-seconds)))
      (<- (StepWallClock inbox.script.wall-back-seconds)))
    (resume None))
  (CoordinatorStopRequested [] (resume inbox.done))
  (SlogEffect []
    (val fields effect.kwargs)
    (when (= effect.msg HEARTBEAT-LAG-LOG)
      (setv inbox.lags (+ inbox.lags [(LagLine :at (get fields "at") :worker (get fields "worker") :elapsed-ms (get fields "elapsed_ms")
                                               :slowest (get fields "slowest") :slowest-ms (get fields "slowest_ms"))])))
    (resume None)))


(defk heartbeat-of [name]
  {:pre [(: name str)] :post [(: % Request)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name の POST /heartbeat の要求を作るため(受付の箱に並んだ ms は 0 — 区間の計りだけを見る)。"
  (! (http-request "POST" "/heartbeat" {} {"name" name "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" VERSIONS}
                   :actor name :peer name :queued-ms 0)))


(defk seen-on [script]
  {:pre [(: script StepScript)] :post [(: % StepSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "新しい状態の調停ループを台本の受付と仮想の時計の下で止めの合図まで回し、出た遅れの行と heartbeat を渡した刻を返すため。"
  (val inbox (StepInbox (! (heartbeat-of "agent-worker-2")) script))
  ;; 仮想の時計の眠り(Delay)は scheduler の上で進む。
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) wall-clock-stepped (stepped-heartbeat inbox) request-bodies reply-bodies]
                    (with-handlers (memory-notices (MemoryBroker))
                      ((kube-unavailable NO-KUBE)
                        (run-coordinator (ClusterState) (ClusterTiming) (ClusterNaming)))))))
  (StepSeen :lags (tuple inbox.lags) :started inbox.started))


(deftest test-a-slow-save-is-named-though-the-wall-clock-stepped-back-beyond-it
  ;; 保存が 6 秒かかる間に壁の時計が 10 秒戻る: 歩は実際に 6 秒かかったので、遅れの行は SaveState を 6 秒以上と名指す。
  ;; 壁の時計の差で計ると、保存の区間も歩の経過も -4 秒になり行が出ない。
  (<- seen StepSeen (seen-on (StepScript :save-seconds 6.0 :reply-seconds 0.0 :wall-back-seconds 10.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.worker "agent-worker-2") lag)
  (assert (= lag.slowest "SaveState") lag)
  (assert (>= lag.slowest-ms 6000) lag)
  (assert (>= lag.elapsed-ms 6000) lag))


(deftest test-the-longest-span-is-named-though-the-wall-clock-shortened-it
  ;; 保存が 6 秒・返事を置くのが 4 秒かかる間に壁の時計が 4 秒戻る: いちばん長いのは SaveState(6 秒)。壁の時計の差で計ると保存が 2 秒に
  ;; 縮み、4 秒の Reply を名指す。
  (<- seen StepSeen (seen-on (StepScript :save-seconds 6.0 :reply-seconds 4.0 :wall-back-seconds 4.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.slowest "SaveState") lag)
  (assert (>= lag.slowest-ms 6000) lag)
  (assert (>= lag.elapsed-ms 10000) lag))


(deftest test-a-quick-step-is-quiet-though-the-wall-clock-stepped-forward
  ;; 反対向き: 何も待たない歩の保存の最中に壁の時計が 10 秒進む。歩は一瞬なので遅れの行は出ない。壁の時計の差で計ると SaveState が
  ;; 10 秒に伸び、速い歩を遅いと名指す。
  (<- seen StepSeen (seen-on (StepScript :save-seconds 0.0 :reply-seconds 0.0 :wall-back-seconds -10.0)))
  (assert (= seen.lags #()) seen))


(deftest test-a-step-without-a-wall-clock-step-is-named-as-before
  ;; 対照: 壁の時計が動かない歩は、今も同じ名指し(この検は直す前も後も緑)。
  (<- seen StepSeen (seen-on (StepScript :save-seconds 6.0 :reply-seconds 0.0 :wall-back-seconds 0.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.slowest "SaveState") lag)
  (assert (>= lag.slowest-ms 6000) lag))


(deftest test-the-line-is-stamped-with-the-wall-clock-of-the-moment
  ;; 遅れの行の at は、返事を置き終えた刻の壁の時計(区間の計りは単調な時計でも、行の刻は他の log と並べる壁の時計)。壁の時計が 10 秒戻った
  ;; 歩では、歩の頭に読んだ壁の時刻から実際の経過(6 秒)を足して戻した分(10 秒)を引いた刻 = 戻った後の壁の枠。
  (<- seen StepSeen (seen-on (StepScript :save-seconds 6.0 :reply-seconds 0.0 :wall-back-seconds 10.0)))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (= lag.at (.isoformat (datetime-of-epoch-ms (- (+ seen.started 6000) 10000)))) #(lag seen)))


(deftest test-the-inbox-wait-is-measured-by-the-monotonic-clock
  ;; 受付の箱に並んでいた ms(Request.queued-ms)は、箱が単調な時計(time.monotonic)の差で測る — 札を作った刻と取った刻の差のまま。
  ;; 壁の時計の差で測ると、時刻が戻った時に負になり max 0 で 0 に丸まって、並んでいた長さが消える。
  (val inbox (RequestInbox 0 30.0))
  (with [_ (patch.object time "monotonic" :side-effect [100.0 102.5])]
    (val raw (RawRequest "POST" "/heartbeat" {} None (ReplySlot) "agent-worker-2" "agent-worker-2"))
    (.offer inbox raw)
    (val batch (.taken inbox 10))
    (assert (= (len batch) 1) batch)
    (assert (= (. (get batch 0) queued-ms) 2500) batch)))
