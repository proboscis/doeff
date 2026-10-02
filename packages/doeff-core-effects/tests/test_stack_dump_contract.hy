;;; 全 thread の stack を書く見張りの契約テスト — 同じ effect(ArmStackDump・DisarmStackDump・ReadStackDumps)に答える本物
;;; (faulthandler-stack-dump-handler)と fake(memory-stack-dump-handler)が、同じ deftest を通る(agora-redesign #2748)。解釈器の組み立ては
;;; stack_dump_contract_handlers.hy。
;;;
;;; 見る性質:
;;;   * 張り直されない見張りは、期限の刻に 1 度だけ書く(期限の後にもう 1 度読んでも増えない — repeat なし)
;;;   * 期限の前に張り直し、外した見張りは書かない
;;; 本物だけの性質(本当に sys.stderr へ全 thread の stack を書く)は test_faulthandler_stack_dump.hy。
;;; 本物は実時間で待つ(1 本 0.5 秒前後)。期限の前の張り直しは、機体の負荷で待ちが延びても期限を跨がない幅(期限 1 秒・待ち 0.1 秒)。
(require doeff-hy.macros [deftest <- val])
(import doeff_core_effects.stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps])
(import doeff_time [Delay GetMonotonic])

;; 張り直さない見張りの期限の秒と、期限を跨ぐ待ちの秒。
(val SHORT-WATCH 0.1)
(val PAST-DEADLINE 0.3)
;; 張り直す見張りの期限の秒と、期限の前の待ちの秒。
(val LONG-WATCH 1.0)
(val BEFORE-DEADLINE 0.1)


(deftest test-a-watch-not-rearmed-writes-once-at-its-deadline
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds SHORT-WATCH))
  (<- (Delay PAST-DEADLINE))
  (<- later float (GetMonotonic))
  (<- written tuple (ReadStackDumps :at later))
  (assert (= written #((+ start SHORT-WATCH))) (.format "期限を過ぎた見張りの書いた刻が {!r}(張った刻 {!r})" written start)))


(deftest test-a-watch-writes-only-once
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds SHORT-WATCH))
  (<- (Delay PAST-DEADLINE))
  (<- first-read float (GetMonotonic))
  (<- first tuple (ReadStackDumps :at first-read))
  (<- (Delay PAST-DEADLINE))
  (<- second-read float (GetMonotonic))
  (<- second tuple (ReadStackDumps :at second-read))
  (assert (= (len first) 1) (.format "期限を過ぎた見張りの書いた刻が {!r}" first))
  (assert (= second first) (.format "書いた後の見張りがもう 1 度書いた: {!r} → {!r}" first second)))


(deftest test-a-watch-rearmed-then-disarmed-before-its-deadline-writes-nothing
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds LONG-WATCH))
  (<- (Delay BEFORE-DEADLINE))
  (<- again float (GetMonotonic))
  (<- (ArmStackDump :at again :seconds LONG-WATCH))
  (<- (Delay BEFORE-DEADLINE))
  (<- off float (GetMonotonic))
  (<- (DisarmStackDump :at off))
  (<- (Delay PAST-DEADLINE))
  (<- later float (GetMonotonic))
  (<- written tuple (ReadStackDumps :at later))
  (assert (= written #()) (.format "期限の前に張り直して外した見張りが書いた: {!r}" written)))
