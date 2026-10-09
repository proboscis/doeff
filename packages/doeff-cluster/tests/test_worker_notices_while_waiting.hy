;; worker の生死の知らせ(WorkerGone・WorkerBack)は、coordinator が要求を待つ間に遅れずに出る(#4270 の「別に残る事実」)。本番の
;; 2026-10-09 07:20 の coordinator の起き直しの後、起動の時の今の状態の知らせが 07:20:46〜07:23:51 に 1 つずつ届き、受け手には
;; agent-worker-2 の WorkerBack が届かなかった。遅れは 28 分から 102 分へ伸び、新しい生死の動きが起きた時だけ古い知らせが 1 つ進んで
;; 届いた。
;;
;; 組は本番のまま: 受付の答え手(shared/protocol/inbox の http-requests と foundation の RequestInbox — HTTP の server は起こさない)・送り出し
;; (coordinator の announced-aside と doeff-events の notice-events-handler・WORKER-NOTICE-ROUTES)・await-handler。broker だけを、本物の
;; Redis の答え手(doeff-events の redis_notice_handler)と同じく送りの答えを別の thread(await-handler の event loop)から返す代役にする。
;; 時計は仮想(sim-time-handler)。
;;   1 起動の時に 3 つの知らせを出して要求を待つと(要求は来ない)、待ちの間に 3 つとも出る(待ちの頭の刻)。
;;   2 その後の生死の動き(w3 が戻る)の知らせも、次の待ちの頭で出る — 受け手が持つ w3 の最新は WorkerBack。
;; 直す前は 1 で 1 つだけ出る(受付の待ちが scheduler の外で thread を塞ぎ、送りの答えを受けた送り手の task が次の Spawn まで回らない)・
;; 2 で 2 つ(新しい動きの Spawn が古い知らせを 1 つ押し出し、w3 の WorkerBack は出ない)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import asyncio)
(import doeff [with-handlers])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [await-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_events [Announce notice-events-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.protocol.inbox [http-requests])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox])
(import doeff_cluster.coordinator.core.program [announced-aside])
(import doeff_cluster.coordinator.intent.worker_notices [WorkerBack WorkerGone])
(import doeff_cluster.coordinator.protocol.worker_notices [WORKER-NOTICE-ROUTES back-of gone-of])
(import doeff_cluster.coordinator.entry.handler_sets [NOTICE-SOURCE NOTICE-PATIENCE-SECONDS])

;; 起動の時の今の状態(coordinator の liveness-now の形): w1・w2 は生きている・w3 は沈黙。
(val W1-BACK (WorkerBack :worker "w1" :boot "b1" :seen-ms 1000))
(val W2-BACK (WorkerBack :worker "w2" :boot "b2" :seen-ms 1100))
(val W3-GONE (WorkerGone :worker "w3" :boot "b3" :deadline-ms 1200))
(val STARTUP #(W1-BACK W2-BACK W3-GONE))
;; その後の生死の動き: w3 が戻る。
(val W3-BACK (WorkerBack :worker "w3" :boot "b3" :seen-ms 9000))
;; coordinator が受付を待つ秒(次の期限まで — 要求は来ない)。
(val WAIT-SECONDS 0.5)


(defclass SentNotices []
  "代役の broker が受けた送りの控え: rows = #(刻 epoch ms 道の名 本文) の tuple(受けた順)。"
  (setv #^ tuple rows #()))


(defhandler network-broker [#^ SentNotices sent]
  ;; Redis の代役: 送りを受けた刻・道の名・本文を控えに足し、答え(受け手の数 1)を await-handler の event loop の thread から返す
  ;; (本物の答え手 redis_notice_handler の Announce と同じ — 送り手の task は答えを受けるまで外からの完了を待つ)。
  (Announce [channel name body]
    (<- at int (now-epoch-ms))
    (setv sent.rows (+ sent.rows #(#(at name body))))
    (<- receivers int (Await (asyncio.sleep 0 1)))
    (resume receivers)))


(defk heard [sent]
  {:pre [(: sent SentNotices)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "代役の broker が受けた送りを、#(刻 出来事) の列に解くため(本文は道の decode で読む)。"
  (tuple (gfor #(at name body) sent.rows
               #(at (match name
                      "worker-gone" (gone-of body)
                      "worker-back" (back-of body))))))


(defk startup-then-move [sent]
  {:pre [(: sent SentNotices)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の起動と次の歩の形: 起動の時の今の状態を出して受付を待ち、その後の生死の動きを出してまた待つ。答え = #(待ちの頭の刻
   1 回目の待ちの後に出ていた送り 2 回目の待ちの後に出ていた送り)。"
  (<- started int (now-epoch-ms))
  (<- (announced-aside STARTUP))
  (<- (NextRequests WAIT-SECONDS))
  (<- after-start tuple (heard sent))
  (<- (announced-aside #(W3-BACK)))
  (<- (NextRequests WAIT-SECONDS))
  (<- after-move tuple (heard sent))
  #(started after-start after-move))


(deftest test-worker-notices-go-out-while-the-coordinator-waits-for-requests
  (val sent (SentNotices))
  (<- seen tuple ((sim-time-handler :clock (SimClock))
                   (with-handlers [(await-handler) (http-requests (RequestInbox 0 30.0)) (network-broker sent)
                                   (notice-events-handler NOTICE-SOURCE WORKER-NOTICE-ROUTES NOTICE-PATIENCE-SECONDS)]
                     (startup-then-move sent))))
  (val started (get seen 0))
  (val after-start (get seen 1))
  (val after-move (get seen 2))
  (val moved (+ started (round (* 1000 WAIT-SECONDS))))
  (val expected-start (tuple (gfor event STARTUP #(started event))))
  (assert (= #(after-start after-move) #(expected-start (+ expected-start #(#(moved W3-BACK)))))
          #(after-start after-move)))
