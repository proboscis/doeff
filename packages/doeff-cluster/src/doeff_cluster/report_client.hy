;;; service の process が coordinator へ自分の様子(readiness・計器)を送る口。HTTP の client はこの module の中に閉じる。
;;;
;;; 送る本文にはいつも、送り手の process の世代を載せる: worker の名・版・pid と、worker が process を起こした時に渡した
;;; 世代(instance = 起こすたびに新しく振る名・attempt = 試行の番号・specHash = 起こした spec の指紋・placement = 割り当ての世代)。
;;; coordinator はこれが「今の宣言で、担い手が running と報告している process」と一致する報告だけを数える(resource_policy.report-matches)。
;;; 送れなくても業務を止めない(報告は観測。届かなければ coordinator の側で期限を過ぎて数えなくなる)。
;;;
;;; 要求の形(report-request)は、この client と手元の sim-cluster の偽の宿(local.hy)が同じ関数で作る(本文を写さない)。
(require doeff-hy.macros [deff])
(import os)
(import sys)
(import urllib.parse [quote :as url-quote])
(import .coordinator_http [CoordinatorEndpoint REPLY-SECONDS])


(deff report-request [#^ str service #^ dict sender #^ str kind #^ dict payload]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: service str) (: sender dict) (: kind str) (: payload dict)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "service の報告 1 つ → #(method path query 本文)。kind = readiness | metrics・sender = 送り手の process の世代({worker pid revision} と
   RunContext.identity)。本番の ServiceReportClient と sim の宿が同じ要求を coordinator へ送るため(定義点はここ 1 つ)。"
  #("POST" (.format "/resources/Service/{}/{}" (url-quote service :safe "") kind) {} (| sender payload)))


(defclass ServiceReportClient []
  "service = 宣言の名(worker が子 process へ渡す DOEFF_WORKER_JOB)。identity = process の世代(job_entry.RunContext.identity)。"
  (defn __init__ [self #^ str url #^ str service #^ str worker #^ str revision [identity None] [timeout REPLY-SECONDS]
                  [transport None]]
    (setv self.service service self.worker worker self.revision revision self.identity (or identity {})
          self.failures {} self.endpoint (CoordinatorEndpoint url timeout 1 :transport transport)))

  (defn #^ dict sender [self]
    (| {"worker" self.worker "pid" (os.getpid) "revision" self.revision} self.identity))

  (defn send [self #^ str kind #^ dict payload]
    "kind = readiness | metrics → POST /resources/Service/<名>/<kind>。"
    (try
      (setv #(method path _ body) (report-request self.service (.sender self) kind payload))
      (setv response (.request self.endpoint method path :json body))
      (.raise-for-status response)
      (setv (get self.failures kind) 0)
      (except [error Exception]
        (setv n (+ (.get self.failures kind 0) 1))
        (setv (get self.failures kind) n)
        ;; 途絶のたびに log を埋めないよう、続けて失敗した 1 回目と 10 回ごとだけ印字する。
        (when (= (% n 10) 1)
          (print (.format "{}: {} を送れなかった({} 回目): {}: {}" kind self.service n (. (type error) __name__) error)
                 :file sys.stderr :flush True))))))


(defn #^ ServiceReportClient report-client [ctx]
  "worker の子 process の文脈(job_entry.RunContext)から、世代つきの報告の口を作る。"
  (ServiceReportClient ctx.coordinator-url ctx.job ctx.worker ctx.revision :identity (.identity ctx)))
