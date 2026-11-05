;;; scheduler-channel-handler, the answerer for the generic queue effects (channel_effects.hy). It has no I/O and waits on
;;; the same run's scheduler promises (CreatePromise / CompletePromise / Wait) - the same answerer for production and mock.
;;;
;;; When something is put, it wakes all waiters, and each woken waiter rechecks the queue (waits again if empty). The value is not handed to just one waiter because, if that waiter's task is Cancelled,
;;; the value given to its promise would reach nobody and vanish - instead the value always stays in the queue and a live waiter takes it.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "scheduler" :role "foundation"})
(import doeff_core_effects._scheduler [CreatePromise CompletePromise Wait])
(import doeff_core_effects._channel_effects [Channel CreateChannel PutChannel TakeChannel])


(defk woken [promises]
  {:pre [(: promises tuple)] :post [(: % None)]}
  "To tell all waiters that something was put in the queue (woken waiters recheck the queue)."
  (when promises
    (<- (CompletePromise (get promises 0) None))
    (<- (woken (cut promises 1 None))))
  None)


(defk filled [channel]
  {:pre [(: channel Channel)] :post [(: % None)]}
  "To make only the task that fired wait until at least one item is put in the queue - on waking it rechecks, and waits again if empty."
  (when (not channel.items)
    (<- promise (CreatePromise))
    (.append channel.waiters promise)
    (<- (Wait promise.future))
    (<- (filled channel)))
  None)


(defhandler scheduler-channel-handler
  "Answers CreateChannel, PutChannel, and TakeChannel with the same run's scheduler promises (no I/O - see the header comment)."
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
