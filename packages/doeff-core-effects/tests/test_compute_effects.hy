;;; 汎用の計算の effect(compute_effects.hy — agora-redesign #802 便 4)の検。
;;;   - 本物(thread-pool-compute-handler)と I/O なし(inline-compute-handler)が同じ筋書きに同じ答えを返す: 値・落ちた program・純粋でない
;;;     program(呼び手の handler には届かず ComputeFailed)。
;;;   - 本物は計算の間も scheduler の他の task を回す(計算が他の task の合図を待っても詰まらない)。
;;;   - 本物は同時に回す数を pool が決める。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.handle [defhandler])
(import threading)
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import doeff [run with_handlers EffectBase])
(import doeff_core_effects.scheduler [scheduled Spawn Gather])
(import doeff_core_effects.compute_effects [Compute Computed ComputeFailed])
(import doeff_core_effects.inline_compute [inline-compute-handler])
(import doeff_core_effects.thread_pool_compute [thread-pool-compute-handler])


(defclass [(dataclass :frozen True)] Outside [EffectBase]
  "計算の中から出すと届く先の無い effect(純粋でない program の筋書き)。")


(defhandler outside-answers
  ;; 呼び手の側にだけ在る答え手 — 計算の中の effect がここへ届かないことを確かめるため。
  (Outside [] (resume "呼び手が答えた")))


(defk doubled [n]
  {:pre [(: n int)] :post [(: % int)]}
  "純粋な計算の筋書き。"
  (* 2 n))


(defk broken []
  {:pre [] :post [(: % int)]}
  "落ちる計算の筋書き。"
  (raise (ValueError "壊れた計算")))


(defk impure []
  {:pre [] :post [(: % str)]}
  "effect を出す(純粋でない)計算の筋書き。"
  (<- answer (Outside))
  answer)


(defk three-answers []
  {:pre [] :post [(: % tuple)]}
  "同じ 3 つの筋書きを Compute で撃つ。"
  (<- value (Compute (doubled 21)))
  (<- failed (Compute (broken)))
  (<- escaped (Compute (impure)))
  #(value failed escaped))


(defn on [handlers program]
  "handler の列の下で program を scheduler つきで 1 回回すため。"
  (run (scheduled (with_handlers handlers program))))


(defn check-three [answers]
  "3 つの筋書きの答えの形を確かめるため(本物と I/O なしで同じ)。"
  (setv #(value failed escaped) answers)
  (assert (= value (Computed :value 42)) value)
  (assert (and (isinstance failed ComputeFailed) (isinstance failed.error ValueError) (= (str failed.error) "壊れた計算")) failed)
  (assert (isinstance escaped ComputeFailed) escaped))


(defn test-inline-answers-values-and-failures-as-values []
  (check-three (on [outside-answers inline-compute-handler] (three-answers))))


(defn test-thread-pool-answers-the-same-as-inline []
  (with [pool (ThreadPoolExecutor :max-workers 2)]
    (check-three (on [outside-answers (thread-pool-compute-handler pool)] (three-answers)))))


(defn gate-race [handlers]
  "計算が別の task の開ける門を待つ筋書きを handlers の下で撃つため。scheduler が計算の間に止まるなら門は待ちの上限まで開かず、計算は
   False を返す。"
  (setv gate (threading.Event))
  (defk waits-for-gate []
    {:pre [] :post [(: % bool)]}
    "別の task が門を開けるまで待つ計算(待ちの上限つき)。"
    (.wait gate 1.0))
  (defk opens-gate []
    {:pre [] :post [(: % str)]}
    "門を開ける別の task。"
    (.set gate)
    "開けた")
  (defk both []
    {:pre [] :post [(: % list)]}
    "計算の task と門の task を同時に走らせる。"
    (<- computing (Spawn (Compute (waits-for-gate))))
    (<- opening (Spawn (opens-gate)))
    (<- answers (Gather computing opening))
    answers)
  (list (on handlers (both))))


(defn test-thread-pool-keeps-other-tasks-running-while-computing []
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv answers (gate-race [(thread-pool-compute-handler pool)])))
  (assert (= answers [(Computed :value True) "開けた"]) answers))


(defn test-inline-blocks-other-tasks-while-computing []
  ;; 反例: その場で回す答え手では計算の間に門の task が進まない — 上の検が違いを見分けていることの確かめ。
  (assert (= (gate-race [inline-compute-handler]) [(Computed :value False) "開けた"])))


(defn test-thread-pool-runs-as-many-at-once-as-the-pool-allows []
  (setv lock (threading.Lock)
        running [0]
        peak [0])
  (defk counted []
    {:pre [] :post [(: % int)]}
    "同時に回っている計算の数の最大を数える計算。"
    (with [lock]
      (setv (get running 0) (+ (get running 0) 1)
            (get peak 0) (max (get peak 0) (get running 0))))
    (.wait (threading.Event) 0.1)
    (with [lock] (setv (get running 0) (- (get running 0) 1)))
    1)
  (defk four []
    {:pre [] :post [(: % list)]}
    "計算を 4 つ同時に撃つ。"
    (<- a (Spawn (Compute (counted))))
    (<- b (Spawn (Compute (counted))))
    (<- c (Spawn (Compute (counted))))
    (<- d (Spawn (Compute (counted))))
    (<- answers (Gather a b c d))
    answers)
  (with [pool (ThreadPoolExecutor :max-workers 2)]
    (setv answers (on [(thread-pool-compute-handler pool)] (four))))
  (assert (= (list answers) (* [(Computed :value 1)] 4)) answers)
  (assert (= (get peak 0) 2) peak))
