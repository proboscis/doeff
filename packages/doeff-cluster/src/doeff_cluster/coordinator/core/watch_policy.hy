;;; 版の変化を待つ読み(GET /watch?after=<版>&timeoutSeconds=<秒>[&worker=<名>&boot=<世代>])の判断(純粋・I/O はしない — #1933)。
;;;
;;; 送り手は最後に知った coordinator 全体の版(ClusterState.revision)を after で渡す。調停ループ(coordinator.coordinator-step)は
;;; 要求を待ち(Watcher)として持ち、書きの後と拍ごとにここで判じ、版が after と違えば {"revision" 今の版 "changed" 真} を、期限を
;;; 過ぎれば {"revision" 今の版 "changed" 偽} を返す。worker を名指した待ちは、版が進んでも、その worker の heartbeat の返事(温める表と
;;; 版の欄を除く — 同じ関数 cluster_policy.heartbeat-reply で作る)が変わらない間は起きない(他の worker の仕事の変化で起こさない)。
;;; 版を比べるのは等しいか(大小ではない — 置き場を失って起き直した coordinator の版が送り手の知る版より小さくても、変わったと答える)。
(require doeff-hy.macros [defk <- val var])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState HeartbeatReply Watcher WatchRefusal WatchAnswer WatchStep] doeff_cluster.shared.intent.protocol [WATCH-MAX-SECONDS])
(import doeff_cluster.coordinator.core.cluster_policy [heartbeat-reply])
(import doeff_cluster.coordinator.core.api_policy [ready-instances])

;; worker の見え方に入れない返事の欄: 温める表(期限で変わる先読み — 次の heartbeat で届けば足りる)と版(版が進むたびに変わる)。
;; 見え方に数えない返事の欄 = 温める表(warm)と版(revision)— worker-mark が空にして比べる。


(defk query-revision [value]
  {:pre [(: value (| str int None))] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "問いの after(知っている版)を読むため: 0 以上の整数(文字列の数字か int)。読めなければ None。"
  (match value
    (int) (if (>= value 0) value None)
    (str) (if (and value (.isdigit value)) (int value) None)
    _ None))


(defk query-seconds [value]
  {:pre [(: value (| str int float None))] :post [(: % (| float None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "問いの timeoutSeconds を読むため: 0 以上の有限の数(文字列か数)を WATCH-MAX-SECONDS で頭打ちにする。無ければ上限。読めなければ None。"
  (val number (match value
                None WATCH-MAX-SECONDS
                (| (int) (float)) (float value)
                (str) (try (float value) (except [ValueError] None))
                _ None))
  (if (and (is-not number None) (<= 0.0 number) (< number (float "inf")))
      (min number WATCH-MAX-SECONDS)
      None))


(defk watch-of [request now]
  {:pre [(: request Request) (: now int)] :post [(: % (| Watcher WatchRefusal None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "受けた要求が版の変化を待つ読み(GET /watch)なら、その待ち(期限 = now + timeoutSeconds)か、読めない問いの断りにするため。
   それ以外の要求は None(受け口の振り分け api_policy.respond へ渡す)。"
  (if (not (and (= request.method "GET") (= (tuple request.parts) #("watch"))))
      None
      (do (<- after (| int None) (query-revision (.get request.query "after")))
          (<- seconds (| float None) (query-seconds (.get request.query "timeoutSeconds")))
          (cond
            (is after None) (WatchRefusal request "after(知っている coordinator の版 — 0 以上の整数)が要る")
            (is seconds None) (WatchRefusal request "timeoutSeconds は 0 以上の数")
            True (Watcher :request request :after after :deadline-ms (+ now (int (* 1000 seconds)))
                          :worker (.get request.query "worker") :boot (.get request.query "boot"))))))


(defk worker-mark [state worker boot now timing]
  {:pre [(: state ClusterState) (: worker str) (: boot (| str None)) (: now int) (: timing ClusterTiming)] :post [(: % HeartbeatReply)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "名指した worker の見え方(その世代の heartbeat の返事から温める表と版の欄を空にした物)を、返事と同じ関数で作るため — 待ちが起きる
   条件と worker が受け取る物の定義を 2 つにしない。"
  (val reply (heartbeat-reply state worker timing (ready-instances state worker now timing) :now now :boot boot))
  (replace reply :warm #() :revision 0))


(defk watch-deadline [watcher state now]
  {:pre [(: watcher Watcher) (: state ClusterState) (: now int)] :post [(: % WatchStep)] :tags {:context "doeff-cluster" :role "judgment"}}
  "変わっていない待ちを、期限を過ぎていれば「変わっていない」の答えで返し、それ以外は待ち続けさせるため。"
  (WatchStep :answer (if (>= now watcher.deadline-ms) (WatchAnswer state.revision False) None) :watcher watcher))


(defk settle-watch [watcher state now timing]
  {:pre [(: watcher Watcher) (: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % WatchStep)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "待ち 1 件を今の状態で判じるため。worker を名指さない待ちは版が after と違えば起きる。名指した待ちは、最初に見た時に版が after と
   違えば(送り手の知らない変化が既にある)起き、同じならその時の見え方を覚え、後で版が進んだ時に見え方が変わっていれば起きる
   (変わっていなければ覚えた版を進めて待ち続ける)。どれでもなければ期限で返す。"
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


(defk earliest-deadline [watchers]
  {:pre [(: watchers tuple)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "待ちのいちばん早い期限(待ちが無ければ None)— 模擬の時計の下の受け口が、その刻の後の拍を飛ばさないため(IdleProbe.wake-ms)。"
  (if watchers (min (gfor w watchers w.deadline-ms)) None))
