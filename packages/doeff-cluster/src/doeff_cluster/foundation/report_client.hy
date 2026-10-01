;;; service の process が coordinator へ自分の様子(readiness・計器)を送る送り手を、この process の値(pid)で作る(service-report-of)。
;;; 送り方と要求の形は shared/protocol/service_report.hy(宛先の部品の上の HttpRequest — #2337 の 4a)。
;;;
;;; task の子 process が終わる前に結果を coordinator へ直に届ける口(task-result-request・deliver-task-result — #1387)もここに置く:
;;; 子 process から coordinator への報告という点で service の報告と同じ(届かなければ worker の heartbeat が file の結果を運ぶ)。
(require doeff-hy.macros [defk deff])
(import os)
(import sys)
(import urllib.parse [quote :as url-quote])
(import httpx)
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.foundation.coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.shared.protocol.service_report [ServiceReport])


(defk service-report-of [ctx]
  {:pre [(: ctx RunContext)] :post [(: % ServiceReport)] :tags {:context "doeff-cluster" :role "foundation"}}
  "worker の子 process の文脈(job_entry.RunContext)とこの process の pid から、世代つきの報告の送り手を作るため(送るのは
   shared/protocol/service_report.hy の sent-report — #2337 の 4a で httpx を直に持つ ServiceReportClient を替えた)。"
  (ServiceReport ctx.job (| {"worker" ctx.worker "pid" (os.getpid) "revision" ctx.revision} (.identity ctx))))


(deff task-result-request [#^ str task #^ str worker #^ str instance #^ str result]  ; defk にできない: 本番の子 process の入口(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: task str) (: worker str) (: instance str) (: result str)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "終わった task の結果 1 つ → #(method path query 本文)。task = coordinator の振った task の id・worker / instance = 送り手の子 process の
   担い手の名と世代の名・result = 詰めた結果(remote_model.encode-outcome)。本番の子 process(job_entry.run-task)と sim の宿が同じ要求を
   coordinator へ送るため(定義点はここ 1 つ — 受けるのは cluster_policy.absorb-task-result)。"
  #("POST" (.format "/tasks/{}/result" (url-quote task :safe "")) {}
    {"worker" worker "instance" instance "result" result "format" PROTOCOL-FORMAT}))


(deff task-id-of-job [#^ str job]  ; defk にできない: 本番の子 process の入口(Program の外の I/O の道具)と sim の宿が同じ判断を使う
  {:pre [(: job str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "子 process の job の名(worker が渡す DOEFF_WORKER_JOB — task は task/<id>)から、結果を届ける task の id を読むため。task/<id> の形で
   なければ None(worker の外で task の入口だけを動かした時 — 届ける相手が無いので、結果は file の路だけになる)。"
  (if (.startswith job "task/") (cut job 5 None) None))


(deff deliver-task-result [#^ RunContext ctx #^ str result [transport None]]  ; defk にできない: 子 process の入口(Program の外)が coordinator へ送る
  {:pre [(: ctx RunContext) (: result str) (: transport (| httpx.BaseTransport None))] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "entry"}}
  "task の子 process が終わる前に、結果を coordinator へ直に届けるため(#1387)。答え = 受けられたか(置いた worker からの結果として受けた・
   既に終わっていた)。届かない・断られた・届ける相手の分からない(job の名が task/<id> でない)時は理由の 1 行を出して偽 — 結果は file に
   在り、worker の heartbeat が運ぶ(前からの路)。送り直しは接続の段だけ(CoordinatorEndpoint)にして子の終わりを長く止めない: 送り直しの
   間に連絡の途絶が fence を越えると worker がこの process を止め、file の結果も終わった task の結果として運ばれなくなる。
   transport = httpx の transport(検が coordinator の模擬を後ろに置く・既定 None = 網)。"
  (setv task (task-id-of-job ctx.job))
  (when (is task None)
    (print (.format "task: job の名 {!r} が task/<id> の形でないので、結果を coordinator へ直には届けない(file の路だけ)" ctx.job)
           :file sys.stderr :flush True)
    (return False))
  (setv #(method path _ body) (task-result-request task ctx.worker ctx.instance result))
  (try
    (setv response (.request (CoordinatorEndpoint ctx.coordinator-url REPLY-SECONDS 1 :transport transport) method path :json body))
    (when (< response.status-code 300)
      (return True))
    (print (.format "task: {} の結果を coordinator に届けられない({}): {} — worker の heartbeat が運ぶ"
                    ctx.job response.status-code (cut response.text 0 300))
           :file sys.stderr :flush True)
    False
    ;; 宛先の無い・読めない文脈(ValueError・InvalidURL)と通信の失敗(HTTPError)は、届かなかったとして file の路に任せる。
    (except [error #(httpx.HTTPError httpx.InvalidURL ValueError)]
      (print (.format "task: {} の結果を coordinator に届けられない: {}: {} — worker の heartbeat が運ぶ"
                      ctx.job (. (type error) __name__) error)
             :file sys.stderr :flush True)
      False)))
