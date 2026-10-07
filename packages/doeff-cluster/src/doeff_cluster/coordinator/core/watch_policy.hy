;;; 版の変化を待つ読み(GET /watch?after=<版>&timeoutSeconds=<秒>[&worker=<名>&boot=<世代>])の判断(純粋・I/O はしない — #1933)。
;;;
;;; 送り手は最後に知った coordinator 全体の版(ClusterState.revision)を after で渡す。調停ループ(coordinator.coordinator-step)は
;;; 要求を待ち(Watcher)として持ち、書きの後と拍ごとにここで判じ、版が after と違えば {"revision" 今の版 "changed" 真} を、期限を
;;; 過ぎれば {"revision" 今の版 "changed" 偽} を返す。worker を名指した待ちは、版が進んでも、その worker の heartbeat の返事(版の欄を
;;; 除く — 同じ関数 cluster_policy.heartbeat-reply で作る)が変わらない間は起きない(他の worker の仕事の変化で起こさない)。
;;; 版を比べるのは等しいか(大小ではない — 置き場を失って起き直した coordinator の版が送り手の知る版より小さくても、変わったと答える)。
;;; lease=<名> の待ち(GET /watch?lease=<名>[&timeoutSeconds=<秒>])は版を見ず、その名前付きの lease に空きがある時に起きる(今空いていれば
;;; すぐ — claim を断られてから待ちに入るまでに返された空きも取りこぼさない)。担い手の期限切れで空く刻は lease_rules.lease-full-until が
;;; 求め、調停ループはその刻に起きる(wake_policy.watchers-due — #3865 の後の単位)。
(require doeff-hy.macros [defk deff <- val var])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState HeartbeatReply Watcher WatchRefusal WatchAnswer WatchStep])
(import doeff_cluster.coordinator.core.cluster_policy [heartbeat-reply])
(import doeff_cluster.coordinator.core.api_policy [ready-instances])
(import doeff_cluster.shared.core.lease_rules [lease-full-until semaphore-key])

;; 見え方に数えない返事の欄は版(revision — 版が進むたびに変わる)だけ。worker-mark が 0 にして比べる。
;; 温める表(warm)は見え方に入る: その worker に当たる行(cluster_policy.warms-for — 返事と同じ選び方)の鍵と宣言だけで、期限は
;; 載せない(行の鍵が宣言と needs の組から決まる)。当たる行の出入りは Worker の資源の行の status の warm に写り版を進める
;; (resource_policy.worker-row — 同じ warms-for)ので、送り手が POST /warm で頼んだ刻に、当たる worker を名指した待ちが起き、worker は
;; 次の heartbeat を待たずに準備を始める。期限の延長だけの書きでは版も見え方も変わらず、待ちは起きない。


