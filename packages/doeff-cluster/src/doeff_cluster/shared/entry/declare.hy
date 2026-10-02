;;; 系(defsystem の関数)の宣言を coordinator へ書く(ADR-DOE-CLUSTER-001)— apply-declaration と、その下請けの要求 2 つ。
;;;
;;; 宣言の入口(命令)は利用側の宣言の道具が持つ(#3030 — 以前ここに在った命令 `hy -m doeff_cluster.shared.entry.declare` は、
;;; 系に渡す土台を引数 --foundation で選んでいた。土台は利用側の「土台の型 → 本番の土台」の表で選び、引数で選ばないので、汎用の
;;; 命令はこの package に置かない)。利用側の道具は次の部品を並べる:
;;;   - 系の関数に土台を渡して System の値を作り、宣言してよいかを doeff_cluster.shared.core.declaring の declaring-refusal で検める
;;;     (系の関数の module の在る git の checkout が汚れておらず push 済みで HEAD が宣言の版と同じ commit・土台の :needs が各 job の
;;;     :needs の一部 — checkout の読みは effect で、答えるのは doeff_cluster.shared.protocol.checkout_reads の checkout-reads と
;;;     汎用の子 process の handler)。
;;;   - 宣言の行と詰めた Program を doeff_cluster.shared.entry.service_build の system-declaration で組む。
;;;   - ここの apply-declaration で書く: 先に詰めた Program を PUT /programs/<sha> で置き(改訂 1 の F)、次に Service ごとに資源の口で
;;;     書く — 無ければ POST /resources/Service で作る(所有者 = 送り手)。在れば GET で読んだ resourceVersion を付けて PUT する
;;;     (読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。所有者と replicas はいまの値を保つ
;;;     (replicas は Rollout が持つ。replicas を渡した時だけ変える)。一覧に無い Service には触らない。
;;;
;;; 置き場(#2346): apply はここ(shared/entry・役 main)・要求の本文の形は doeff_cluster.shared.protocol.declaration_requests・
;;; 宣言してよいかの判断は doeff_cluster.shared.core.declaring。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import collections.abc [Mapping])
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.protocol.declaration_requests [spec-for-update create-body])
(import doeff_cluster.shared.intent.service_model [Declaration])


;; 要求 1 つの上限(秒)。書きは送り直さない(返事を読む前に切れた書きは相手に届いたか分からない)。
(val DECLARE-REPLY-SECONDS 30.0)


(defk declare-request [method url actor body]
  {:pre [(: method str) (: url str) (: actor str) (: body (| (get dict #(str object)) None))] :post [(: % HttpResponse)]
   :tags {:context "doeff-cluster" :role "main" :spells "http"}}
  "宣言の書きの要求 1 つを、送り手(header X-Actor — coordinator は出来事の記録に残す)を付けて送り直さずに送るため。4xx・5xx も返事として
   返し、届かなければ汎用の HTTP の答え手の例外のまま上げる。"
  (<- response HttpResponse (HttpRequest method url :headers {"X-Actor" actor} :body body
                                         :timeout-seconds DECLARE-REPLY-SECONDS :max-retries 0))
  response)


(defk service-written [base actor row replicas]
  {:pre [(: base str) (: actor str) (: row (get Mapping #(str object))) (: replicas (| int None))] :post [(: % HttpResponse)]
   :tags {:context "doeff-cluster" :role "main" :reads "json"}}
  "Service の行 1 つを資源の口へ書くため: 無ければ POST /resources/Service で作り(所有者 = 送り手)、在れば GET で読んだ resourceVersion を
   付けて PUT する(読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。答え = 書きの返事。"
  (val url (+ base "/resources/Service/" (url-quote (get row "name") :safe "")))
  (<- current HttpResponse (declare-request "GET" url actor None))
  (when (= current.status 404)
    (<- created HttpResponse (declare-request "POST" (+ base "/resources/Service") actor (create-body row replicas)))
    (return created))
  (.raise-for-status current)
  (val body (json.loads current.text))
  (<- spec dict (spec-for-update row (get body "spec") replicas))
  (<- updated HttpResponse (declare-request "PUT" url actor {"resourceVersion" (get body "resourceVersion") "spec" spec}))
  updated)


(defk apply-declaration [url declaration actor [replicas None]]
  {:pre [(: url str) (: declaration Declaration) (: actor str) (: replicas (| int None))] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "詰めた Program を置いてから、宣言の行を資源の口で書くため。HTTP は汎用の effect(HttpRequest)で送り、要求ごとの返事を 1 行出す(slog)。
   答え = 全部が通ったか(Program の置きが 1 つでも落ちれば行は書かずに偽 — 入口が 1 で終わる)。"
  (val base (.rstrip url "/"))
  (val versions (get (get (get declaration.rows 0) "run") "versions"))
  (var programs-placed True)
  (for [#(sha blob) (sorted (.items declaration.programs))]
    (<- placed HttpResponse (declare-request "PUT" (+ base "/programs/" sha) actor {"blob" blob "versions" versions}))
    (<- (slog (.format "program {}: {}" (cut sha 0 12) placed.status)))
    (when (>= placed.status 300)
      (:= programs-placed False)))
  (when (not programs-placed)
    (return False))
  (var rows-written True)
  (for [row declaration.rows]
    (val name (get row "name"))
    (<- written HttpResponse (service-written base actor row replicas))
    (<- (slog (.format "{}: {} {}" name written.status (cut written.text 0 300))))
    (when (>= written.status 300)
      (:= rows-written False)))
  rows-written)
