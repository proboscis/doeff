;;; 終わった process の lease の返し(ReleaseLeases)の言い換え lease-release(handlers.hy の前の lease の返しと宛先を
;;; 置き換えた・#2427)— coordinator の盤と lease の口への要求を、宛先の部品(shared/protocol/coordinator_route)の上の汎用の HttpRequest で
;;; 出す。本物の I/O は入口が積む http-production-handler。
;;;
;;; 振る舞いは前の release-leases と同じ: token の頭は子が名乗った担い手と同じ定義(lease_rules.lease-holder と holder-tokens-prefix —
;;; <job>/<世代の名>/)。外すのは coordinator(POST /leases/<名> の drop — 2026-09-25)。drop の口を持たない旧い coordinator(404)には、
;;; 盤の行の compare-and-set で外す(以前の形 — 競合は 3 回まで読み直す)。届かない・競合が続く時はあきらめる(期限で切れる)。
;;;
;;; 返しは拍の外の task が行い、答え手は待ちの列に積んですぐ答える。1 回の返しは盤の semaphore の行を全部読む(本番の盤で 3 万行・
;;; 2.7 MB・1 回 0.4〜1.4 秒)ので、以前のように回収 1 件ごとに拍の中で返すと、版の替わる入れ替えで 10〜19 件の回収が 1 つの拍に並び、
;;; 区間 ActionsToPublishStatus が 10〜36 秒になって、入れ替えの新の起こしと状態の報告を待たせた(2026-11-12 の実例)。task は同時に
;;; 1 本で、走っている間に積まれた物は、同じ task が続けて 1 回の読みで返す(読みの数は回収の数でなく task の周の数)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects [slog])
(import doeff_core_effects [HttpResponse])
(import doeff_core_effects [Spawn])
(import doeff_cluster._shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster._shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import doeff_cluster._shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions RoutedReply routed-request])
(import doeff_cluster._worker.intent.worker_model [ReleaseLeases])

;; 返しの task の組 1 つが思わぬ例外で終わった時の行の名(名 + 欄 count = あきらめた process の数・detail = 例外)。
(val RELEASE-FAILED-LOG "worker: lease の返しの task の失敗")