(defk query-revision [value]
  {:pre [(: value (| str int None))] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "問いの after(知っている版)を読むため: 0 以上の整数(文字列の数字か int)。読めなければ None。"
  (match value
    (int) (if (>= value 0) value None)
    (str) (if (and value (.isdigit value)) (int value) None)
    _ None))


(defk query-seconds [value limit]
  {:pre [(: value (| str int float None)) (: limit float)] :post [(: % (| float None))] :tags {:context "coordinator" :role "judgment"}}
  "問いの timeoutSeconds を読むため: 0 以上の有限の数(文字列か数)を待ちの上限 limit(秒 — ClusterTiming.watch-max-ms)で頭打ちにする。
   無ければ上限。読めなければ None。"
  (val number (match value
                None limit
                (| (int) (float)) (float value)
                (str) (try (float value) (except [ValueError] None))
                _ None))
  (if (and (is-not number None) (<= 0.0 number) (< number (float "inf")))
      (min number limit)
      None))


(defk watch-of [request now timing]
  {:pre [(: request Request) (: now int) (: timing ClusterTiming)] :post [(: % (| Watcher WatchRefusal None))] :tags {:context "coordinator" :role "judgment"}}
  "受けた要求が版の変化を待つ読み(GET /watch)なら、その待ち(期限 = now + timeoutSeconds)か、読めない問いの断りにするため。
   それ以外の要求は None(受け口の振り分け api_policy.respond へ渡す)。"
  (if (not (and (= request.method "GET") (= (tuple request.parts) #("watch"))))
      None
      (do (val lease (.get request.query "lease"))
          ;; lease の待ちは版を見ないので after を求めない(Watcher の after は使われない 0)。
          (<- after (| int None) (if lease (query-revision 0) (query-revision (.get request.query "after"))))
          (<- seconds (| float None) (query-seconds (.get request.query "timeoutSeconds") (/ timing.watch-max-ms 1000.0)))
          (cond
            (and lease (is-not (.get request.query "worker") None)) (WatchRefusal request "lease の待ちに worker は名指せない")
            (is after None) (WatchRefusal request "after(知っている coordinator の版 — 0 以上の整数)が要る")
            (is seconds None) (WatchRefusal request "timeoutSeconds は 0 以上の数")
            True (Watcher :request request :after after :deadline-ms (+ now (int (* 1000 seconds)))
                          :worker (.get request.query "worker") :boot (.get request.query "boot") :lease (or lease None))))))


(deff lease-row [#^ ClusterState state #^ str name]  ; defk にできない: all-waiting-unchanged の内包表記の中から呼ぶ純粋な読み
  {:pre [(: state ClusterState) (: name str)] :post [(: % (| dict None))] :tags {:context "coordinator" :role "judgment"}}
  "名前付きの lease の盤の行の値(行が無ければ None)を読むため — lease の待ちの判断が lease_rules へ渡す値。"
  (setv entry (.get state.board (semaphore-key name)))
  (if (is entry None) None entry.value))


(defk lease-full-at [watcher state now]
  {:pre [(: watcher Watcher) (: state ClusterState) (: now int)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "lease の待ちが待つ lease が今満ちていれば最初に空く刻、空いていれば None を知るため(盤の行 semaphore/<名> の値を lease_rules で判じる)。"
  (lease-full-until (lease-row state watcher.lease) now))


(defk worker-mark [state worker boot now timing]
  {:pre [(: state ClusterState) (: worker str) (: boot (| str None)) (: now int) (: timing ClusterTiming)] :post [(: % HeartbeatReply)]
   :tags {:context "coordinator" :role "judgment"}}
  "名指した worker の見え方(その世代の heartbeat の返事から版の欄を 0 にした物 — 当たる温める行を含む)を、返事と同じ関数で作るため
   — 待ちが起きる条件と worker が受け取る物の定義を 2 つにしない。"
  (<- reply HeartbeatReply (heartbeat-reply state worker timing (ready-instances state worker now timing) :now now :boot boot))
  (replace reply :revision 0))


(defk watch-deadline [watcher state now]
  {:pre [(: watcher Watcher) (: state ClusterState) (: now int)] :post [(: % WatchStep)] :tags {:context "coordinator" :role "judgment"}}
  "変わっていない待ちを、期限を過ぎていれば「変わっていない」の答えで返し、それ以外は待ち続けさせるため。"
  (WatchStep :answer (if (>= now watcher.deadline-ms) (WatchAnswer state.revision False) None) :watcher watcher))


(defk all-waiting-unchanged [watchers state now]
  {:pre [(: watchers tuple) (: state ClusterState) (: now int)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "どの待ちにも settle-watch が答えず、待ちをそのまま返す時か(版が after のまま・worker を名指した待ちは見え方を覚え済み・期限の
   前)を、見え方を作らずに知るため — 次に起きる刻(wake_policy.watchers-due)が、この時は待ちの期限まで待ってよいと判じる
   (#3865)。条件は settle-watch の枝のうち「起きない・覚え直さない・期限で返さない」枝と同じ(同値の検 = tests/test_watch.hy)。"
  ;; lease の待ちは、その lease が満ちていて期限の前なら落ち着いている(空けば settle-watch が起こす)。
  (all (gfor watcher watchers
             (if (is-not watcher.lease None)
                 (and (is-not (lease-full-until (lease-row state watcher.lease) now) None) (< now watcher.deadline-ms))
                 (and (= state.revision watcher.after)
                      (or (is watcher.worker None) (is-not watcher.mark None))
                      (< now watcher.deadline-ms))))))


(defk settle-watch [watcher state now timing]
  {:pre [(: watcher Watcher) (: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % WatchStep)]
   :tags {:context "coordinator" :role "judgment"}}
  "待ち 1 件を今の状態で判じるため。worker を名指さない待ちは版が after と違えば起きる。名指した待ちは、最初に見た時に版が after と
   違えば(送り手の知らない変化が既にある)起き、同じならその時の見え方を覚え、後で版が進んだ時に見え方が変わっていれば起きる
   (変わっていなければ覚えた版を進めて待ち続ける)。どれでもなければ期限で返す。"
  (when (is-not watcher.lease None)
    (<- full (| int None) (lease-full-at watcher state now))
    (return (if (is full None)
                (WatchStep :answer (WatchAnswer state.revision True) :watcher watcher)
                (! (watch-deadline watcher state now)))))
  (val moved (!= state.revision watcher.after))
  (var woke False)
  (var kept watcher)
  (cond
    (and moved (or (is watcher.worker None) (is watcher.mark None))) (:= woke True)
    (is watcher.worker None) None
    (is watcher.mark None)
      (do (<- first HeartbeatReply (worker-mark state watcher.worker watcher.boot now timing))
          (:= kept (replace watcher :mark first)))
    moved
      (do (<- seen HeartbeatReply (worker-mark state watcher.worker watcher.boot now timing))
          (if (!= seen watcher.mark)
              (:= woke True)
              (:= kept (replace watcher :after state.revision)))))
  (if woke
      (WatchStep :answer (WatchAnswer state.revision True) :watcher watcher)
      (do (<- step WatchStep (watch-deadline kept state now))
          step)))
