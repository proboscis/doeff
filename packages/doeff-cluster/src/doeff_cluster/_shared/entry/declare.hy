;;; 系(defsystem の関数)の宣言を coordinator へ書く(ADR-DOE-CLUSTER-001)— apply-declaration と、その下請けの要求 2 つ。
;;;
;;; 宣言の入口(命令)は利用側の宣言の道具が持つ(#3030 — 以前ここに在った命令 `hy -m doeff_cluster.shared.entry.declare` は、
;;; 系に渡す土台を引数 --foundation で選んでいた。土台は利用側の「土台の型 → 本番の土台」の表で選び、引数で選ばないので、汎用の
;;; 命令はこの package に置かない)。利用側の道具は次の部品を並べる:
;;;   - 系の関数に土台を渡して System の値を作り、宣言してよいかを doeff_cluster._shared.core.declaring の declaring-refusal で検める
;;;     (系の関数の module の在る git の checkout が汚れておらず push 済みで HEAD が宣言の版と同じ commit・土台の :needs が各 job の
;;;     :needs の一部 — checkout の読みは effect で、答えるのは doeff_cluster._shared.protocol.checkout_reads の checkout-reads と
;;;     汎用の子 process の handler)。
;;;   - 宣言の行と詰めた Program を doeff_cluster._shared.entry.service_build の system-declaration で組む。
;;;   - ここの apply-declaration で書く: 先に系の組の同時数の上限を資源 GroupLimit へ書き(2026-10-12 — 無ければ作り・spec が違えば
;;;     読んだ版つきで書き直す)、詰めた Program を PUT /programs/<sha> で置き(改訂 1 の F)、次に Service ごとに資源の口で
;;;     書く — 無ければ POST /resources/Service で作る(所有者 = 送り手)。在れば GET で読んだ resourceVersion を付けて PUT する
;;;     (読んでから書くまでに誰かが spec を書いていれば 409 で止まる — 他の作業係の変更を消さない。版は状態の欄の変化でも進むので、
;;;     409 の後に読み直して spec が同じ — 変わったのは状態だけ — なら今の版で書き直す)。所有者はいまの値を保ち、台数は行の値
;;;     (job の :replicas)を書く — 手で台数だけを替えた Service も、宣言し直すと job の値へ戻る(#3487)。一覧に無い Service には触らない。
;;;   - restart-service: 宣言を替えずに 1 つの Service だけを起こし直す(2026-10-13・card ki-0019be4b9939 — 手で replicas 1→0→1 にする「止めてから
;;;     起こす」の替わり)。spec の環境変数 RESTART_MARK を 1 つ進めて書き、handoff の job は新を旧と並べて起こしてから旧を止める。
;;;
;;; 置き場(#2346): apply はここ(shared/entry・役 main)・要求の本文の形は doeff_cluster._shared.protocol.declaration_requests・
;;; 宣言してよいかの判断は doeff_cluster._shared.core.declaring。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import collections.abc [Mapping])
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects [slog])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_cluster._shared.protocol.declaration_requests [ServiceRead CONFLICT-REREADS service-read reread-after-conflict needed-programs body-of
                                                            group-limit-write RestartPlan restart-read])
(import doeff_cluster._shared.intent.cluster_control [ServiceRestarted RestartRefused])
(import doeff_cluster._shared.intent.service_model [Declaration])
(import doeff_cluster._shared.intent.detached_model [GroupLimit])


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


(defk service-current [url actor]
  {:pre [(: url str) (: actor str)] :post [(: % (| dict None))]
   :tags {:context "doeff-cluster" :role "main" :reads "json"}}
  "資源の口 url の Service 1 つの今の資源(GET の本文 — 無ければ None)を読むため(宣言の前の読みと、409 の後の読み直しが同じ読みを通る)。"
  (<- current HttpResponse (declare-request "GET" url actor None))
  (when (= current.status 404)
    (return None))
  (.raise-for-status current)
  (json.loads current.text))


