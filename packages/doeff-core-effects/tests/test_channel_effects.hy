;;; Tests for the generic channel effects (channel_effects.hy).
;;;   - Values can be taken in arrival order; a task taking from an empty channel waits until another task puts (other tasks keep running).
;;;   - Even if a waiter is Cancelled, the values put are not lost and the next waiter takes them.
;;;   - The consumer shape: the answers of Spawned computations (Compute) are read by the processing loop from one channel in arrival order.
(require doeff-hy.macros [defk <- val])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled Spawn Wait Cancel])
(import doeff_core_effects [Channel CreateChannel PutChannel TakeChannel])
(import doeff_core_effects [scheduler-channel-handler])
(import doeff_core_effects [Compute Computed])
(import doeff_core_effects [thread-pool-compute-handler])
(import doeff_core_effects [inline-compute-handler])


(defn on [handlers program]
  "To run program once with the scheduler under the channel handler."
  (run (scheduled (with_handlers handlers program))))


(defk puts [channel items]
  {:pre [(: channel Channel) (: items tuple)] :post [(: % str)]}
  "A producer task that puts items on the channel in order."
  (when items
    (<- (PutChannel channel (get items 0)))
    (<- (puts channel (cut items 1 None))))
  "put")


(defk takes [channel n]
  {:pre [(: channel Channel) (: n int)] :post [(: % tuple)]}
  "A consumer task that takes n items from the channel."
  (if (= n 0)
      #()
      (do
        (<- head (TakeChannel channel))
        (<- rest (takes channel (- n 1)))
        #(head #* rest))))


(defk fifo []
  {:pre [] :post [(: % list)]}
  "A scenario where the consumer first waits on an empty channel and the producer puts later."
  (<- channel (CreateChannel))
  (<- reader (Spawn (takes channel 3)))
  (<- writer (Spawn (puts channel #("one" "two" "three"))))
  (<- read (Wait reader))
  (<- wrote (Wait writer))
  [read wrote])


(defn test-take-waits-for-put-and-keeps-the-order []
  (assert (= (list (on [scheduler-channel-handler] (fifo))) [#("one" "two" "three") "put"])))


(defk cancelled-waiter []
  {:pre [] :post [(: % str)]}
  "A scenario where a value put after one of the waiters was Cancelled is taken by a living waiter."
  (<- channel (CreateChannel))
  (<- gone (Spawn (TakeChannel channel)))
  (<- alive (Spawn (TakeChannel channel)))
  (<- tick (Spawn (puts channel #())))
  (<- (Wait tick))  ; run until the first 2 waiters are waiting
  (assert (= (len channel.waiters) 2) channel.waiters)
  (<- (Cancel gone))
  (<- (PutChannel channel "remaining value"))
  (<- got (Wait alive))
  got)


(defn test-a-cancelled-waiter-does-not-lose-the-item []
  (assert (= (on [scheduler-channel-handler] (cancelled-waiter)) "remaining value")))


(defk answers-to-loop [n]
  {:pre [(: n int)] :post [(: % Computed)]}
  "A task that puts a computation's answer onto the channel (the shape of work outside the processing loop)."
  (<- channel (CreateChannel))
  (<- worker (Spawn (answer-into channel n)))
  (<- got (TakeChannel channel))
  (<- (Wait worker))
  got)


(defk squared [n]
  {:pre [(: n int)] :post [(: % int)]}
  "A scenario of a pure computation."
  (* n n))


(defk answer-into [channel n]
  {:pre [(: channel Channel) (: n int)] :post [(: % None)]}
  "To run a computation and put its answer on the receiving channel."
  (<- outcome (Compute (squared n)))
  (<- (PutChannel channel outcome))
  None)


(defn test-the-loop-reads-computed-answers-through-the-channel []
  (assert (= (on [scheduler-channel-handler inline-compute-handler] (answers-to-loop 7)) (Computed :value 49)))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (assert (= (on [scheduler-channel-handler (thread-pool-compute-handler pool)] (answers-to-loop 7)) (Computed :value 49)))))
