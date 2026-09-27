;;; fake の handler だけが持つ筋書きの口(FakeReply の fail・lose・usage・lines と、検の口 ClaudeForgetSession・world.restarted)の検。
;;; 上の層(doeff-agents の adapter と、その上の業務の模擬)が本番の翻訳 handler の下で、失敗の終わり・途中で消える process・usage・行数の多い本文・
;;; 家を空にした形・process の作り直しを fake で起こすための口。本物の CLI に同じ振る舞いを起こす宣言は無いので、fake の世界を
;;; この file の中で組む(3 つの解釈器で同じ筋書きを走らせる test_scenarios.hy とは別)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff [with_handlers Program EffectBase])
(import doeff_time [SimClock sim-time-handler GetMonotonic])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession Rebuilt])
(import doeff_claude_code.lines [AssistantMessage Completed Failed BackendLost Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeSessionStatus ClaudeExportSession TurnStarted SessionNotFound
                                   SessionExported SessionStatus TranscriptAbsent TranscriptPresent TurnRunning])
(import doeff_claude_code.faults [ClaudeForgetSession])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import tests.scenario_steps [TurnRecord read-to-end new-id typed kinds-of])

(val SPEC (ClaudeSessionSpec :home (ClaudeHome "fake-home") :cwd "/work"))
(val USAGE (Usage :input-tokens 11 :output-tokens 22 :cache-creation-input-tokens 3 :cache-read-input-tokens 4))
(val TIMEOUT 120.0)

(defn scripted-reply [#^ str text #^ tuple memory]  ; defk にできない: fake の世界が呼ぶ callback
  "筋書きの返事(本文の語で終わり方を選ぶ): fail / lose / lines / usage / それ以外は 30 秒の道具のあと本文をそのまま返す。"
  (cond
    (= text "fail") (FakeReply "" :tool-seconds 2.0 :fail "下の層の失敗" :usage USAGE)
    (= text "lose") (FakeReply "" :tool-seconds 2.0 :lose "消えた")
    (= text "lines") (FakeReply "done" :tool-seconds 4.0 :lines 300)
    (= text "usage") (FakeReply "counted" :usage USAGE)
    (= text "think") (FakeReply "thought" :think-seconds 5.0)
    True (FakeReply text :tool-seconds 30.0)))

(defk on-fake [world program]
  {:pre [(: world FakeClaudeWorld) (: program (| Program EffectBase))] :post [(: % (| TurnRecord TurnStarted SessionStatus tuple))]}
  "検の Program を fake の世界 world の上で(仮想の時計つきで)走らせ、答えを返す。"
  (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) (fake-claude-code-handler world)] program))
  answer)

(defk begin [#^ str text origin]
  {:pre [(: text str) (: origin (| FreshSession ResumeSession))] :post [(: % TurnStarted)]}
  "手番を 1 つ始める(origin = FreshSession / ResumeSession)。"
  (<- started (ClaudeStartTurn origin SPEC (typed text)))
  (assert (isinstance started TurnStarted) (repr started))
  started)

(defk run-one [#^ str text]
  {:pre [(: text str)] :post [(: % TurnRecord)]}
  "新しい会話で手番を 1 つ走らせ、終わりまで読む。"
  (<- started (begin text (FreshSession (new-id))))
  (<- record (read-to-end started.turn TIMEOUT))
  record)


(deftest test-a-scripted-failure-ends-as-failed-with-usage
  ;; fail の返事: 期限で Failed(detail = 筋書きの文)で終わり、usage を運ぶ。
  (<- record TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "fail")))
  (assert (isinstance record.end Failed) (repr record.end))
  (assert (= record.end.detail "下の層の失敗") (repr record.end))
  (assert (= record.end.usage USAGE) (repr record.end)))

(deftest test-a-scripted-loss-ends-as-backend-lost-and-resume-works
  ;; lose の返事: 期限で BackendLost で終わる。会話は家に残り、次の ResumeSession は通る。
  (val world (FakeClaudeWorld scripted-reply))
  (defk lose-then-resume []
    {:pre [] :post [(: % tuple)]}
    "消えた手番の後に同じ会話を続ける。"
    (val sid (new-id))
    (<- started (begin "lose" (FreshSession sid)))
    (<- lost (read-to-end started.turn TIMEOUT))
    (<- again (begin "usage" (ResumeSession sid)))
    (<- done (read-to-end again.turn TIMEOUT))
    #(lost done))
  (<- #(lost done) (on-fake world (lose-then-resume)))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (assert (= lost.end.detail "消えた") (repr lost.end))
  (assert (isinstance done.end Completed) (repr done.end)))

(deftest test-usage-is-carried-on-the-completed-end
  ;; usage の返事: Completed が筋書きの usage を運ぶ(無い返事は空の Usage のまま — 今までと同じ)。
  (<- record TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "usage")))
  (assert (isinstance record.end Completed) (repr record.end))
  (assert (= record.end.usage USAGE) (repr record.end)))

