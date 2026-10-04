;;; fake の handler だけが持つ筋書きの口(FakeReply の fail・lose・usage・lines と、検の口 ClaudeForgetSession・world.restarted)の検。
;;; 上の層(doeff-agents の adapter と、その上の業務の模擬)が本番の翻訳 handler の下で、失敗の終わり・途中で消える process・usage・行数の多い本文・
;;; 家を空にした形・process の作り直しを fake で起こすための口。本物の CLI に同じ振る舞いを起こす宣言は無いので、fake の世界を
;;; この file の中で組む(3 つの解釈器で同じ筋書きを走らせる test_scenarios.hy とは別)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff [with_handlers Program EffectBase Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_time [SimClock sim-time-handler GetMonotonic Delay])
(import doeff_core_effects.scheduler [Spawn])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession Rebuilt])
(import doeff_claude_code.lines [AssistantMessage Completed Failed BackendLost Interrupted Init InputFate Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeReadTurnEvents ClaudeSessionStatus ClaudeExportSession TurnStarted SessionNotFound
                                   SessionExported SessionStatus TranscriptAbsent TranscriptPresent TurnRunning
                                   ClaudeInjectInput ClaudeInterruptTurn InputQueued InterruptRequested])
(import doeff_claude_code.faults [ClaudeForgetSession])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import tests.scenario_steps [TurnRecord read-to-end read-to-tool-start new-id typed kinds-of])

(val SPEC (ClaudeSessionSpec :home (ClaudeHome "fake-home") :cwd "/work"))
(val USAGE (Usage :input-tokens 11 :output-tokens 22 :cache-creation-input-tokens 3 :cache-read-input-tokens 4))
(val COST 0.5)
(val TIMEOUT 120.0)

(defn scripted-reply [#^ str text #^ tuple memory]  ; defk にできない: fake の世界が呼ぶ callback
  "筋書きの返事(本文の語で終わり方を選ぶ): fail / lose / lines / usage / それ以外は 30 秒の道具のあと本文をそのまま返す。"
  (cond
    (= text "fail") (FakeReply "" :tool-seconds 2.0 :fail "下の層の失敗" :usage USAGE :cost-usd COST)
    (= text "lose") (FakeReply "" :tool-seconds 2.0 :lose "消えた")
    (= text "lines") (FakeReply "done" :tool-seconds 4.0 :lines 300)
    (= text "usage") (FakeReply "counted" :usage USAGE :cost-usd COST)
    (= text "think") (FakeReply "thought" :think-seconds 5.0)
    (= text "no-receipt") (FakeReply "never" :tool-seconds 30.0 :interrupt-receipt False)
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
  ;; fail の返事: 期限で Failed(detail = 筋書きの文)で終わり、usage と手番の額を運ぶ。
  (<- record TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "fail")))
  (assert (isinstance record.end Failed) (repr record.end))
  (assert (= record.end.detail "下の層の失敗") (repr record.end))
  (assert (= record.end.usage USAGE) (repr record.end))
  (assert (= record.end.cost-usd COST) (repr record.end)))

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
  ;; usage の返事: Completed が筋書きの usage と手番の額を運ぶ(無い返事は空の Usage・額 None のまま — 0 を発明しない)。
  (<- record TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "usage")))
  (assert (isinstance record.end Completed) (repr record.end))
  (assert (= record.end.usage USAGE) (repr record.end))
  (assert (= record.end.cost-usd COST) (repr record.end))
  (<- plain TurnRecord (on-fake (FakeClaudeWorld scripted-reply) (run-one "think")))
  (assert (isinstance plain.end Completed) (repr plain.end))
  (assert (= plain.end.usage (Usage)) (repr plain.end))
  (assert (is plain.end.cost-usd None) (repr plain.end)))

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

