;;; worker の値の写しの係(ADR-DOE-CLUSTER-004): API server が答えている間に、この worker に置かれうる job の宣言の参照(heartbeat の
;;; 返事の supplied — coordinator の supplied-for が並べ、coordinator への口が入れ物 LinkState に写す)の値を、worker の作業の根の下に写しと
;;; して持つ。API server が止まっている間に置き直された job は、届かない読みをこの写しで答える(shared/protocol/supplied_values の頭の註)。
;;;
;;;   supplied-hold-step  1 回: 綴りの列が前に持ち直した時と変わったか、前に全部を読めてから refresh-seconds を過ぎたか、前の回に届かない
;;;                       物が在れば、hold-supplied で持ち直す(届かない間は前の写しを残す)。数えが前の回と違う時だけ 1 行を出す(値は出さない)
;;;   supplied-hold-loop  背景の task の本体: 止めの合図まで check-seconds ごとに 1 回
;;;
;;; 返事がまだ届いていない(綴りが None)間は何もしない — 写しを消さない。読みと書きの effect(HttpRequest・file・ReadEnvironment・時計)は
;;; worker の handler の組が答える(本番 = 区画の CA で検める in-cluster-api-routed の在る Pod の worker だけが係を起こす)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_core_effects [slog])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.protocol.supplied_values [HoldTally hold-supplied])
(import doeff_cluster._worker.protocol.coordinator_link [LinkState])

(val MODULE-TAGS {:context "worker" :role "protocol"})


(defrecord SuppliedHoldSettings
  "値の写しの係の設定(頭の註): worker = 写しの dir の名に成る worker の名・check-seconds = 回の間隔・refresh-seconds = 綴りが変わらなくても
   読み直す間隔(cluster の側の値の替わり — 鍵の回し — に写しを合わせる)。"
  {:check [(> check-seconds 0) (>= refresh-seconds check-seconds)]}
  (#^ str worker)
  (setv #^ float check-seconds 15.0)
  (setv #^ float refresh-seconds 300.0))


(defclass SuppliedHoldCell []
  "係の背景の task と組み立て(main)が分ける値。closing = 止めの合図・spellings = 前に持ち直した綴りの列(None = まだ)・held-at-ms = 前に
   全部の物を読めた刻・tally = 前の回の数え(同じ数えは 2 度言わない)・failure = 前の回で起きた例外の repr(同じ例外は 2 度言わない)。
   scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.closing False self.spellings None self.held-at-ms 0 self.tally None self.failure "")))


(defk supplied-hold-step [settings link cell]
  {:pre [(: settings SuppliedHoldSettings) (: link LinkState) (: cell SuppliedHoldCell)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "係の 1 回(頭の註): 要る時だけ写しを持ち直し、数えが変われば 1 行を出すため。"
  (val spellings link.supplied)
  (when (is spellings None)
    (return None))
  (<- now int (now-epoch-ms))
  (val due (or (!= spellings cell.spellings)
               (>= (- now cell.held-at-ms) (int (* settings.refresh-seconds 1000)))
               (and (is-not cell.tally None) (> cell.tally.kept 0))))
  (when (not due)
    (return None))
  (<- tally HoldTally (hold-supplied settings.worker spellings))
  (setv cell.spellings spellings)
  ;; 届かない物が在る回は刻を進めない(次の回に読み直す — 止まりが明けたらすぐ写しを今の値に揃える)。
  (when (= tally.kept 0)
    (setv cell.held-at-ms now))
  (when (!= tally cell.tally)
    (<- (slog "worker: 置かれうる job の参照の値の写しを持ち直しました" :level (if (> tally.kept 0) "warning" "info")
              :references (len spellings) :held tally.held :kept tally.kept :dropped tally.dropped :pruned tally.pruned)))
  (setv cell.tally tally)
  None)


(defk supplied-hold-loop [settings link cell]
  {:pre [(: settings SuppliedHoldSettings) (: link LinkState) (: cell SuppliedHoldCell)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "係の背景の task の本体(頭の註): 止めの合図まで check-seconds ごとに 1 回。1 回の中の例外(写しを書けない・作業の根の事実が無い ほか)は、
   違う例外の時だけ 1 行にして次の回へ進む(黙って止まらない・同じ例外を回ごとに書かない)。"
  (while (not cell.closing)
    (try
      (<- (supplied-hold-step settings link cell))
      (setv cell.failure "")
      (except [error Exception]
        (when (!= (repr error) cell.failure)
          (setv cell.failure (repr error))
          (<- (slog "worker: 参照の値の写しの持ち直しが失敗しました — 次の回へ進みます" :level "warning" :error (repr error))))))
    (<- (Delay settings.check-seconds)))
  None)
