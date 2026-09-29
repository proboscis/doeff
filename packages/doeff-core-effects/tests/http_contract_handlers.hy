;;; HttpRequest の契約テストの解釈器(composition root)— 同じ契約の Program を、HttpRequest の handler だけ替えて走らせる
;;; (agora-redesign #1159)。
;;;
;;;   http-production      本物: http-production-handler。client は httpx の MockTransport で、外へ出ずに契約の世界(WORLD)が答える
;;;   http-fixture-replay  fake: http-fixture-handler の replay。先に同じ Program を record で 1 度走らせて同じ世界の答えを
;;;                        fixture へ記録し、次に transport の無い replay の handler の下でもう 1 度走らせる(答えはこちらを返す)
;;;
;;; 契約の世界(world-answer)は URL の path ごとに決まった答えを返す httpx の相手役。/flaky だけは世界の中で数えた試行の
;;; 回数で答えが変わる(5xx の再試行を見るため)— 世界は解釈器の 1 回の実行ごとに新しく作る。
;;; 再試行の間の待ちは 0 秒(no-wait)にする。使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk deff <- val])
(import asyncio)
(import collections [Counter])
(import datetime [timedelta])
(import functools [partial])
(import pathlib [Path])
(import tempfile)
(import httpx)
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-fixture-handler http-production-handler])

(val PRODUCTION "http-production")
(val FIXTURE-REPLAY "http-fixture-replay")
(val WORLD "https://world.test")
;; /flaky が 5xx を返す試行の回数(3 回目から 200)。
(val FLAKY-FAILURES 2)
(val REFUSED "[Errno 111] Connection refused")
(val NO-TIME (timedelta 0))


(deff world-answer [attempts request]  ; defk にできない: httpx の MockTransport が要求ごとに同期で呼ぶ callback
  {:pre [(: attempts Counter) (: request httpx.Request)] :post [(: % httpx.Response)]
   :tags {:context "http-test" :role "foundation"}}
  "httpx の MockTransport が要求ごとに呼ぶ契約の世界。attempts は path ごとの試行の回数(世界の 1 回の実行の中だけ)。
   /echo      200・本文 = method・要求の header x-mode・要求の本文(header と本文が答えを変える)
   /moved     302・location = /landing      /landing  200「landed」
   /flaky     FLAKY-FAILURES 回目までは 503、その後は 200「recovered」
   /missing   404「no such thing」           /down     接続できない(httpx.ConnectError)"
  (.update attempts [request.url.path])
  ;; 経過の秒を 0 に置く理由: MockTransport が返す本文を持った Response は client が読み直さず閉じもしないので、httpx は
  ;; elapsed を付けない(本物の transport では付く)。本物の handler はそれを読むので、世界の側で付けておく。
  (let [response (match request.url.path
    "/echo" (httpx.Response 200 :headers {"x-world" "echo"}
                            :content (.encode (.join " " [request.method (.get request.headers "x-mode" "-") (.decode request.content "utf-8")])
                                              "utf-8"))
    "/moved" (httpx.Response 302 :headers {"location" "/landing"})
    "/landing" (httpx.Response 200 :content b"landed")
    "/flaky" (if (<= (get attempts "/flaky") FLAKY-FAILURES)
                 (httpx.Response 503 :content b"not yet")
                 (httpx.Response 200 :content b"recovered"))
    "/missing" (httpx.Response 404 :content b"no such thing")
    "/down" (raise (httpx.ConnectError REFUSED :request request))
    other (raise (ValueError (+ "契約の世界に無い path: " other))))]
    (setattr response "elapsed" NO-TIME)
    response))


(deff world-client []  ; defk にできない: http-production-handler の client-factory として同期で呼ばれる callback
  {:pre [] :post [(: % httpx.AsyncClient)] :tags {:context "http-test" :role "foundation"}}
  "新しい契約の世界に繋がる httpx の client(http-production-handler の client-factory — 外の library の口なので deff)。"
  (httpx.AsyncClient :transport (httpx.MockTransport (partial world-answer (Counter)))))


(deff no-wait [delay]  ; defk にできない: http-production-handler の sleep として呼ばれ Await に渡す awaitable を返す callback
  {:pre [(: delay float)] :post [(: % "待たずに終わる awaitable")] :tags {:context "http-test" :role "foundation"}}
  "再試行の間の待ち(http-production-handler の sleep — Await に渡す awaitable を返す callback なので deff)。"
  (asyncio.sleep 0))


(defk under-production [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "http-test" :role "foundation"}}
  "本物の http-production-handler の下で program を走らせる(client は契約の世界に繋がる MockTransport)。"
  (<- answer (with_handlers [(await-handler) (http-production-handler :client-factory world-client :sleep no-wait)] program))
  answer)


(defk under-fixture-replay [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "http-test" :role "foundation"}}
  "program を record で 1 度走らせて契約の世界の答えを fixture へ記録し、transport の無い replay の handler の下でもう 1 度走らせる。
   replay の handler は作る時に fixture を読むので、record の実行の後に作る。"
  (with [directory (tempfile.TemporaryDirectory)]
    (val path (/ (Path directory) "contract.pickle"))
    (<- (with_handlers [(await-handler) (http-fixture-handler path :mode "record" :client-factory world-client :sleep no-wait)]
          program))
    (<- answer (with_handlers [(http-fixture-handler path :mode "replay")] program)))
  answer)


(val INTERPRETERS {PRODUCTION under-production
                   FIXTURE-REPLAY under-fixture-replay})