(deftest test-an-interrupt-without-the-receipt-capability-drops-the-unread-input
  ;; 止めるの受理(interrupt_receipt_v1)を名乗らない CLI の手番に入力を足してから止める: 本物の handler(dialogue.hy の interrupt と
  ;; on-result の StopSignal の道)と同じく SIGINT の形で止まり、読まれていない入力は捨てた入力(dropped-refs)— 持ち越さず、次の手番も
  ;; 開かず、その入力の started も出ない。同じ会話は ResumeSession で続く。上の層の翻訳の「捨てた入力」の道を模擬で通すための口
  ;; (#3467)。受理を名乗る既定の手番の持ち越しは test_scenarios.hy の 3 つの解釈器の筋書きが守る。
  (val world (FakeClaudeWorld scripted-reply))
  (defk inject-then-stop []
    {:pre [] :post [(: % tuple)]}
    "受理を名乗らない手番に入力を足して止め、終わりまで読んでから同じ会話を続ける。"
    (val sid (new-id))
    (<- started (begin "no-receipt" (FreshSession sid)))
    (<- _opened (read-to-tool-start started.turn TIMEOUT))
    (<- queued (ClaudeInjectInput started.turn (typed "extra" "inj-dropped")))
    (<- asked (ClaudeInterruptTurn started.turn))
    (<- stopped (read-to-end started.turn TIMEOUT))
    (<- again (begin "usage" (ResumeSession sid)))
    (<- done (read-to-end again.turn TIMEOUT))
    #(queued asked stopped done))
  (<- #(queued asked stopped done) (on-fake world (inject-then-stop)))
  (assert (= queued (InputQueued "inj-dropped")) (repr queued))
  (assert (isinstance asked InterruptRequested) (repr asked))
  (assert (= stopped.end (Interrupted :dropped-refs #("inj-dropped"))) (repr stopped.end))
  (assert (= (lfor kind (kinds-of stopped.lines Init) kind.capabilities) [#("msg_lifecycle_v1")]) stopped.lines)
  (assert (not-in (InputFate "inj-dropped" "started") (kinds-of stopped.lines InputFate)) stopped.lines)
  (assert (isinstance done.end Completed) (repr done.end)))

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

(defk forget-after [#^ str sid #^ float seconds]
  {:pre [(: sid str) (: seconds float)] :post [(: % bool)]}
  "seconds 秒の後に会話 sid を家から消す(読みの待ちの外から手番を終える故障の注入)。"
  (<- (Delay seconds))
  (<- forgot (ClaudeForgetSession sid))
  forgot)

(deftest test-a-waiting-read-wakes-when-the-turn-is-ended-from-outside
  ;; 読みが長く待つ間に、待ちの外(別の task)が 2 秒後に家を空にして 30 秒の道具の手番を終える: 本番の CLI の行の流れと同じく、読みは
  ;; 期限(60 秒)や筋書きの次の刻(道具の終わり 30 秒)まで眠らず、終わらせた刻に BackendLost の終わりを返す(#3130 —
  ;; 眠り続けた読みの上で、機体の死を待っていた上の層の試行が先に止められ、手番を手放すのが 60 秒遅れた)。
  (val world (FakeClaudeWorld scripted-reply))
  (defk read-while-forgotten []
    {:pre [] :post [(: % tuple)]}
    "始めた手番の最初の行を読み切り、長い待ちの読みの間に会話を忘れさせ、読みが返った時の終わりと経った秒を返す。"
    (val sid (new-id))
    (<- started (begin "long" (FreshSession sid)))
    (<- first (ClaudeReadTurnEvents started.turn -1 0.0))
    (val after (. (get first.lines -1) seq))
    (<- began (GetMonotonic))
    (<- _forgetter (Spawn (forget-after sid 2.0)))
    (<- page (ClaudeReadTurnEvents started.turn after 60.0))
    (<- woke (GetMonotonic))
    #(page.end (- woke began)))
  (<- #(end waited) (on-fake world (read-while-forgotten)))
  (assert (isinstance end BackendLost) (repr end))
  (assert (< (abs (- waited 2.0)) 0.01) waited))

(defk effectful-reply [text memory]
  {:pre [(: text str) (: memory tuple)] :post [(: % FakeReply)]}
  "効果を出す筋書きの返事(respond の形): 返事の本文を外側の環境(Ask)から読む — 上の層の相手役が、いま始めている手番を
   自分の handler の状態から効果で読むのと同じ形。"
  (<- prefix str (Ask "reply-prefix"))
  (FakeReply (+ prefix text) :tool-seconds 1.0))

(deftest test-an-effectful-respond-answers-the-turn
  ;; respond(本文 記憶 → FakeReply の Program)の返事: 返事を作る時に出した効果は fake の handler の外側が答え、その答えで手番が終わる。
  (<- record TurnRecord (with_handlers [(reader {"reply-prefix" "from-effect: "})]
                          (on-fake (FakeClaudeWorld :respond effectful-reply) (run-one "hello"))))
  (assert (isinstance record.end Completed) (repr record.end))
  (assert (= record.end.result-text "from-effect: hello") (repr record.end)))

(deftest test-a-restarted-world-keeps-the-effectful-respond
  ;; process を作り直した世界も同じ respond で答える。
  (val restarted (.restarted (FakeClaudeWorld :respond effectful-reply)))
  (<- record TurnRecord (with_handlers [(reader {"reply-prefix" "again: "})] (on-fake restarted (run-one "hello"))))
  (assert (= record.end.result-text "again: hello") (repr record.end)))

(deftest test-a-world-takes-exactly-one-of-responder-and-respond
  ;; 返事の作り方はちょうど 1 つ(同期の responder か、効果を出せる respond)。
  (for [make [(fn [] (FakeClaudeWorld)) (fn [] (FakeClaudeWorld scripted-reply :respond effectful-reply))]]
    (try
      (make)
      (assert False "返事の作り方が 0 か 2 つの世界を受けた")
      (except [ValueError] None))))

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
