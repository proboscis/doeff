;;; 検の HTTP の答え手 — 宛先の部品(coordinator_route)の上の口(detached-cluster・warm-cluster ほか)が出す汎用の HttpRequest に、
;;; httpx の transport(MockTransport の後ろの MemoryCoordinator・筋書きの答え)で同期に答える(#2337 の 4c — 前は検が httpx の
;;; transport を client に直に渡していた)。本番の答え手 http-production-handler と同じく、届かない失敗は値(HttpFailed)で返す:
;;; 接続の段の失敗(ConnectError・ConnectTimeout)= CONNECT-FAILED・時間切れ = TIMED-OUT・ほかの通信の失敗 = OTHER。
;;; 使い手は答え手 (transport-http transport) を外側に、口の handler(宛先の入れ物 route-cell・送り方 TEST-ROUTE)を内側に並べる。
(require doeff-hy.macros [defhandler deff val])
(import httpx)
(import doeff [run])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions])
(import doeff_cluster.foundation.coordinator_http [RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import os)
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.protocol.detached [DetachedSender])

;; 検の coordinator の宛先(MockTransport は宛先を見ない)と、口の送り方(本番の組み立てと同じ値)。
(val COORDINATOR-URL "http://coordinator")
(val TEST-ROUTE (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 4 :recheck-ms 60000 :actor "test-sender"))


(deff failure-kind [#^ httpx.TransportError error]  ; defk にできない: handler の節が httpx の例外を値に写す純粋な判断(except の中で呼ぶ)
  {:pre [(: error httpx.TransportError)] :post [(: % HttpFailureKind)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "httpx の通信の失敗を、本番の答え手と同じ失敗の種類にするため(接続の段 → 時間切れ → ほか の順に読む)。"
  (cond
    (isinstance error #(httpx.ConnectError httpx.ConnectTimeout)) HttpFailureKind.CONNECT-FAILED
    (isinstance error httpx.TimeoutException) HttpFailureKind.TIMED-OUT
    True HttpFailureKind.OTHER))


(defhandler transport-http [#^ httpx.BaseTransport transport]
  ;; 引数に残す理由: 答えの相手は検が作る transport そのもの(MemoryCoordinator や筋書きの答えを後ろに持つ — Ask で運ぶ設定ではない)。
  (HttpRequest [method url headers params body]
    (val request (httpx.Request method url :params params :json body :headers headers))
    (val answer (try (.handle-request transport request)
                     (except [error httpx.TransportError]
                       (HttpFailed :url url :detail (.format "{}: {}" (. (type error) __name__) error) :kind (failure-kind error)))))
    (resume (if (isinstance answer HttpFailed)
                answer
                (do (.read answer)
                    (HttpResponse answer.status-code (dict answer.headers) answer.content answer.text url 0.0))))))


(deff route-cell [#^ str [url COORDINATOR-URL]]  ; defk にできない: 組み立て(Program を走らせる前)が handler の引数を作る準備
  {:pre [(: url str)] :post [(: % RouteCell)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "口の handler が要求から要求へ持ち越す宛先の状態の入れ物(宛先 1 つ — 組み立てのたびに新しく作る)。"
  (RouteCell (CoordinatorRoute :urls #(url) :active 0 :switched-at-ms 0)))


(deff detached-sender [#^ str revision #^ (| RuntimeEnv None) [runtime-env None] #^ float [deadline-seconds IDEMPOTENT-DEADLINE-SECONDS]]  ; defk にできない: 組み立て(Program を走らせる前)が handler の引数を作る準備
  {:pre [(: revision str) (: runtime-env (| RuntimeEnv None)) (: deadline-seconds float)] :post [(: % DetachedSender)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の切り離した task の送り手(版の識別はこの process の版 — 本番の組み立ては宿の契約の Ask で読む)。"
  (DetachedSender :revision revision :versions (run (process-versions os.environ)) :runtime-env runtime-env :deadline-seconds deadline-seconds))


(defn #^ None released-through [#^ httpx.BaseTransport transport #^ str job #^ str instance]  ; defk にできない: 検が Program の外から 1 回走らせる入口
  "終わった process の lease の返し(worker/protocol/lease_release の lease-release — #2427)を、transport の後ろの coordinator への検の
   HTTP の答え手と模擬の時計の下で 1 回走らせる(行き先の 1 行は捨てる)。"
  (import doeff [run with-handlers])
  (import doeff_core_effects.handlers [slog-discard-handler])
  (import doeff_core_effects.scheduler [scheduled])
  (import doeff_time [SimClock sim-time-handler])
  (import doeff_cluster.worker.intent.worker_model [ReleaseLeases])
  (import doeff_cluster.worker.protocol.lease_release [lease-release])
  (run (scheduled (with-handlers [(transport-http transport) slog-discard-handler (sim-time-handler :clock (SimClock))
                                  (lease-release (route-cell) TEST-ROUTE)]
                                 (ReleaseLeases job instance)))))
