;; 考えの block の始まりが、層 3 の出来事 AgentThinkingStartedEvent になる事の検(agora-redesign #4186)。
;; 層 2 の行の型 → 層 3 の出来事を作る 1 か所(handlers/headless.hy の event-builders-of)を直に読む — CLI も session host も使わない。
(require doeff-hy.macros [deftest val])
(import datetime [datetime timezone])
(import doeff_claude_code.lines [PartialMessage DeltaKind])
(import doeff_agents.effects [AgentThinkingStartedEvent AgentThinkingDeltaEvent])
(import doeff_agents.handlers.headless [event-builders-of])

(val AT (datetime 2026 10 9 1 0 0 :tzinfo timezone.utc))

(deftest test-the-start-of-a-thinking-block-becomes-one-started-event
  ;; 考えの最初の差分(AgentThinkingDeltaEvent)より前に、上の層が「考えている」と分かる合図を 1 つ受ける。
  ;; 失敗ケース: 始まりを落とす adapter では出来事が 0 個で、上の層は考えの最初の差分(約 0.3 秒後)まで何も出せない。
  (val built (lfor build (event-builders-of (PartialMessage :thinking-start True) AT) (build 7)))
  (assert (= built [(AgentThinkingStartedEvent :seq 7 :at AT)]) (repr built)))

(deftest test-only-the-start-of-a-thinking-block-makes-the-started-event
  ;; 何も名乗らない行(message_start・本文の block の始まり)は出来事にしない。考えの差分は今までどおり差分の出来事だけ。
  (assert (= (event-builders-of (PartialMessage) AT) []))
  (val delta (lfor build (event-builders-of (PartialMessage :delta DeltaKind.THINKING :thinking-delta "hmm") AT) (build 8)))
  (assert (= delta [(AgentThinkingDeltaEvent :seq 8 :at AT :text "hmm")]) (repr delta)))
