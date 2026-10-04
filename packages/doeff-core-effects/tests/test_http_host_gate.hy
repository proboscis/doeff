(require doeff-hy.macros [deftest defhandler defk <- val with-handler])

;;; http-host-gate(doeff_core_effects/http_host_gate.hy — agora-redesign #3412):
;;;   * 通す集合に在る host への要求は答えず、外側の答え手へそのまま流れる(port は見ない)
;;;   * 通す集合に無い host への要求は外へ出ない: 失敗を値で受ける要求には HttpFailed(CONNECT-FAILED)、それ以外には HostNotAllowed
;;;   * host は URL の hostname ちょうどで照らす(手元の名を前に付けただけの外の名は通らない)
;;;   * 通す集合が空なら、どの要求も外へ出ない
;;; 外側の相手役は受けた要求に 200(本文 = 受けた url)で答える。外へ出た要求は相手役の HttpResponse を受け、止めた要求は HttpFailed か
;;; HostNotAllowed を受ける — HttpResponse を作るのは相手役だけなので、答えの型が「外へ出たか」をそのまま名指す。

(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects.http_host_gate [HostNotAllowed http-host-gate])

;; 手元の構成の通す集合(この機体の中だけ)。
(val LOOPBACK (frozenset ["127.0.0.1" "localhost" "::1"]))


(defhandler answering-peer
  "外側の相手役: 受けた要求に 200(本文 = 受けた url)で答える(外へ出た要求だけがこの答えを受ける)。"
  {:tags {:context "http" :role "foundation"}}
  (HttpRequest [url]
    (resume (HttpResponse 200 {} (.encode url "utf-8") url url 0.0))))


(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "failures-as-values の旗だけを変えて同じ要求を 1 回出すため(撃ち直しなし)。"
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "GET" url :max-retries 0 :failures-as-values values))
  answer)


(defk gated [hosts url values]
  {:pre [(: hosts frozenset) (: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "foundation"}}
  "相手役の内側に http-host-gate(通す集合 hosts)を被せて要求を 1 回出すため。"
  (<- answer (| HttpResponse HttpFailed) (with-handler [answering-peer (http-host-gate hosts)] (ask url values)))
  answer)


(deftest test-a-request-to-an-allowed-host-flows-to-the-outer-answerer
  (for [url ["http://127.0.0.1:8320/worker/heartbeat" "http://localhost/x" "http://[::1]:9/y"]]
    (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK url True))
    (assert (isinstance answer HttpResponse) #(url answer))
    (assert (= answer.text url) #(url answer.text))))


(deftest test-a-request-to-an-outside-host-is-answered-as-not-connected-and-never-leaves
  (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK "https://api.anthropic.com/api/oauth/usage" True))
  (assert (isinstance answer HttpFailed) answer)
  (assert (= answer.kind HttpFailureKind.CONNECT-FAILED) answer))


(deftest test-a-request-to-an-outside-host-without-failures-as-values-raises-the-named-error
  (try
    (<- (gated LOOPBACK "https://api.github.com/app/installations" False))
    (assert False "通す集合の外の要求が止まらなかった")
    (except [e HostNotAllowed]
      (assert (= e.url "https://api.github.com/app/installations") e.url))))


(deftest test-a-host-is-matched-exactly-not-by-prefix
  (for [url ["http://127.0.0.1.attacker.test/x" "http://localhost.example/x"]]
    (<- answer (| HttpResponse HttpFailed) (gated LOOPBACK url True))
    (assert (isinstance answer HttpFailed) #(url answer))))


(deftest test-an-empty-allowed-set-lets-no-request-out
  (for [url ["http://127.0.0.1:8320/worker/heartbeat" "https://api.anthropic.com/api/oauth/usage"]]
    (<- answer (| HttpResponse HttpFailed) (gated (frozenset) url True))
    (assert (isinstance answer HttpFailed) #(url answer))))
