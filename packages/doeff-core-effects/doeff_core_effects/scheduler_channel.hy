;;; 汎用の列の effect(channel_effects.hy)の答え手 scheduler-channel-handler(agora-redesign #802 便 4 の相乗り)。I/O を持たず、同じ run の
;;; scheduler の promise(CreatePromise / CompletePromise / Wait)で待つ — 本番と模擬で同じ答え手。
;;;
;;; 積んだ時は待ち手を全部起こし、起きた待ち手は列を見直す(空なら待ち直す)。1 人だけに値を渡す形にしないのは、待ち手の task が Cancel
;;; された時に、その promise へ渡した値が誰にも届かず消えるため — 値は常に列に残り、生きている待ち手が取る。
(require doeff-hy.macros [defhandler defk <- val])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Wait])
(import doeff_core_effects.channel_effects [Channel CreateChannel PutChannel TakeChannel])


(defk woken [promises]
  {:pre [(: promises tuple)] :post [(: % None)]}
  "列に積んだことを待ち手の全部へ知らせるため(起きた待ち手が列を見直す)。"
  (when promises
    (<- (CompletePromise (get promises 0) None))
    (<- (woken (cut promises 1 None))))
  None)


(defk filled [channel]
  {:pre [(: channel Channel)] :post [(: % None)]}
  "列に 1 つ以上積まれるまで撃った task だけを待たせるため — 起きたら見直し、空なら待ち直す。"
  (when (not channel.items)
    (<- promise (CreatePromise))
    (.append channel.waiters promise)
    (<- (Wait promise.future))
    (<- (filled channel)))
  None)


(defhandler scheduler-channel-handler
  ;; 列の 3 つの effect に scheduler の promise で答える(頭の註)。
  (CreateChannel []
    (resume (Channel)))
  (PutChannel [channel item]
    (.append channel.items item)
    (val waiting (tuple channel.waiters))
    (.clear channel.waiters)
    (<- (woken waiting))
    (resume None))
  (TakeChannel [channel]
    (<- (filled channel))
    (resume (.popleft channel.items))))
