;;; service の process が coordinator へ自分の様子(readiness・計器)を送る口 — 報告 1 つを、宛先の部品(coordinator_route)の上の
;;; 汎用の HttpRequest で送る(#2337 の 4a — httpx を直に持っていた foundation/report_client.hy の ServiceReportClient を替えた)。
;;;
;;; 送る本文にはいつも、送り手の process の世代を載せる: worker の名・版・pid と、worker が process を起こした時に渡した
;;; 世代(instance = 起こすたびに新しく振る名・attempt = 試行の番号・specHash = 起こした spec の指紋・placement = 割り当ての世代)。
;;; coordinator はこれが「今の宣言で、担い手が running と報告している process」と一致する報告だけを数える(resource_policy.report-matches)。
;;; 世代は組み立ての根が process の値から作って渡す(ServiceReport — この module は pid も環境も読まない)。
;;; 送れなくても業務を止めない(報告は観測。届かなければ coordinator の側で期限を過ぎて数えなくなる): 断り・届かないは log の効果
;;; (SlogEffect — 出し先は入口の slog-handler)に、続けて失敗した 1 回目と 10 回ごとだけ出す(途絶のたびに log を埋めない)。
;;; 書きなので送り直しは接続の段だけ(routed-request — 返事を読む前に切れた報告は届いたか分からない)。
;;;
;;; 要求の形(report-request)は、この口と手元の sim-cluster の偽の宿(local.hy)が同じ関数で作る(本文を写さない)。
(require doeff-hy.macros [defk <- val])
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects [slog])
(import doeff_core_effects.http_effects [HttpResponse HttpFailed])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request])
(import doeff_cluster.job_context [RunContext])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})


(defclass ServiceReport []
  "報告の送り手: service = 宣言の名(worker が子 process へ渡す DOEFF_WORKER_JOB)・sender = 送り手の process の世代({worker pid
   revision} と RunContext.identity — 組み立ての根が作る)・failures = 報告の種類ごとに続けて送れなかった数(handler の節が書き換える
   — log を間引くため)。"
  (defn #^ None __init__ [self #^ str service #^ dict sender]
    (setv self.service service self.sender sender self.failures {})))


(defk report-request [service sender kind payload]
  {:pre [(: service str) (: sender dict) (: kind str) (: payload dict)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "service の報告 1 つ → #(method path query 本文)。kind = readiness | metrics・sender = 送り手の process の世代({worker pid revision} と
   RunContext.identity)。本番の口(sent-report)と sim の宿が同じ要求を coordinator へ送るため(定義点はここ 1 つ)。"
  #("POST" (.format "/resources/Service/{}/{}" (url-quote service :safe "") kind) {} (| sender payload)))


(defk sent-report [cell options report kind payload]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: report ServiceReport) (: kind str) (: payload dict)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "kind(readiness | metrics)の報告 1 つを POST /resources/Service/<名>/<kind> へ送るため。答えはいつも None — 断り(400 以上)と
   届かない(HttpFailed)は数えて、続けて失敗した 1 回目と 10 回ごとだけ log の効果に出す。宛先の状態は cell に書き戻す。"
  (<- request tuple (report-request report.service report.sender kind payload))
  (setv #(method path _ body) request)
  (<- reply RoutedReply (routed-request cell.route method path options None body))
  (setv cell.route reply.route)
  (val failure (match reply.answer
                 (HttpResponse :status status) :if (< status 400) None
                 (HttpResponse :status status) (.format "coordinator が断った({}): {}" status (cut reply.answer.text 0 300))
                 (HttpFailed :detail detail) (.format "coordinator に届かない({}): {}" reply.answer.url detail)
                 _ "coordinator の宛先が無い"))
  (val count (if (is failure None) 0 (+ (.get report.failures kind 0) 1)))
  (setv (get report.failures kind) count)
  (when (= (% count 10) 1)
    (<- (slog (.format "{}: {} を送れなかった({} 回目): {}" kind report.service count failure))))
  None)


(defk service-report-of [ctx pid]
  {:pre [(: ctx RunContext) (: pid int)] :post [(: % ServiceReport)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "worker の子 process の文脈(job_context.RunContext)と process の pid から、世代つきの報告の送り手を作るため(送るのは上の
   sent-report)。pid は組み立てる側(cluster_foundation)が渡す — 前は foundation/report_client.hy が os.getpid を読んでいたが、層
   foundation は protocol の ServiceReport を読めないので、ここに寄せた(#2566)。"
  (ServiceReport ctx.job (| {"worker" ctx.worker "pid" pid "revision" ctx.revision} (.identity ctx))))
