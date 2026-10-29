;; 退きの保ち(card ki-e3531030eb37): 入れ替え(handoff)で退いた process が退きの保ち(名前付きの lease RETIREMENT-HOLD)を持つ間は、
;; 宣言の寿命の上限(readiness の retiredSeconds)を越えても worker が止めず、走っている仕事が終わりまで回る。保ちを持たない退いた
;; process は今どおり上限で止められる(上限は、自分で終わらない退いた process を受け止める物 — 仕事の長さの上限ではない)。
;;
;; 筋書き: worker w1 だけが job holder(needs cluster-net・update handoff・30 秒の仕事・寿命の上限 10 秒 — tests/fixtures/working_programs.hy)を
;; 持てる。5 秒目に版 2 を宣言し、版 1 の process を退かせる。版 1 は退く知らせを受けて保ちを取り(対照は取らない)、仕事の後に保ちを
;; 返して自分で終わる。40 秒待って、版 1 の仕事が終わりまで回ったかを盤の行と process の終わりから読む。止めを試す筋書きなので、時間の設定は
;; 本番の既定(ClusterTiming)を明示する(比で延ばした世界では worker の拍が heartbeat の間隔まで起きず、寿命の上限の刻に止めを判じない)。
;; 本番(10-16 04:28:49 JST): 退いてから 6 時間の上限を越えた拍で SIGTERM され、6 時間 7 分走っていた仕事が切れた。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster [sim-cluster SimWorker SimProcess ProcessesOf SharedRows Redeclare])
(import tests.fixtures.lease_programs [lease-sim-foundation])
(import tests.fixtures.working_programs [HOLD-PREFIX holding-hosts holding-hosts-v2 unheld-hosts unheld-hosts-v2])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0))


(defrecord HoldSeen
  "筋書きが読んだ姿: processes = holder の process(起きた順)・rows = 盤の hold/ の行。"
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ dict rows))


(defk redeclared-during-the-work [second]
  {:pre [(: second System)] :post [(: % HoldSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 仕事の 5 秒目に系 second で宣言し直し、40 秒待って holder の process と盤の行を読む。"
  (<- (Delay 5.0))
  (<- _declared tuple (Redeclare second))
  (<- (Delay 40.0))
  (<- processes tuple (ProcessesOf "holder"))
  (<- rows dict (SharedRows (+ HOLD-PREFIX "/")))
  (HoldSeen :processes (tuple (sorted processes :key (fn [p] p.started-ms))) :rows rows))


(defk first-row [seen kind]
  {:pre [(: seen HoldSeen) (: kind str)] :post [(: % (| dict None))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "版 1 の process の盤の行 kind(begun・done・left)を読むため(無ければ None)。"
  (.get seen.rows (.format "{}/{}/{}" HOLD-PREFIX (. (get seen.processes 0) instance) kind)))


(deftest test-a-retired-process-holding-the-retirement-hold-finishes-its-work-past-its-lifetime
  (<- seen HoldSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) :workers #(W1) (holding-hosts lease-sim-foundation)
                                 (redeclared-during-the-work (holding-hosts-v2 lease-sim-foundation))))
  (assert (= (len seen.processes) 2) seen.processes)
  (val first (get seen.processes 0))
  (val begun (! (first-row seen "begun")))
  (val done (! (first-row seen "done")))
  (assert (is-not begun None) seen.rows)
  ;; 版 1 の仕事は、退いてから寿命の上限(10 秒)を越えても終わりまで回り、保ちを返して自分で終わる(止めで切られない)。
  (assert (is-not done None) #("版 1 の仕事が終わりまで回らなかった" first seen.rows))
  (assert (>= (- (get done "at") (get begun "at")) 30000) #(begun done))
  (assert (is-not (! (first-row seen "left")) None) #(first seen.rows))
  (assert (= first.exit-code 0) first)
  ;; 版 2 の process は動き続ける。
  (assert (is (. (get seen.processes 1) ended-ms) None) seen.processes))


(deftest test-a-retired-process-without-the-retirement-hold-still-stops-at-its-lifetime
  ;; 対照(直す前から緑): 保ちを取らない退いた process は、退いてから寿命の上限を越えた拍で止められる — 上限は、自分で終わらない退いた
  ;; process の受け止めとして残る。仕事の終わりは盤に無い。
  (<- seen HoldSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) :workers #(W1) (unheld-hosts lease-sim-foundation)
                                 (redeclared-during-the-work (unheld-hosts-v2 lease-sim-foundation))))
  (assert (= (len seen.processes) 2) seen.processes)
  (val first (get seen.processes 0))
  (assert (is-not (! (first-row seen "begun")) None) seen.rows)
  (assert (is (! (first-row seen "done")) None) #("保ちの無い版 1 が上限で止まらなかった" first seen.rows))
  (assert (is-not first.ended-ms None) first)
  (assert (!= first.exit-code 0) first))
