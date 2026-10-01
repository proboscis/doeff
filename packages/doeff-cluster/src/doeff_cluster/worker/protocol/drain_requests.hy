;;; worker の drain の問い(worker/intent/drain_model の CoordinatorCall・AskDrain)を coordinator への HTTP の要求に言い換えて送る
;;; 答え手 coordinator-calls(drain_main.hy から移した)。要求の形はここだけが知る(worker/core/drain_client.hy から移した — core が HTTP の形を組んでいた・
;;; DOEFF105・agora-redesign #2541)。並べるのは入口(drain_main.hy)。
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import json)
(import doeff_core_effects.http_effects [HttpResponse HttpFailed])
(import doeff_cluster.shared.protocol.coordinator_route [RouteCell RouteOptions RoutedReply routed-request])
(import doeff_cluster.worker.intent.drain_model [AskDrain CoordinatorCall])
(import doeff_cluster.worker.core.drain_client [worker-path])


(deff drain-request [#^ str name #^ float ttl-seconds #^ (| str None) own-boot]  ; defk にできない: 答え手 drain-requests と手元の sim-cluster の宿(local.hy)が同じ形を作る純粋な言い換え
  {:pre [(: name str) (: ttl-seconds float) (: own-boot (| str None))] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "drain の頼みを要求 #(method path query 本文) にするため。ttl-seconds = drain の期限・own-boot = 頼み手の worker の process の世代
   (在れば、同じ名の別の世代には drain を付けない — drain_policy.request-drain)。"
  #("POST" (+ (worker-path name) "/drain") {} (| {"ttlSeconds" ttl-seconds} (if own-boot {"boot" own-boot} {}))))


(defk call-answer [answer]
  {:pre [(: answer (| HttpResponse HttpFailed None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "要求の答えを drain の Program が読む形にするため: 返事 = {\"status\" 番号 \"body\" 本文の JSON の dict(dict でなければ空)}・
   届かない = {\"error\" 理由}。"
  (match answer
    (HttpResponse) (do (val parsed (try (json.loads answer.text) (except [ValueError] {})))
                       {"status" answer.status "body" (if (isinstance parsed dict) parsed {})})
    (HttpFailed) {"error" (.format "{}: {}" answer.url answer.detail)}
    _ {"error" "coordinator の宛先が無い"}))


(defhandler coordinator-calls [#^ RouteCell cell #^ RouteOptions options]
  ;; 引数に残す理由: 宛先の状態(cell)は要求から要求へ持ち越す入れ物・送り方は CLI の mode ごとの値(#2427 — 前は httpx の client を持つ口)。
  ;; 送り直しは接続の段の宛先の回りだけ(一巡し直しは options の connect-retries)— drain の Program が間を置いて問い直す。
  (CoordinatorCall [method path body]
    (<- reply RoutedReply (routed-request cell.route method path options None body))
    (setv cell.route reply.route)
    (<- answer dict (call-answer reply.answer))
    (resume answer))
  ;; drain の頼みは、要求の形をここで組んでから同じ口で送る(core は形を知らない — #2541)。
  (AskDrain [name ttl-seconds own-boot]
    (val request (drain-request name ttl-seconds own-boot))
    (<- reply RoutedReply (routed-request cell.route (get request 0) (get request 1) options None (get request 3)))
    (setv cell.route reply.route)
    (<- answer dict (call-answer reply.answer))
    (resume answer)))
