(require doeff-hy.macros [defk <-])

;;; HttpRequest の failures-as-values(agora-redesign #805 の構成レビュー 2-1 — 呼び手が transport の library の例外の型を知らずに届かない失敗を読むため):
;;;   * 立てれば、応答が 1 度も来なかった失敗は HttpFailed(url・detail = 例外の class と文)で答える
;;;   * 立てなければ今までどおり transport の例外が上がる(今の使い手は変わらない)
;;; 届かない相手は httpx 自身の MockTransport(library の持ち主の相手役)で作る。

(import httpx)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])


(defn refuse [request]
  (raise (httpx.ConnectError "[Errno 111] Connection refused" :request request)))

(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))]}
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "PATCH" url :max-retries 0 :failures-as-values values))
  answer)


(defn send-through [transport #^ bool values]
  (run (scheduled (with_handlers [(await-handler)
                                  (http-production-handler :client-factory (fn [] (httpx.AsyncClient :transport (httpx.MockTransport transport))))]
                                 (ask "https://api.test/x" values)))))


(defn test-an-unreachable-server-is-answered-as-a-value-when-asked []
  (assert (= (send-through refuse True)
             (HttpFailed :url "https://api.test/x" :detail "ConnectError: [Errno 111] Connection refused"))))


(defn test-without-the-field-the-transport-error-is-raised-as-before []
  (with [(pytest.raises httpx.ConnectError)]
    (send-through refuse False)))