(defk service-read-at [base actor name row]
  {:pre [(: base str) (: actor str) (: name str) (: row (get Mapping #(str object)))] :post [(: % ServiceRead)]
   :tags {:context "doeff-cluster" :role "main"}}
  "Service の行 1 つの今の資源を資源の口で読み、送る書きを決めるため(差分の宣言の判断は declaration_requests.service-read)。"
  (val url (+ base "/resources/Service/" (url-quote name :safe "")))
  (<- current (| dict None) (service-current url actor))
  (<- read ServiceRead (service-read name row url current))
  read)


(defk service-written [base actor read]
  {:pre [(: base str) (: actor str) (: read ServiceRead)] :post [(: % HttpResponse)]
   :tags {:context "doeff-cluster" :role "main"}}
  "読んだ Service 1 つへ書きを送るため: 無ければ POST /resources/Service で作り(所有者 = 送り手)、在れば読んだ resourceVersion を付けて
   PUT する(読んでから書くまでに誰かが書いていれば 409 で止まる — 他の作業係の変更を消さない)。答え = 書きの返事。"
  (<- body dict (body-of read))
  (when (is read.version None)
    (<- created HttpResponse (declare-request "POST" (+ base "/resources/Service") actor body))
    (return created))
  (<- updated HttpResponse (declare-request "PUT" read.target actor body))
  updated)


(defk service-written-rereading [base actor read]
  {:pre [(: base str) (: actor str) (: read ServiceRead)] :post [(: % HttpResponse)]
   :tags {:context "doeff-cluster" :role "main"}}
  "読んだ Service 1 つへ書きを送り、版つきの書き直しが 409 を受けたら読み直して書き直すため(coordinator は状態の欄が変わっても版を
   進めるので、落ち続ける job は読んでから書くまでに版が進む): 読み直した spec が読んだ時と同じなら今の版で同じ本文を送り直す
   (declaration_requests.reread-after-conflict・CONFLICT-REREADS 回まで)。spec が変わっていれば最後の 409 を返す(止まる)。"
  (var sent read)
  (var rereads 0)
  (<- first HttpResponse (service-written base actor sent))
  (var written first)
  (while (and (= written.status 409) (is-not sent.version None) (< rereads CONFLICT-REREADS))
    (:= rereads (+ rereads 1))
    (<- current (| dict None) (service-current sent.target actor))
    (<- again (| ServiceRead None) (reread-after-conflict sent current))
    (when (is again None)
      (break))
    (<- (slog (.format "{}: 409 の後に読み直した — 変わったのは状態の欄だけなので版 {} で書き直す" sent.name again.version)))
    (:= sent again)
    (<- retried HttpResponse (service-written base actor sent))
    (:= written retried))
  written)


(defk group-limit-written [base actor limit]
  {:pre [(: base str) (: actor str) (: limit GroupLimit)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "main"}}
  "系の宣言の組の上限 1 つを資源 GroupLimit へ差分だけ書くため(無ければ作る・spec が同じなら書かない・違えば読んだ版つきで書き直す —
   本文の判断は declaration_requests.group-limit-write)。返事を 1 行出す(slog)。答え = 通ったか。"
  (val url (+ base "/resources/GroupLimit/" (url-quote limit.group :safe "")))
  (<- current (| dict None) (service-current url actor))
  (<- body (| dict None) (group-limit-write limit current))
  (when (is body None)
    (<- (slog (.format "GroupLimit {}: unchanged" limit.group)))
    (return True))
  (<- written HttpResponse (if (is current None)
                               (declare-request "POST" (+ base "/resources/GroupLimit") actor body)
                               (declare-request "PUT" url actor body)))
  (<- (slog (.format "GroupLimit {}: {} {}" limit.group written.status (cut written.text 0 300))))
  (< written.status 300))


(defk restart-service [url actor name]
  {:pre [(: url str) (: actor str) (: name str)] :post [(: % (| ServiceRestarted RestartRefused))]
   :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "Service name を版を替えずに重ねて起こし直すため(RestartService の配備の答え): 資源の口で今の資源を読み、環境変数 RESTART_MARK を 1 つ進めた
   spec を読んだ版つきで書く(書く spec の判断は declaration_requests.restart-read — handoff の job は新を旧と並べて起こす)。409 を受けたら
   宣言の書きと同じく、変わったのが状態の欄だけなら読み直して書き直す。返事を 1 行出す(slog)。書かない時は RestartRefused(理由つき)を返し、
   書いて通らなければ例外で止まる(起こし直しを頼んだ側へ、書けなかったと分からせる)。"
  (val target (+ (.rstrip url "/") "/resources/Service/" (url-quote name :safe "")))
  (<- current (| dict None) (service-current target actor))
  (<- plan (| RestartPlan RestartRefused) (restart-read name target current))
  (when (isinstance plan RestartRefused)
    (<- (slog (.format "{}: 起こし直しを書かない — {}" name plan.reason)))
    (return plan))
  (<- written HttpResponse (service-written-rereading (.rstrip url "/") actor plan.read))
  (<- (slog (.format "{}: 起こし直し {} {}" name written.status (cut written.text 0 300))))
  (when (>= written.status 300)
    (raise (RuntimeError (.format "{} の起こし直しを書けない — {} {}" name written.status (cut written.text 0 300)))))
  (ServiceRestarted :name name :mark plan.mark))


(defk apply-declaration [url declaration actor]
  {:pre [(: url str) (: declaration Declaration) (: actor str)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "宣言の行を差分だけ資源の口で書くため: 先に系の組の上限(資源 GroupLimit)を書き(新しい版の job と送り手が名乗る組を先に在らせる —
   coordinator は宣言に無い組を名乗る task を断る)、Service を全部読み、書く Service(無い・spec が変わった)が名指す Program だけを置いて
   から、書く Service だけを書く(spec の変わらない Service には書きを送らない — declaration_requests の頭注)。HTTP は汎用の effect
   (HttpRequest)で送り、要求ごとの返事を 1 行出す(slog)。答え = 全部が通ったか(組の上限か Program の置きが 1 つでも落ちれば行は
   書かずに偽 — 入口が 1 で終わる)。系に無くなった組の資源は消さない(一覧に無い Service に触らないのと同じ)。"
  (val base (.rstrip url "/"))
  ;; 組の上限の書きが 1 つ断られたら(例 別の系が宣言した組で 409)、残りの組も行も書かずに偽 — sim の宣言の書きと同じ所で止まる。
  (for [limit declaration.limits]
    (<- ok bool (group-limit-written base actor limit))
    (when (not ok)
      (return False)))
  ;; job の無い宣言(組の上限だけ)は Program を置かないので、版の識別を読まない(以前は rows[0] を読んで IndexError — 盲検 A の観測 5)。
  (val versions (if declaration.rows (get (get (get declaration.rows 0) "run") "versions") {}))
  (var reads #())
  (for [row declaration.rows]
    (<- read ServiceRead (service-read-at base actor (get row "name") row))
    (:= reads (+ reads #(read))))
  (<- needed (get tuple #(str ...)) (needed-programs declaration reads))
  (var programs-placed True)
  (for [sha needed]
    (<- placed HttpResponse (declare-request "PUT" (+ base "/programs/" sha) actor {"blob" (get declaration.programs sha) "versions" versions}))
    (<- (slog (.format "program {}: {}" (cut sha 0 12) placed.status)))
    (when (>= placed.status 300)
      (:= programs-placed False)))
  (when (not programs-placed)
    (return False))
  (var rows-written True)
  (for [read reads]
    (if (is read.body None)
        (<- (slog (.format "{}: unchanged" read.name)))
        (do (<- written HttpResponse (service-written-rereading base actor read))
            (<- (slog (.format "{}: {} {}" read.name written.status (cut written.text 0 300))))
            (when (>= written.status 300)
              (:= rows-written False)))))
  rows-written)
