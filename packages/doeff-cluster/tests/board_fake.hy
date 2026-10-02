;;; 共有の保存の HTTP の層の fake — 本物の保存 shared-http が宛先の部品から出す HttpRequest(coordinator の /board と /leases)に、
;;; coordinator と同じ純粋な判断(board_rules・lease_rules)で、同じ process の dict を盤として答える(#2337 の 3 本目 — 業務の効果の
;;; fake だった shared-memory を替えた。業務の効果 ReadShared・WriteShared・LeaseOp は本番と同じ shared-http が言い換える)。
;;;
;;; 本物の coordinator の答え(api_policy.respond-board・cluster_policy.board-write / lease-write)との違い = 契約の外:
;;; 行の版(resourceVersion・expectVersion)・盤の容量の上限(507)・期限つきの行の期限切れ(行は残り続ける)・lease の行への直の書きの断り
;;; (semaphore-write-refusal)は持たない。網も持たない(いつでも届く)。
;;; 使い手は (board-handlers store) を with-handlers の list に展開する(外側に doeff-time の時計の handler が要る — 宛先の部品と lease の
;;; 判断が時刻を読む)。store = 盤の行 {鍵: 値}(検が中を見る・書き換える dict そのもの)。
(require doeff-hy.macros [defk deff defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import urllib.parse [urlsplit unquote :as url-unquote])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.board_rules [board-allows board-ttl-refusal])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-key])
(import doeff_hy.wire [dump])
(import doeff_cluster.shared.intent.protocol [BodyInvalid])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions])
(import doeff_cluster.shared.protocol.shared_handlers [shared-http])
(import doeff_cluster.foundation.coordinator_http [RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])

;; fake の盤の宛先(この URL で始まる要求だけに答える — ほかの HttpRequest は外側へ通す)と、shared-http の送り方。
(val BOARD-URL "http://board-fake")
(val BOARD-ROUTE (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 0 :recheck-ms 60000 :actor "board-fake"))


(defrecord BoardReply
  "fake の盤の答え 1 つ: status・本文(JSON の object)・書く行(#(鍵 値) か、行を変えないなら None)。"
  {:tags {:context "doeff-cluster-test" :role "judgment"}}
  (#^ int status)
  (#^ dict answer)
  (#^ (| tuple None) written))


(defk board-reply [rows method path query body now-ms]
  {:pre [(: rows dict) (: method str) (: path str) (: query dict) (: body (| dict None)) (: now-ms int)]
   :post [(: % BoardReply)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "盤の要求 1 つの答えを決めるため(coordinator の respond-board と同じ振り分け): GET /board = prefix で始まる行・
   PUT /board/<鍵> = compare-and-set(期限の断りは 400・合わなければ 409)・POST /leases/<名> = lease の操作(形の悪い操作は 400)・
   ほかは 404。"
  (val parts (.split (.strip path "/") "/"))
  (match #(method (get parts 0) (len parts))
    #("GET" "board" 1)
      (BoardReply :status 200 :written None
                  :answer (dfor #(k v) (sorted (.items rows)) :if (.startswith k (.get query "prefix" "")) k v))
    #("PUT" "board" n) :if (> n 1)
      (do (val board-key (.join "/" (cut parts 1 None)))
          (<- refusal (board-ttl-refusal (.get body "ttlSeconds")))
          (val allowed (board-allows (.get rows board-key) (in board-key rows) (in "expect" body) (.get body "expect")))
          (match #((is refusal None) allowed)
            #(False _) (BoardReply :status 400 :answer {"ok" False "error" refusal} :written None)
            #(True False) (BoardReply :status 409 :answer {"ok" False "current" (.get rows board-key)} :written None)
            #(True True) (BoardReply :status 200 :answer {"ok" True} :written #(board-key (get body "value")))))
    #("POST" "leases" 2)
      (do (val lease-key (semaphore-key (url-unquote (get parts 1))))
          (val current (.get rows lease-key))
          (try
            (setv #(row verdict) (lease-op current (get body "op") (get body "token") (int (.get body "permits" 1))
                                           (int (.get body "ttlMs" 0)) now-ms))
            ;; 返事の本文は coordinator と同じく LeaseAnswer の wire の形(lease-write の dump と同じ)。
            (<- answer dict (dump verdict))
            (BoardReply :status 200 :answer answer
                        :written (if (or (is row current) (is row None)) None #(lease-key row)))
            (except [refused BodyInvalid]
              (BoardReply :status 400 :answer {"ok" False "error" (str refused)} :written None))))
    _ (BoardReply :status 404 :answer {"ok" False "error" (.format "知らない要求: {} {}" method path)} :written None)))


(defhandler board-fake [#^ dict store]
  ;; 引数に残す理由: 盤は検が中を見る・書き換える dict そのもの(組み立てが 1 つ作って渡す — Ask で運ぶ設定ではない)。
  ;; 本文は JSON に通した写しで受け、答えも JSON の本文で返す(本物と同じく、書いた・読んだ値が呼び手の object と切り離される)。
  (HttpRequest [method url params body]
    :when (.startswith url BOARD-URL)
    (<- now int (now-epoch-ms))
    (val wire-body (if (is body None) None (json.loads (json.dumps body))))
    (<- reply BoardReply (board-reply (dict store) method (. (urlsplit url) path) (or params {}) wire-body now))
    (when (is-not reply.written None)
      (.update store {(get reply.written 0) (get reply.written 1)}))
    (val text (json.dumps reply.answer))
    (resume (HttpResponse reply.status {} (.encode text "utf-8") text url 0.0))))


(deff board-handlers [#^ dict store]  ; defk にできない: 組み立て(Program を走らせる前)が handler の list を作る準備
  {:pre [(: store dict)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "盤 store の上の共有の保存の handler の list(外側が先): fake の盤と、本番と同じ shared-http(宛先 = fake の盤だけ)。"
  [(board-fake store) (shared-http (RouteCell (CoordinatorRoute :urls #(BOARD-URL) :active 0 :switched-at-ms 0)) BOARD-ROUTE)])
