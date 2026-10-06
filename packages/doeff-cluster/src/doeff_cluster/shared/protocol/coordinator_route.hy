;;; coordinator への宛先の部品 — 宛先の順・切り替え・送り直しを、汎用の効果(doeff-core-effects の HttpRequest を failures-as-values で・
;;; doeff-time の GetTime / Delay)だけで書く(#2337 の 1 本目)。
;;;
;;; 振る舞いの出自(2026-09-23 の tailnet の経路の揺れの実測と直しの理由)は foundation/coordinator_http.hy の頭の註(前は同じ振る舞いを
;;; httpx の client を持つ口が持っていた — #2427 で退役):
;;;   - 宛先を前から順に試す。接続できない時(HttpFailed の kind = CONNECT-FAILED — 接続の段の時間切れを含む・要求はまだ相手に
;;;     届いていない)だけ次の宛先へ回る。読みや返事の途中の失敗(TIMED-OUT・OTHER)では回らない(宛先の問題と限らない)。
;;;   - 回った後も recheck-ms ごとに先頭の宛先を先に試し、届けば戻る。
;;;   - 全部の宛先に届かなければ connect-retries 回まで間を置いて一巡し直し、最後の接続の失敗を答える。
;;;   - 何度送っても同じ意味の要求(resent-request)は、答えが失敗なら期限まで間を置いて送り直す。書きは接続の段だけ(上の一巡)。
;;; 宛先の状態は object に持たず値(CoordinatorRoute)で返す — 使い手(handler)が次の要求へ渡す。
;;; 本物の I/O は入口が積む汎用の答え手(http-production-handler)が持ち、この module は socket も時計も直に読まない。
;;; 使い手の付け替え(shared-http・heartbeat・task・readiness の口)は #2337 の 2・4 本目。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import json)
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})

;; 一巡し直す前の間(秒)の物差し: 1 回目 0.25・2 回目 0.5 …。
(val ROUND-PAUSE-SECONDS 0.25)


