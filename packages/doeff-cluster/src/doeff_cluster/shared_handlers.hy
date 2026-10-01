;;; 共有の保存の handler: shared-http = coordinator の /board と /leases(クラスタ)。業務コードは ReadShared / WriteShared / LeaseOp しか
;;; 知らない。テストは業務の効果の fake を持たず、同じ shared-http を HTTP の層の fake の盤(tests/board_fake.hy — coordinator と同じ
;;; 純粋な判断で答える)の上で回す(#2337 の 3 本目で shared-memory を退役させた)。
;;; 要求の形(board-read-request・board-write-request・lease-request)は foundation/board_requests.hy — この handler と手元の sim-cluster の
;;; 偽の宿(local.hy)が同じ関数で作る(本文を写さない)。
(require doeff-hy.macros [defhandler <- val])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp])
(import doeff_cluster.foundation.coordinator_http [IDEMPOTENT-DEADLINE-SECONDS RESEND-PAUSE-SECONDS])
(import doeff_cluster.foundation.board_requests [board-read-request board-write-request lease-request])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request resent-request
                                                         answer-json write-accepted])


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
    (setv #(method path _ body) (board-write-request key value (is-not expect ANY) (if (is expect ANY) None expect) ttl-seconds))
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
    (<- answer dict (answer-json reply.answer))
    (resume answer)))
