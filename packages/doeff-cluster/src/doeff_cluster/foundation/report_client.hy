;;; service の process が coordinator へ自分の様子(readiness・計器)を送る送り手を、この process の値(pid)で作る(service-report-of)。
;;; 送り方と要求の形は shared/protocol/service_report.hy(宛先の部品の上の HttpRequest — #2337 の 4a)。
;;; task の子 process が終わる前に結果を coordinator へ直に届ける口は shared/protocol/task_result.hy(#2427 で移した)。
(require doeff-hy.macros [defk])
(import os)
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.shared.protocol.service_report [ServiceReport])


(defk service-report-of [ctx]
  {:pre [(: ctx RunContext)] :post [(: % ServiceReport)] :tags {:context "doeff-cluster" :role "foundation"}}
  "worker の子 process の文脈(job_entry.RunContext)とこの process の pid から、世代つきの報告の送り手を作るため(送るのは
   shared/protocol/service_report.hy の sent-report — #2337 の 4a で httpx を直に持つ ServiceReportClient を替えた)。"
  (ServiceReport ctx.job (| {"worker" ctx.worker "pid" (os.getpid) "revision" ctx.revision} (.identity ctx))))