(defk board-rows [route options prefix]
  {:pre [(: route CoordinatorRoute) (: options RouteOptions) (: prefix str)] :post [(: % tuple)]
   :tags {:context "worker" :role "protocol" :spells "http" :reads "json"}}
  "盤の行のうち鍵が prefix で始まる物を読むため。答え = #(行の dict か読めない理由の str 次の宛先の状態)。"
  (<- reply RoutedReply (routed-request route "GET" "/board" options {"prefix" prefix} None))
  (val answer reply.answer)
  (val rows (if (and (isinstance answer HttpResponse) (< answer.status 300))
                (try (json.loads answer.text) (except [ValueError] "盤の返事が JSON でない"))
                (if (isinstance answer HttpResponse)
                    (.format "盤を読めない({}): {}" answer.status (cut answer.text 0 200))
                    (.format "盤を読めない: {}" (if (is answer None) "宛先が無い" answer.detail)))))
  #(rows reply.route))


(defk released-by-board [route options key row prefix instance]
  {:pre [(: route CoordinatorRoute) (: options RouteOptions) (: key str) (: row dict) (: prefix str) (: instance str)]
   :post [(: % CoordinatorRoute)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "旧い coordinator(/leases の口が無い)へ: 盤の行の compare-and-set で担い手を外すため(競合は読み直して 3 回まで)。答え = 次の宛先の状態。"
  (var current route)
  (var seen row)
  (for [attempt (range 3)]
    (val updated (drop-holders seen prefix))
    (when (is updated None) (break))
    (<- put RoutedReply (routed-request current "PUT" (+ "/board/" key) options None {"value" updated "expect" seen}))
    (:= current put.route)
    (val status (if (isinstance put.answer HttpResponse) put.answer.status None))
    (when (and (is-not status None) (< status 300))
      (<- (slog (.format "worker: 終わった process {} の lease を返しました({})" instance key)))
      (break))
    (when (!= status 409) (break))
    ;; 競合(延長と重なった)は読み直す。
    (<- again tuple (board-rows current options key))
    (:= current (get again 1))
    (val rows (get again 0))
    (when (or (isinstance rows str) (is (.get rows key) None)) (break))
    (:= seen (get rows key)))
  current)


(defk released-leases [route options holders]
  {:pre [(: route CoordinatorRoute) (: options RouteOptions) (: holders tuple)] :post [(: % CoordinatorRoute)]
   :tags {:context "worker" :role "protocol" :spells "json"}}
  "終わった process の組 holders(#(job の名 世代の名) の並び)が持っていた lease を、盤の semaphore の行の 1 回の読みで返すため(頭の註)。
   答え = 次の宛先の状態。"
  (val prefixes (tuple (gfor #(job instance) holders #(instance (holder-tokens-prefix (lease-holder job instance))))))
  (<- read tuple (board-rows route options SEMAPHORE-PREFIX))
  (var current (get read 1))
  (val rows (get read 0))
  (if (isinstance rows str)
      (for [#(instance _) prefixes]
        (<- (slog (.format "worker: lease を返せなかった({}): {}" instance rows))))
      (for [#(key row) (.items rows) #(instance prefix) prefixes]
        (val dropped-holders (drop-holders row prefix))
        (when (is-not dropped-holders None)
          (val name (cut key (len SEMAPHORE-PREFIX) None))
          (<- dropped RoutedReply (routed-request current "POST" (+ "/leases/" (url-quote name :safe "")) options None
                                                  {"op" "drop" "token" prefix}))
          (:= current dropped.route)
          (val status (if (isinstance dropped.answer HttpResponse) dropped.answer.status None))
          (cond
            (and (is-not status None) (< status 300))
              (<- (slog (.format "worker: 終わった process {} の lease を返しました({})" instance key)))
            (= status 404)
              (do (<- after-board CoordinatorRoute (released-by-board current options key row prefix instance))
                  (:= current after-board))
            True
              (<- (slog (.format "worker: lease を返せなかった({}・{}): {}" instance key
                                 (if (is status None) (if (is dropped.answer None) "宛先が無い" dropped.answer.detail) status))))))))
  current)


(defclass ReleaseQueue []
  "lease の返しの答え手と拍の外の返しの task が分ける値: pending = まだ返していない #(job の名 世代の名) の並び(回収した順)・running = 返しの
   task が走っている。scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.pending #() self.running False)))


(defk releasing [queue cell options]
  {:pre [(: queue ReleaseQueue) (: cell RouteCell) (: options RouteOptions)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "拍の外の返しの task の本体: 待ちの列に在る物を全部取り、盤の 1 回の読みで返し、返している間に積まれた物が在れば続けて返すため(頭の註)。
   列が空になった時に running を下ろす(列の読みと下ろしの間に効果を挟まないので、積んだ物を取りこぼさない)。思わぬ例外はその組を
   あきらめて 1 行出し(lease は期限で切れる)、残りを続ける。"
  (var batch queue.pending)
  (while batch
    (setv queue.pending #())
    (try
      (<- route CoordinatorRoute (released-leases cell.route options batch))
      (setv cell.route route)
      (except [error Exception]
        (<- (slog RELEASE-FAILED-LOG :level "warning" :count (len batch) :detail (repr error)))))
    (:= batch queue.pending))
  (setv queue.running False)
  None)


(defhandler lease-release [#^ RouteCell cell #^ RouteOptions options]
  ;; 引数に残す理由: 宛先の状態(cell)は要求から要求へ持ち越す入れ物・送り方は worker の process の値(main が作る)。
  ;; 待ちの列(頭の註)。返しの task が 1 本だけ cell を使う。
  (session val queue (ReleaseQueue))
  (ReleaseLeases [job instance]
    (setv queue.pending (+ queue.pending #(#(job instance))))
    (when (not queue.running)
      (setv queue.running True)
      (<- (Spawn (releasing queue cell options) :daemon True)))
    (resume None)))