(defrecord CoordinatorRoute
  "coordinator の宛先の並びと、いま使う宛先。urls = 前ほど優先(Mac なら LAN・tailnet の順)・active = いま使う宛先の位置・
   switched-at-ms = 最後に宛先を替えた / 先頭を試し直した時刻(epoch ms)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ (get tuple #(str ...)) urls)
  (#^ int active)
  (#^ int switched-at-ms))


(defrecord RouteOptions
  "送り方。reply-seconds = 1 回の要求の上限(秒)・watch-seconds = 版の変化を待つ読み(GET /watch)で待ってもらう上限(秒 — reply-seconds より
   短い。どちらも組み立てる側が ClusterTiming の watch-max-ms・client-reply-ms から渡す・#3865)・connect-seconds = 接続の段だけの上限(秒 — tailnet の SYN の取り落としは待つより
   次の宛先へ)・connect-retries = 全部の宛先に届かない時に一巡し直す回数・recheck-ms = 先頭以外に
   いる間、先頭を試し直す間隔・actor = 書きの送り手(header X-Actor — coordinator は出来事の記録に残す)・
   resend-deadline-seconds = 何度送っても同じ意味の要求を通信の失敗を越えて送り直す上限(秒)・resend-pause-seconds = 送り直しの間(秒)。
   送り直しの 2 つの値は組み立てる側(entry・平たい組み立ての module)が foundation/coordinator_http の値を渡す — 層 protocol は
   foundation を読めない(#2565)。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ float reply-seconds)
  (#^ float watch-seconds)
  (#^ float connect-seconds)
  (#^ int connect-retries)
  (#^ int recheck-ms)
  (#^ str actor)
  (#^ float resend-deadline-seconds)
  (#^ float resend-pause-seconds))


(defrecord RouteTurn
  "1 巡の試す順(宛先の位置の列)と、試し直しの時刻を進めた宛先の状態。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ (get tuple #(int ...)) indices)
  (#^ CoordinatorRoute route))


(defclass RouteCell []
  "handler が要求から要求へ持ち越す宛先の状態の入れ物(handler の引数 — 組み立てが route-of で作った初めの状態を入れて渡す)。
   宛先の部品そのものは値を返すだけで、これを書き換えるのは使い手の handler の節だけ。"
  (defn #^ None __init__ [self #^ CoordinatorRoute route]
    (setv self.route route)))


(defrecord RoutedReply
  "要求の答え(返事か、どの宛先にも届かなかった・途中で切れた失敗)と、次の要求へ渡す宛先の状態。"
  {:tags {:context "doeff-cluster" :role "protocol"}}
  (#^ (| HttpResponse HttpFailed) answer)
  (#^ CoordinatorRoute route))


(defk route-of [spec now-ms]
  {:pre [(: spec str) (: now-ms int)] :post [(: % CoordinatorRoute)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宛先の指定(URL を `,` で並べた文字列 — 前ほど優先)から、先頭を使う宛先の状態を作るため。宛先が 1 つも無ければ ValueError。"
  (val urls (tuple (gfor u (.split spec ",") :if (.strip u) (.rstrip (.strip u) "/"))))
  (when (not urls)
    (raise (ValueError (.format "coordinator の宛先が無い: {!r}" spec))))
  (CoordinatorRoute :urls urls :active 0 :switched-at-ms now-ms))


(defk route-order [route now-ms recheck-ms]
  {:pre [(: route CoordinatorRoute) (: now-ms int) (: recheck-ms int)] :post [(: % RouteTurn)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "1 巡の試す順を決めるため: いまの宛先から(残りは並びの順)。先頭以外にいる間は、最後に先頭を試した時から recheck-ms を過ぎていれば
   先頭から試し、試した時刻を進める。"
  (val rest (tuple (gfor i (range (len route.urls)) :if (!= i route.active) i)))
  (match #((!= route.active 0) (>= (- now-ms route.switched-at-ms) recheck-ms))
    #(True True) (RouteTurn :indices (tuple (range (len route.urls))) :route (replace route :switched-at-ms now-ms))
    _ (RouteTurn :indices #(route.active #* rest) :route route)))


(defk route-used [route index now-ms]
  {:pre [(: route CoordinatorRoute) (: index int) (: now-ms int)] :post [(: % CoordinatorRoute)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "答えが返った宛先を、次の要求のいまの宛先にするため(替わった時だけ替えた時刻を進める)。"
  (match (= index route.active)
    True route
    False (replace route :active index :switched-at-ms now-ms)))


(defk round-pause-seconds [round]
  {:pre [(: round int)] :post [(: % float)] :tags {:context "doeff-cluster" :role "protocol"}}
  "一巡し直す前の間(秒)— round 回目(1 から)。"
  (* ROUND-PAUSE-SECONDS (** 2 (- round 1))))


(defk routed-request [route method path options params body]
  {:pre [(: route CoordinatorRoute) (: method str) (: path str) (: options RouteOptions) (: params (| dict None))
         (: body (| dict list None))]
   :post [(: % RoutedReply)] :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "要求 1 つを宛先の順に送るため: 接続できない時(CONNECT-FAILED)だけ次の宛先へ回り、全部に届かなければ間を置いて
   connect-retries 回まで一巡し直す。答え = 最初に返った返事(4xx・5xx も返事)か、途中で切れた失敗か、最後の接続の失敗。"
  (var current route)
  (var last None)
  (for [round (range (+ options.connect-retries 1))]
    (when (> round 0)
      (<- pause float (round-pause-seconds round))
      (<- (Delay pause)))
    (<- started int (now-epoch-ms))
    (<- turn RouteTurn (route-order current started options.recheck-ms))
    (:= current turn.route)
    (for [index turn.indices]
      (<- answer (HttpRequest method (+ (get current.urls index) path)
                              :headers {"X-Actor" options.actor} :params params :body body
                              :timeout-seconds options.reply-seconds :connect-timeout-seconds options.connect-seconds
                              :max-retries 0 :failures-as-values True))
      (match answer
        (HttpFailed :kind HttpFailureKind.CONNECT-FAILED) (:= last answer)
        _ (do (<- now int (now-epoch-ms))
              (<- used CoordinatorRoute (route-used current index now))
              (return (RoutedReply :answer answer :route used))))))
  (RoutedReply :answer last :route current))


(defk resent-request [route method path options params body deadline-seconds pause-seconds]
  {:pre [(: route CoordinatorRoute) (: method str) (: path str) (: options RouteOptions) (: params (| dict None))
         (: body (| dict list None)) (: deadline-seconds float) (: pause-seconds float)]
   :post [(: % RoutedReply)] :tags {:context "doeff-cluster" :role "protocol"}}
  "何度送っても同じ意味の要求(読み・同じ鍵の置き)を、答えが失敗(どの種類でも)なら期限まで間を置いて送り直すため。書きには使わない
   — 返事を読む前に切れた書きは相手に届いたか分からない(書きの送り直しは routed-request の接続の段だけ)。"
  (<- started int (now-epoch-ms))
  (var reply (RoutedReply :answer None :route route))
  (while True
    (<- got RoutedReply (routed-request reply.route method path options params body))
    (:= reply got)
    (<- now int (now-epoch-ms))
    (match #((isinstance got.answer HttpFailed) (> (+ (- now started) (* pause-seconds 1000)) (* deadline-seconds 1000)))
      #(True False) (<- (Delay pause-seconds))
      _ (return got))))


(defclass RouteRefused [Exception]
  "coordinator が要求を断った(4xx・5xx)— status と coordinator の返した本文(先頭 500 字)。断りの理由(coordinator の {\"error\": …})を
   worker の log と状態の note に出すため本文を持つ(前の httpx の client を持つ口の断りと同じ読み)。"
  (defn #^ None __init__ [self #^ int status #^ str body]
    (.__init__ (super) status body)
    (setv self.status status self.body body))
  (defn #^ str __repr__ [self]
    (.format "coordinator が断った({}): {}" self.status self.body)))


(defclass RouteUnreachable [Exception]
  "どの宛先からも返事が無かった(接続できない・途中で切れた・時間切れ)— 失敗の値 HttpFailed を持つ。"
  (defn #^ None __init__ [self #^ HttpFailed failed]
    (.__init__ (super) failed.url failed.detail)
    (setv self.failed failed))
  (defn #^ str __repr__ [self]
    (.format "coordinator に届かない({}): {}" self.failed.url self.failed.detail)))


(defk answer-json [answer]
  {:pre [(: answer (| HttpResponse HttpFailed None))] :post [(: % (| dict list str int float bool None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "答えを本文の JSON の値にするため: 300 未満の返事 = 本文の JSON・断り(400 以上)= RouteRefused・返事の無い失敗 = RouteUnreachable。"
  (match answer
    (HttpResponse) (match (>= answer.status 400)
                     True (raise (RouteRefused answer.status (cut answer.text 0 500)))
                     False (json.loads answer.text))
    (HttpFailed) (raise (RouteUnreachable answer))
    _ (raise (RuntimeError "coordinator の宛先が無いので送れなかった"))))


(defk write-accepted [answer]
  {:pre [(: answer (| HttpResponse HttpFailed None))] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "compare-and-set の書きの答えを読むため: 409 = 合わなかった = 偽・300 未満 = 真・ほかの断りと返事の無い失敗は answer-json と同じく投げる。"
  (match answer
    (HttpResponse :status 409) False
    _ (do (<- _body (answer-json answer)) True)))