(deftest test-lines-are-emitted-before-the-end
  ;; lines の返事: 本文の行が lines 行、終わりの前に出る(最後の本文の行 1 つは別)。
  (<- record TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "lines")))
  (assert (isinstance record.end Completed) (repr record.end))
  (val texts (lfor kind (kinds-of record.lines AssistantMessage) :if kind.text kind.text))
  (assert (= (len texts) 301) (len texts))
  (assert (= (get texts 0) "line 0") (get texts 0))
  (assert (= (get texts -1) "done") (get texts -1)))

(deftest test-a-thinking-turn-runs-its-seconds-without-tool-lines
  ;; think-seconds の返事: 道具の行を出さずに、その秒まで走ってから終わる。
  (defk think-and-time []
    {:pre [] :post [(: % tuple)]}
    "考える手番を走らせ、始めから終わりまでの仮想の秒を測る。"
    (<- began (GetMonotonic))
    (<- record (run-one "think"))
    (<- ended (GetMonotonic))
    #(record (- ended began)))
  (<- #(record seconds) (on-fake (FakeClaudeWorld scripted-reply) (think-and-time)))
  (assert (isinstance record.end Completed) (repr record.end))
  (assert (>= seconds 5.0) seconds)
  (assert (= (lfor kind (kinds-of record.lines AssistantMessage) :if kind.tool-names kind) []) record.lines))

(deftest test-fail-and-lose-together-are-refused
  ;; 終わり方は多くとも 1 つ。
  (try
    (FakeReply "" :fail "a" :lose "b")
    (assert False "fail と lose の両方を受けた")
    (except [ValueError] None)))

(deftest test-forgetting-a-session-empties-the-home
  ;; 家を空にした形: 走っている手番は BackendLost・transcript は消え、ResumeSession は SessionNotFound。写し(Rebuilt)を
  ;; 持ち込めば続けられる。
  (val world (FakeClaudeWorld scripted-reply))
  (defk forget-then-carry []
    {:pre [] :post [(: % tuple)]}
    "走っている会話を忘れさせ、写しを持ち込んで続ける。"
    (val sid (new-id))
    (<- started (begin "long" (FreshSession sid)))
    (<- copy (ClaudeExportSession SPEC.home SPEC.cwd sid))
    (<- forgot (ClaudeForgetSession sid))
    (<- lost (read-to-end started.turn TIMEOUT))
    (<- status (ClaudeSessionStatus SPEC.home SPEC.cwd sid))
    (<- refused (ClaudeStartTurn (ResumeSession sid) SPEC (typed "usage")))
    (<- carried (begin "usage" (ResumeSession sid :carry (Rebuilt copy.jsonl-text))))
    (<- done (read-to-end carried.turn TIMEOUT))
    #(copy forgot lost status refused done))
  (<- #(copy forgot lost status refused done) (on-fake world (forget-then-carry)))
  (assert (isinstance copy SessionExported) (repr copy))
  (assert (is forgot True))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (assert (isinstance status.transcript TranscriptAbsent) (repr status))
  (assert (isinstance refused SessionNotFound) (repr refused))
  (assert (isinstance done.end Completed) (repr done.end)))

(deftest test-a-restarted-world-shares-the-home-but-not-the-process
  ;; process の作り直し: 新しい世界は同じ家の transcript を見る(続きを開ける)が、前の process で走っている手番は知らない
  ;; (同じ会話の手番が走っていても TurnInFlight で断らない)。前の手番は前の世界で走り続ける。
  (val world (FakeClaudeWorld scripted-reply))
  (val restarted (.restarted world))
  (val sid (new-id))
  (<- first TurnStarted (on-fake world (begin "long" (FreshSession sid))))
  (<- second TurnStarted (on-fake restarted (begin "usage" (ResumeSession sid))))
  (<- status SessionStatus (on-fake world (ClaudeSessionStatus SPEC.home SPEC.cwd sid)))
  (assert (= second.session-id sid) (repr second))
  (assert (isinstance status.state TurnRunning) (repr status))
  (assert (isinstance status.transcript TranscriptPresent) (repr status)))
