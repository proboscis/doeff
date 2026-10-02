;;; 全 thread の stack を書く見張りの本物の答え手(faulthandler-stack-dump-handler)だけの性質 — 期限を過ぎると、張った時の sys.stderr へ
;;; 本当に全 thread の stack を 1 度書き、期限の前に外せば何も書かない(agora-redesign #2748)。契約の性質は test_stack_dump_contract.hy。
;;; 実時間で待つ(1 本 0.5 秒前後 — 期限の後の待ちは、機体の負荷で見張りの thread の起き上がりが遅れても書き終わる幅)。
(require doeff-hy.macros [deftest <- val])
(import doeff_core_effects.stack_dump_effects [ArmStackDump DisarmStackDump])
(import doeff_time [Delay GetMonotonic])
(import stack_dump_contract_handlers [WrittenText])

;; 見張りの期限の秒と、期限の後に書き終わるのを待つ秒。
(val WATCH 0.1)
(val SETTLE 0.4)


(deftest test-a-passed-deadline-writes-every-thread-stack-once
  {:interpreters ["faulthandler-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds WATCH))
  (<- (Delay SETTLE))
  (<- text str (WrittenText))
  ;; faulthandler の見張りは 1 度書くごとに「Timeout (…)!」の見出しを 1 行置き、続けて thread ごとに「(most recent call first)」の stack を書く。
  (assert (= (.count text "Timeout (") 1) text)
  (assert (in "most recent call first" text) text))


(deftest test-a-disarmed-watch-writes-nothing
  {:interpreters ["faulthandler-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds WATCH))
  (<- off float (GetMonotonic))
  (<- (DisarmStackDump :at off))
  (<- (Delay SETTLE))
  (<- text str (WrittenText))
  (assert (= text "") text))
