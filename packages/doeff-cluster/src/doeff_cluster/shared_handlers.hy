;;; 共有の保存の handler: shared-http = coordinator の /board と /leases(クラスタ)。業務コードは ReadShared / WriteShared / LeaseOp しか
;;; 知らない。テストは業務の効果の fake を持たず、同じ shared-http を HTTP の層の fake の盤(tests/board_fake.hy — coordinator と同じ
;;; 純粋な判断で答える)の上で回す(#2337 の 3 本目で shared-memory を退役させた)。
;;; 要求の形(board-read-request・board-write-request・lease-request)は、この handler と手元の sim-cluster の偽の宿(local.hy)が
;;; 同じ関数で作る(本文を写さない)。
(require doeff-hy.macros [defhandler deff <- val])
(import urllib.parse [quote :as url-quote])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY AnyExpect JsonValue])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp LeaseAnswer])
(import doeff_hy.wire [parse])
(import doeff_cluster.foundation.coordinator_http [IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request resent-request
                                                         answer-json write-accepted])


(deff board-read-request [#^ str prefix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: prefix str)] :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadShared を coordinator の盤の読みの要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("GET" "/board" {"prefix" prefix} None))


(deff board-write-request [#^ str key value expect #^ (| int float None) ttl-seconds]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: value JsonValue) (: expect (| JsonValue AnyExpect))
         (: ttl-seconds (| int float None))]
   :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol"}}
  "WriteShared を盤の compare-and-set の要求 #(method path query 本文) にするため。expect の 3 値を JSON で運ぶ: 欄が無い = 無条件・
   null = 行が無い時だけ・値 = その値の時だけ。答えの読みは 409 = 偽(合わなかった)・300 未満 = 真。"
  #("PUT" (+ "/board/" key) {}
    (| {"value" value}
       (if (is expect ANY) {} {"expect" expect})
       (if (is ttl-seconds None) {} {"ttlSeconds" ttl-seconds}))))


(deff lease-request [#^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: op str) (: token str) (: permits int) (: ttl-ms int)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "LeaseOp を coordinator の lease の口の要求 #(method path query 本文) にするため(本番の shared-http と sim の宿で同じ形)。"
  #("POST" (+ "/leases/" (url-quote name :safe "")) {} {"op" op "token" token "permits" permits "ttlMs" ttl-ms}))


;; 本物の保存: coordinator の /board と /leases へ、汎用の HttpRequest で話す(#2337 の 2 本目)。宛先の順・切り替え・
;; 送り直しは宛先の部品(shared/protocol/coordinator_route.hy)— 宛先の状態は組み立てが渡す入れ物(RouteCell)で要求から要求へ持ち越す。
;; 出す HttpRequest に答える本物の I/O の答え手(http-production-handler)は、process の組み立ての根が外側に積む(業務の HttpRequest と同じ
;; 答え手 — 接続の段の上限は要求の connect-timeout-seconds が運ぶ)。
;;   読み(ReadShared)・claim と renew の lease = 何度送っても同じ意味なので、失敗は期限まで送り直す(resent-request)。
;;   書き(WriteShared)・release と drop の lease = 接続の段だけ送り直す(routed-request — 返事を読む前に切れた書きは届いたか分からない)。
;; 断り(4xx・5xx)は RouteRefused、返事の無い失敗は RouteUnreachable を業務の Program へ投げる(前の httpx の例外と同じく service を落とす)。
(defhandler shared-http [#^ RouteCell cell #^ RouteOptions options]
  (ReadShared [prefix]
    (setv #(method path query _) (board-read-request prefix))
    (<- reply RoutedReply (resent-request cell.route method path options query None IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS))
    (setv cell.route reply.route)
    (<- rows dict (answer-json reply.answer))
    (resume rows))
  (WriteShared [key value expect ttl-seconds]
    (setv #(method path _ body) (board-write-request key value expect ttl-seconds))
    (<- reply RoutedReply (routed-request cell.route method path options None body))
    (setv cell.route reply.route)
    (<- ok bool (write-accepted reply.answer))
    (resume ok))
  (LeaseOp [name op token permits ttl-ms]
    (setv #(method path _ body) (lease-request name op token permits ttl-ms))
    (<- reply RoutedReply (match (in op #("claim" "renew"))
                            True (resent-request cell.route method path options None body IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS)
                            False (routed-request cell.route method path options None body)))
    (setv cell.route reply.route)
    (<- answered dict (answer-json reply.answer))
    ;; 返事の本文を LeaseAnswer に解く(形が違えば Malformed — 業務へ素の dict を渡さない・#2523)。
    (<- answer LeaseAnswer (parse LeaseAnswer answered))
    (resume answer)))
