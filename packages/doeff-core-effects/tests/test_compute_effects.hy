;;; 汎用の計算の effect(compute_effects.hy — agora-redesign #802 便 4)の検。
;;;   - 本物(thread-pool-compute-handler)と I/O なし(inline-compute-handler)が同じ筋書きに同じ答えを返す: 値・落ちた program・純粋でない
;;;     program(呼び手の handler には届かず ComputeFailed)。
;;;   - 本物は計算の間も scheduler の他の task を回す(計算が他の task の合図を待っても詰まらない)。
;;;   - 本物は同時に回す数を pool が決める。
;;;   - 負荷の読み(ReadComputeLoad): 本物は pool の thread を待つ仕事の数(並び)・回っている数(走り)・回り始めた数と待った秒と回った秒の累計を
;;;     答え、始まる前に取り消された仕事を並びから外す。I/O なしは列を持たないので全部 0。反例: 前の形は pool が大きな計算で塞がった時の
;;;     待ちを測る計器を持たず、待ちは要求ごとの区間の秒に混ざって見分けられなかった(doeff-records の ADR-DOE-RECORDS-002 R6)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.handle [defhandler])
(import threading)
(import time)
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import typing [TypeVar])
(import doeff [run with_handlers EffectBase Program])
(import doeff_core_effects [scheduled Spawn Gather Cancel])
(import doeff_core_effects [Compute Computed ComputeFailed ComputeLoad ReadComputeLoad])
(import doeff_core_effects [inline-compute-handler])
(import doeff_core_effects [thread-pool-compute-handler])
(import pytest)


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


;; on が回す program の答えの型(筋書きごとに違う)。
(val T (TypeVar "T"))


(defn #^ T on [#^ list handlers #^ (get Program T) program]
  "handler の列の下で program を scheduler つきで 1 回回すため。"
  (run (scheduled (with_handlers handlers program))))


(defn #^ None check-three [#^ tuple answers]
  "3 つの筋書きの答えの形を確かめるため(本物と I/O なしで同じ)。"
  (setv #(value failed escaped) answers)
  (assert (= value (Computed :value 42)) value)
  (assert (and (isinstance failed ComputeFailed) (isinstance failed.error ValueError) (= (str failed.error) "壊れた計算")) failed)
  (assert (isinstance escaped ComputeFailed) escaped))


(defn #^ None test-inline-answers-values-and-failures-as-values []
  (check-three (on [outside-answers inline-compute-handler] (three-answers))))


(defn #^ None test-thread-pool-answers-the-same-as-inline []
  (with [pool (ThreadPoolExecutor :max-workers 2)]
    (check-three (on [outside-answers (thread-pool-compute-handler pool)] (three-answers)))))


(defn #^ list gate-race [#^ list handlers]
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


(defn #^ None test-thread-pool-keeps-other-tasks-running-while-computing []
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv answers (gate-race [(thread-pool-compute-handler pool)])))
  (assert (= answers [(Computed :value True) "開けた"]) answers))


(defn #^ None test-inline-blocks-other-tasks-while-computing []
  ;; 反例: その場で回す答え手では計算の間に門の task が進まない — 上の検が違いを見分けていることの確かめ。
  (assert (= (gate-race [inline-compute-handler]) [(Computed :value False) "開けた"])))


