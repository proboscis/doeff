;;; task の子 process が終わる前に、結果を coordinator の POST /tasks/<id>/result へ直に届ける口(#1387 — foundation/report_client.hy から
;;; #2427 で移した)。要求の形(task-result-request)と届ける相手の読み(task-id-of-job)は本番の子 process(job_entry の task の入口)と
;;; sim の宿が同じ定義を使う。送りは宛先の部品(coordinator_route)の上の汎用の HttpRequest — 本物の答え手は入口が積む。
(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects [slog])
(import doeff_core_effects.http_effects [HttpResponse])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteOptions RoutedReply route-of routed-request])


(deff task-result-request [#^ str task #^ str worker #^ str instance #^ str result]  ; defk にできない: 本番の子 process の入口(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: task str) (: worker str) (: instance str) (: result str)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "http"}}
  "終わった task の結果 1 つ → #(method path query 本文)。task = coordinator の振った task の id・worker / instance = 送り手の子 process の
   担い手の名と世代の名・result = 詰めた結果(program_codec.encode-outcome)。本番の子 process(job_entry.run-task)と sim の宿が同じ要求を
   coordinator へ送るため(定義点はここ 1 つ — 受けるのは cluster_policy.absorb-task-result)。"
  #("POST" (.format "/tasks/{}/result" (url-quote task :safe "")) {}
    {"worker" worker "instance" instance "result" result "format" PROTOCOL-FORMAT}))


(deff task-id-of-job [#^ str job]  ; defk にできない: 本番の子 process の入口(Program の外の I/O の道具)と sim の宿が同じ判断を使う
  {:pre [(: job str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "子 process の job の名(worker が渡す DOEFF_WORKER_JOB — task は task/<id>)から、結果を届ける task の id を読むため。task/<id> の形で
   なければ None(worker の外で task の入口だけを動かした時 — 届ける相手が無いので、結果は file の路だけになる)。"
  (if (.startswith job "task/") (cut job 5 None) None))


(defk route-failure [job why]
  {:pre [(: job str) (: why str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "結果を直に届けられなかった理由の 1 行を出して偽を返すため(結果は file に在り、worker の heartbeat が運ぶ)。"
  (<- (slog (.format "task: {} の結果を coordinator に届けられない: {} — worker の heartbeat が運ぶ" job why)))
  False)


(defk delivered-task-result [coordinator-url job worker instance result options]
  {:pre [(: coordinator-url str) (: job str) (: worker str) (: instance str) (: result str) (: options RouteOptions)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "task の子 process が終わる前に、結果を coordinator へ直に届けるため(#1387)。答え = 受けられたか(置いた worker からの結果として受けた・
   既に終わっていた)。届かない・断られた・届ける相手の分からない(job の名が task/<id> でない)時は理由の 1 行を出して偽 — 結果は file に
   在り、worker の heartbeat が運ぶ(前からの路)。送りは宛先の部品の上の HttpRequest(#2427 — 前は httpx の client を持つ口)で、送り直しは
   接続の段の一巡し直し 1 回だけにして子の終わりを長く止めない: 送り直しの間に連絡の途絶が fence を越えると worker がこの process を
   止め、file の結果も終わった task の結果として運ばれなくなる。coordinator-url・job・worker・instance = 子 process の文脈
   (job_context.RunContext の欄)・options = 送り方(入口が作る — 一巡し直しは 1 回)。"
  (val task (task-id-of-job job))
  (when (is task None)
    (<- (slog (.format "task: job の名 {!r} が task/<id> の形でないので、結果を coordinator へ直には届けない(file の路だけ)" job)))
    (return False))
  (val request (task-result-request task worker instance result))
  (<- now int (now-epoch-ms))
  ;; 宛先の無い・読めない文脈は、届かなかったとして file の路に任せる。
  (var route None)
  (try
    (<- made CoordinatorRoute (route-of coordinator-url now))
    (:= route made)
    (except [error ValueError]
      (:= route error)))
  (when (isinstance route ValueError)
    (<- unrouted bool (route-failure job (.format "ValueError: {}" route)))
    (return unrouted))
  (<- reply RoutedReply (routed-request route (get request 0) (get request 1) options None (get request 3)))
  (val answer reply.answer)
  (cond
    (and (isinstance answer HttpResponse) (< answer.status 300)) True
    (isinstance answer HttpResponse)
      (do (<- turned-down bool (route-failure job (.format "{}: {}" answer.status (cut answer.text 0 300))))
          turned-down)
    True
      (do (<- failed bool (route-failure job (.format "{}: {}" answer.url answer.detail)))
          failed)))
