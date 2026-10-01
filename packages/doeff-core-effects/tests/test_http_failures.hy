(require doeff-hy.macros [deftest defk <- with-handler])

;;; HttpRequest の failures-as-values(agora-redesign #805 の構成レビュー 2-1 — 呼び手が transport の library の例外の型を知らずに届かない失敗を読むため):
;;;   * 立てれば、応答が 1 度も来なかった失敗は HttpFailed(url・detail = 例外の class と文・kind = 失敗の類)で答える
;;;   * 立てなければ今までどおり transport の例外が上がる(今の使い手は変わらない)
;;;   この 2 つは本物と fake(fixture の replay)の共通の契約として test_http_request_contract.hy が見る(agora-redesign #1159)。
;;; 失敗の類 kind(agora-redesign #850 — 消費者が detail の型名の文字列で期限切れを判じないため):
;;;   * 答え手が transport の例外の class の階層から写す: 期限切れ 4 種 = TIMED-OUT・接続できない = CONNECT-FAILED・残り = OTHER
;;;   * 反例: 名が Timeout で終わるだけの例外や、文に timed out を含む失敗は期限切れと読めない
;;;   * kind は既定値を持たない(作り手が書き忘れたら作る時に落ちる — 黙って「期限切れでない」にならない)
;;; 届かない相手は httpx 自身の MockTransport(library の持ち主の相手役)で作る。

(import httpx)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects.http_handlers [http-production-handler])


(defclass GatewayTimeout [httpx.NetworkError]
  "名が Timeout で終わるが期限切れの類(httpx.TimeoutException)ではない transport の失敗 — 型の名を読むと期限切れと取り違える反例。")


(defk ask [url values]
  {:pre [(: url str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))] :tags {:context "http" :role "program"}}
  "failures-as-values の旗だけを変えて同じ要求を 1 回出すため(撃ち直しなし)。"
  (<- answer (| HttpResponse HttpFailed) (HttpRequest "PATCH" url :max-retries 0 :failures-as-values values))
  answer)


(defk answer-from-silent-server [error-class text values]
  {:pre [(: error-class type) (: text str) (: values bool)] :post [(: % (| HttpResponse HttpFailed))]
   :tags {:context "http" :role "foundation"}}
  "応答しない相手(MockTransport が要求ごとに error-class の例外を上げる)へ本番の答え手で 1 回要求し、答え手の答えを読むため。"
  (<- answer (| HttpResponse HttpFailed)
      (with-handler [(await-handler)
                     (http-production-handler
                       :client-factory (fn [] (httpx.AsyncClient :transport (httpx.MockTransport (fn [request] (raise (error-class text :request request)))))))]
        (ask "https://api.test/x" values)))
  answer)


(deftest test-the-failure-kind-comes-from-the-transport-error-class
  (for [#(error-class kind) [#(httpx.ConnectTimeout HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.WriteTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.PoolTimeout HttpFailureKind.TIMED-OUT)
                            #(httpx.ConnectError HttpFailureKind.CONNECT-FAILED)
                            #(httpx.ReadError HttpFailureKind.OTHER)
                            #(httpx.RemoteProtocolError HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class "no answer" True))
    (assert (= failed.kind kind) #(error-class failed))))


(deftest test-a-failure-that-only-looks-like-a-timeout-is-not-read-as-one
  ;; detail は型の名と文を運ぶので、detail を読む暫定は 1 つ目(型の名が …Timeout)を期限切れと読み、文を読めば 2・3 つ目も
  ;; 期限切れと読んでしまう(#850 の反例)。kind は class の階層だけから決まる。
  (for [#(error-class text detail kind) [#(GatewayTimeout "upstream gave up" "GatewayTimeout: upstream gave up" HttpFailureKind.OTHER)
                                        #(httpx.ConnectError "timed out" "ConnectError: timed out" HttpFailureKind.CONNECT-FAILED)
                                        #(httpx.ReadError "ReadTimeout: peer went away" "ReadError: ReadTimeout: peer went away"
                                          HttpFailureKind.OTHER)]]
    (<- failed (| HttpResponse HttpFailed) (answer-from-silent-server error-class text True))
    (assert (= failed (HttpFailed :url "https://api.test/x" :detail detail :kind kind)) failed)))


(deftest test-a-failure-cannot-be-made-without-its-kind
  (try
    (HttpFailed :url "https://api.test/x" :detail "ReadTimeout: timed out")
    (assert False "kind の無い HttpFailed が作れた")
    (except [TypeError])))
