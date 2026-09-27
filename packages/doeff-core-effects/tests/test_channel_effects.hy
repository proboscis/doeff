;;; 汎用の列の effect(channel_effects.hy — agora-redesign #802 便 4 の相乗り)の検。
;;;   - 届いた順に取れる・空の列を取る task は、別の task が積むまで待つ(他の task は回る)。
;;;   - 待ち手が Cancel されても、積んだ値は消えずに次の待ち手が取る。
;;;   - 消費者の形: Spawn した計算(Compute)の答えを、処理ループが 1 本の列で届いた順に読む。
(require doeff-hy.macros [defk <- val])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait Cancel])
(import doeff_core_effects.channel_effects [Channel CreateChannel PutChannel TakeChannel])
(import doeff_core_effects.scheduler_channel [scheduler-channel-handler])
(import doeff_core_effects.compute_effects [Compute Computed])
(import doeff_core_effects.thread_pool_compute [thread-pool-compute-handler])
(import doeff_core_effects.inline_compute [inline-compute-handler])


(defn on [handlers program]
  "handler の列の下で program を scheduler つきで 1 回回すため。"
  (run (scheduled (with_handlers handlers program))))


(defk puts [channel items]
  {:pre [(: channel Channel) (: items tuple)] :post [(: % str)]}
  "列に items を順に積む生産者の task。"
  (when items
    (<- (PutChannel channel (get items 0)))
    (<- (puts channel (cut items 1 None))))
  "積んだ")


(defk takes [channel n]
  {:pre [(: channel Channel) (: n int)] :post [(: % tuple)]}
  "列から n 個を取る消費者の task。"
  (if (= n 0)
      #()
      (do
        (<- head (TakeChannel channel))
        (<- rest (takes channel (- n 1)))
        #(head #* rest))))


(defk fifo []
  {:pre [] :post [(: % list)]}
  "先に消費者が空の列を待ち、後から生産者が積む筋書き。"
  (<- channel (CreateChannel))
  (<- reader (Spawn (takes channel 3)))
  (<- writer (Spawn (puts channel #("一" "二" "三"))))
  (<- read (Wait reader))
  (<- wrote (Wait writer))
  [read wrote])


(defn test-take-waits-for-put-and-keeps-the-order []
  (assert (= (list (on [scheduler-channel-handler] (fifo))) [#("一" "二" "三") "積んだ"])))


(defk cancelled-waiter []
  {:pre [] :post [(: % str)]}
  "待ち手の 1 人が Cancel された後に積んだ値を、生きている待ち手が取る筋書き。"
  (<- channel (CreateChannel))
  (<- gone (Spawn (TakeChannel channel)))
  (<- alive (Spawn (TakeChannel channel)))
  (<- tick (Spawn (puts channel #())))
  (<- (Wait tick))  ; 先に並んだ 2 人が待ちに入るまで回す
  (assert (= (len channel.waiters) 2) channel.waiters)
  (<- (Cancel gone))
  (<- (PutChannel channel "残る値"))
  (<- got (Wait alive))
  got)


(defn test-a-cancelled-waiter-does-not-lose-the-item []
  (assert (= (on [scheduler-channel-handler] (cancelled-waiter)) "残る値")))


(defk answers-to-loop [n]
  {:pre [(: n int)] :post [(: % Computed)]}
  "計算の答えを列へ積む task(処理ループの外の仕事の形)。"
  (<- channel (CreateChannel))
  (<- worker (Spawn (answer-into channel n)))
  (<- got (TakeChannel channel))
  (<- (Wait worker))
  got)


(defk squared [n]
  {:pre [(: n int)] :post [(: % int)]}
  "純粋な計算の筋書き。"
  (* n n))


(defk answer-into [channel n]
  {:pre [(: channel Channel) (: n int)] :post [(: % None)]}
  "計算を回して、答えを受け口の列へ積むため。"
  (<- outcome (Compute (squared n)))
  (<- (PutChannel channel outcome))
  None)


(defn test-the-loop-reads-computed-answers-through-the-channel []
  (assert (= (on [scheduler-channel-handler inline-compute-handler] (answers-to-loop 7)) (Computed :value 49)))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (assert (= (on [scheduler-channel-handler (thread-pool-compute-handler pool)] (answers-to-loop 7)) (Computed :value 49)))))