(defn [pytest.mark.realtime] #^ None test-thread-pool-runs-as-many-at-once-as-the-pool-allows []
  (setv lock (threading.Lock)
        running [0]
        peak [0])
  (defk counted []
    {:pre [] :post [(: % int)]}
    "同時に回っている計算の数の最大を数える計算。"
    (with [lock]
      (setv (get running 0) (+ (get running 0) 1)
            (get peak 0) (max (get peak 0) (get running 0))))
    (time.sleep 0.1)
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


(defn #^ tuple held-pair [#^ threading.Event started #^ threading.Event gate]
  "pool の thread を門で塞ぐ計算の筋書きを 2 つ作るため: 回り始めると started を立て、門 gate が開くまで(上限 5 秒)thread を塞ぐ。"
  (defk held []
    {:pre [] :post [(: % bool)]}
    "回り始めを知らせ、門が開くまで pool の thread を塞ぐ計算。"
    (.set started)
    (.wait gate 5.0))
  #((held) (held)))


(defn #^ None test-thread-pool-load-counts-the-queue-and-the-wait []
  ;; pool の thread 1 本を 1 つ目の計算で塞ぎ、2 つ目の計算を列に並べる — 1 つ目が回り始めた出来事の後の負荷は並び 1・走り 1・回り
  ;; 始め 1。門を開けて 2 つとも終わった後は並びも走りも 0 で、回り始め 2・待ちの累計は 2 つ目が 1 つ目の終わりを待った秒で 0 より大きい。
  (setv started (threading.Event)
        gate (threading.Event))
  (setv #(first-held second-held) (held-pair started gate))
  (defk peek []
    {:pre [] :post [(: % ComputeLoad)]}
    "1 つ目が回り始めた出来事を待ってから負荷を読み、門を開ける task。"
    (.wait started 5.0)
    (<- busy ComputeLoad (ReadComputeLoad))
    (.set gate)
    busy)
  (defk both []
    {:pre [] :post [(: % tuple)]}
    "計算 2 つと覗きの task を同時に走らせ、塞がった時と終わった後の負荷を返す。"
    (<- first (Spawn (Compute first-held)))
    (<- second (Spawn (Compute second-held)))
    (<- peeking (Spawn (peek)))
    (<- answers (Gather first second peeking))
    (<- after ComputeLoad (ReadComputeLoad))
    #((tuple answers) after))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv #(answers after) (on [(thread-pool-compute-handler pool)] (both))))
  (setv busy (get answers 2))
  (assert (= (cut answers 0 2) #((Computed :value True) (Computed :value True))) answers)
  (assert (= #(busy.queued busy.running busy.started) #(1 1 1)) busy)
  (assert (= #(after.queued after.running after.started) #(0 0 2)) after)
  (assert (> after.waited-seconds 0.0) after)
  ;; 回った秒の累計は 2 つの計算が pool の thread を塞いだ秒の和(1 つ目は覗きの task が門を開けるまで回り続けた)。
  (assert (> after.ran-seconds 0.0) after))


(defn #^ None test-thread-pool-load-drops-a-queued-job-that-is-cancelled []
  ;; 列に並んだまま取り消された計算は回り始めないので並びから外れ、回り始めの数と待ちの累計に入らない。
  (setv started (threading.Event)
        gate (threading.Event))
  (setv #(first-held second-held) (held-pair started gate))
  (defk cancelled []
    {:pre [] :post [(: % tuple)]}
    "1 つ目で pool を塞ぎ、並んだ 2 つ目を取り消してから門を開け、取り消しの前と後と終わった後の負荷を返す。"
    (<- first (Spawn (Compute first-held)))
    (<- second (Spawn (Compute second-held)))
    ;; 立てた 2 つの task が計算を pool へ出してから(scheduler に順を渡す — 立てた順に回る)、1 つ目が回り始めた出来事を待つ。
    (<- handed (Spawn (doubled 0)))
    (<- (Gather handed))
    (.wait started 5.0)
    (<- busy ComputeLoad (ReadComputeLoad))
    (<- (Cancel second))
    (<- dropped ComputeLoad (ReadComputeLoad))
    (.set gate)
    (<- (Gather first))
    (<- after ComputeLoad (ReadComputeLoad))
    #(busy dropped after))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv #(busy dropped after) (on [(thread-pool-compute-handler pool)] (cancelled))))
  (assert (= #(busy.queued busy.running) #(1 1)) busy)
  (assert (= #(dropped.queued dropped.running dropped.started) #(0 1 1)) dropped)
  (assert (= #(after.queued after.running after.started) #(0 0 1)) after))


(defn #^ None test-inline-load-is-always-idle []
  ;; その場で回す答え手は列を持たない — 計算を回した後も負荷は全部 0。
  (defk computed-then-load []
    {:pre [] :post [(: % ComputeLoad)]}
    "計算を 1 つ回してから負荷を読む。"
    (<- (Compute (doubled 1)))
    (<- load ComputeLoad (ReadComputeLoad))
    load)
  (assert (= (on [inline-compute-handler] (computed-then-load)) (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0 :ran-seconds 0.0))))
