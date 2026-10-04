;; 名を挙げた Service の Ready を待つ AwaitServiceReady(detached_model・本番の detached.service-ready-awaited・sim の宿 — #3470)。
;;
;; 依る service の短い停止を落ちずに越える呼び手(合図の源・追記の書き手)が、戻りを出来事として知るための待ち。
;; - Ready の Service には、待たずに ServiceReady で返る。
;; - Ready でない間は返らず、coordinator の版の変化(GET /watch)で起きて読み直し、Ready に戻った拍で返る — 時間で起きて確かめない。
;;   模擬の cluster: 見本の系 beacons の担い手の worker を落として NotReady にし、別の worker へ置き直されて Ready に戻るまで待つ。
;; - 本番の client も同じ読み(service-ready-of)で答える: 200 Ready → 返る・200 NotReady と 404 → 版の変化を待って読み直す・
;;   200 の本文の形が違えば名指して落ちる・待つ口の無い coordinator(/watch が 404)は名指して落ちる。
(require doeff-hy.macros [deftest defk <- val var])
(import httpx)
(import pytest)
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.detached_model [AwaitServiceReady ServiceReady])
(import doeff_cluster.shared.protocol.detached [DetachedSender service-ready-awaited])
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator KillWorker])
(import tests.transport_http [transport-http route-cell TEST-ROUTE])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons NET])

(val WORKERS #((SimWorker :name "w1" :provides NET :task-reserve 0) (SimWorker :name "w2" :provides NET :task-reserve 0)))
(val SENDER (DetachedSender :revision "test" :versions {} :runtime-env None :deadline-seconds 2.0))


(defk ready-word [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の coordinator の Service name の status.ready の語を読むため(待ちの答えと突き合わせる筋書きの確かめ)。"
  (<- view dict (ReadCoordinator (+ "/resources/Service/" name)))
  (get (get view "status") "ready"))


(defk ready-at-once []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後(beacon が Ready)に待つ。答え = #(答え 待ち始めた刻 返った刻 返った時の語)。"
  (<- (Delay 12.0))
  (<- started int (now-epoch-ms))
  (<- answer ServiceReady (AwaitServiceReady "beacon"))
  (<- at int (now-epoch-ms))
  (<- word str (ready-word "beacon"))
  #(answer started at word))


(deftest test-a-ready-service-answers-at-once
  (<- seen tuple (sim-cluster (beacons sim-foundation) (ready-at-once) :workers WORKERS))
  (val answer (get seen 0))
  (val started (get seen 1))
  (val at (get seen 2))
  (val word (get seen 3))
  (assert (= answer (ServiceReady :name "beacon" :revision 0)) answer)
  (assert (= started at) "Ready の Service は待たずに返る")
  (assert (= word "Ready") seen))


(defk down-then-back []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon の担い手の worker を落とし、準備の報告が窓(5 秒)を過ぎて NotReady になってから待ち始める。beacon は lease の後に
   別の worker へ置き直されて Ready に戻り、その拍で待ちが返る。答え = #(落とす前の語 待ち始めた時の語 答え 待ち始めた刻 返った刻 返った時の語)。"
  (<- (Delay 12.0))
  (<- view dict (ReadCoordinator "/state"))
  (val holder (get (get (get view "placements") "beacon") "worker"))
  (<- before str (ready-word "beacon"))
  (<- (KillWorker holder))
  (<- (Delay 8.0))
  (<- down str (ready-word "beacon"))
  (<- started int (now-epoch-ms))
  (<- answer ServiceReady (AwaitServiceReady "beacon"))
  (<- at int (now-epoch-ms))
  (<- back str (ready-word "beacon"))
  #(before down answer started at back))


(deftest test-a-service-that-is-down-is-waited-for-until-it-is-ready-again
  ;; 失敗ケース: NotReady の間に返る待ち(読みを 1 回しかしない・Ready を見ない)は、待ち始めた刻に返り、返った時の語が NotReady。
  (<- seen tuple (sim-cluster (beacons sim-foundation) (down-then-back) :workers WORKERS))
  (val before (get seen 0))
  (val down (get seen 1))
  (val answer (get seen 2))
  (val started (get seen 3))
  (val at (get seen 4))
  (val back (get seen 5))
  (assert (= before "Ready") seen)
  (assert (= down "NotReady") seen)
  (assert (= answer.name "beacon") answer)
  (assert (> answer.revision 0) answer)
  (assert (< started at) "NotReady の間は返らない")
  (assert (= back "Ready") seen))


;; --- 本番の client は同じ読みで待つ ---------------------------------------------------------------------------------


(defk awaited-through [answer]
  {:pre [(: answer Callable)] :post [(: % ServiceReady)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の待ち(service-ready-awaited)を、answer(要求 → httpx の返事)で答える coordinator の上で出すため(Service の名は store)。"
  (<- got (with-handlers [(sim-time-handler :clock (SimClock)) (transport-http (httpx.MockTransport answer))]
            (service-ready-awaited (route-cell "http://coord") TEST-ROUTE SENDER "store" 1.0)))
  got)


(defk after-one-watch [first-read]
  {:pre [(: first-read Callable)] :post [(: % Callable)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator の代役の答え方を作るため: /watch が来るまでの Service の読みは first-read で答え、/watch には版 7 で答え、その後の読みは Ready。"
  (val state {"watched" False})
  (fn [request]
    (cond
      (= request.url.path "/watch")
        (do (setv (get state "watched") True)
            (httpx.Response 200 :json {"revision" 7 "changed" True}))
      (get state "watched") (httpx.Response 200 :json {"status" {"ready" "Ready"}})
      True (first-read request))))


(deftest test-the-production-client-answers-a-ready-service-at-once
  (<- got ServiceReady (awaited-through (fn [request] (httpx.Response 200 :json {"status" {"ready" "Ready"} "spec" {}}))))
  (assert (= got (ServiceReady :name "store" :revision 0)) got))


(deftest test-the-production-client-waits-for-the-version-to-change-while-not-ready
  ;; 失敗ケース: NotReady か宣言の無い 404 を「戻った」と読む待ちは、版 7 の変化を待たずに revision 0 で返る。
  (<- not-ready-answer Callable (after-one-watch (fn [request] (httpx.Response 200 :json {"status" {"ready" "NotReady"}}))))
  (<- not-ready ServiceReady (awaited-through not-ready-answer))
  (assert (= not-ready (ServiceReady :name "store" :revision 7)) not-ready)
  (<- missing-answer Callable (after-one-watch (fn [request] (httpx.Response 404 :json {"error" "宣言が無い"}))))
  (<- missing ServiceReady (awaited-through missing-answer))
  (assert (= missing (ServiceReady :name "store" :revision 7)) missing))


(deftest test-the-production-client-names-a-malformed-service-answer
  (with [raised (pytest.raises ValueError :match "ServiceViewWire")]
    (<- _never (awaited-through (fn [request] (httpx.Response 200 :json {"status" "Ready"}))))))


(deftest test-the-production-client-names-a-coordinator-without-the-watch
  (with [raised (pytest.raises RuntimeError :match "GET /watch")]
    (<- _never (awaited-through (fn [request]
                                  (if (= request.url.path "/watch")
                                      (httpx.Response 404 :json {"error" "知らない"})
                                      (httpx.Response 200 :json {"status" {"ready" "NotReady"}})))))))
