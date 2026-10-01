;;; job の process の終わりを待つ effect と、答えの型(2026-09-29・proboscis/doeff#631)。
;;;
;;;   (<- ended (AwaitProcessEnded "tally"))                         ; 終わるまで待つ
;;;   (<- ended (AwaitProcessEnded "tally" :timeout-seconds 300.0))  ; 300 秒まで(過ぎたら ProcessWaitExpired)
;;;
;;; 待つ相手 = job の今の最後の process(まだ 1 つも起きていなければ、最初に起きる process)。それが既に終わっていればすぐ答え、
;;; 動いていれば終わりを待つ。終わった後に同じ job の次の process が起きても(起こし直し)、答えは終わった方の process。
;;; 答えは値で返す(待ちの時間切れも例外にしない)。
;;;
;;; handler:
;;;   detached-cluster(detached.hy)… 本番: coordinator の GET /state の worker の状態の行を poll-seconds ごとに読む(本番の coordinator は
;;;                                   長い待ちの読みを持たない — 契約の答えとして置く)。
;;;   sim-cluster(local.hy)        … 模擬: sim の世界が process の終わりを書いた時に待ち手の Promise を満たす(読み直さない)。
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defrecord ProcessEnded
  "job の process が終わった(AwaitProcessEnded の答え)。job = job の名・instance = 終わった process の世代の名(分からなければ空)・
   worker = その process を起こした worker の名(分からなければ空)。"
  (#^ str job)
  (#^ str instance)
  (#^ str worker))


(defrecord ProcessWaitExpired
  "timeout-seconds の間に job の process が終わらなかった(AwaitProcessEnded の答え — 時間切れも値で返す)。"
  (#^ str job)
  (#^ float waited-seconds))


(defeffect AwaitProcessEnded
  "job の今の最後の process(まだ無ければ最初に起きる process)が終わるまで待つ。timeout-seconds = 待つ上限の秒(None = 終わるまで・
   0 = 待たずに今の姿を読む)。答え = ProcessEnded か ProcessWaitExpired。"
  {:fields [(: job str) (: timeout-seconds (| float None) None)]
   :answer (| ProcessEnded ProcessWaitExpired)
   :tags {:context "doeff-cluster" :role "intent"}})
