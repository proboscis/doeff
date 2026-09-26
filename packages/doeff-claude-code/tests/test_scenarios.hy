;; 公開 effect の筋書き — fake・本番の handler + 替え玉の CLI・本番の handler + 本物の claude の 3 つで同じ Program を走らせる
;; (設計 layer2-effects-design.md 8 節の不変条件)。筋書きは handler を知らない: ScenarioSettings で宣言を読み、公開 effect だけを撃つ。
(require doeff-hy.macros [deftest <- val])
(import dataclasses [replace])
(import os.path)
(import doeff_claude_code.values [ClaudeTurn TurnInput FreshSession ResumeSession ForkSession AskHost Allow LinkFromHome Rebuilt
                                  ImageAttachment])
(import doeff_claude_code.lines [Init InputFate PermissionRequested TurnResult Completed Interrupted BackendLost])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested Answered SessionClosed
                                   SessionExported Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   SessionNotFound SessionIdInUse TurnInFlight AttachmentRefused NoTurnInFlight
                                   UnknownTurn NoSuchRequest])
(import doeff_claude_code.faults [ClaudeDropProcess])
(import tests.scenario_rules [CODEWORD EXTRA-WORD remember-prompt recall-prompt reply-prompt sleep-prompt
                              touch-prompt extra-prompt])
(import tests.scenario_steps [settings start read-to-end read-to-tool-start read-until-permission new-id typed kinds-of])

(setv LONG-SLEEP 40)


(deftest test-a-turn-completes-and-the-next-turn-restarts-the-process
  ;; 手番を始める・出来事を読む・死んだ(降りた)process の起こし直し: 1 手番目の後に process は降りる(手番ごとの process)。
  ;; 2 手番目は同じ ResumeSession を頼むだけで、会話の記憶(1 手番目の言葉)が続く。seq は会話の中で単調に増える。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv sid (new-id))
  (<- first (start (FreshSession sid) s.base (remember-prompt "ALPHA-1")))
  (assert (= first.session-id sid))
  (<- one (read-to-end first.turn s.turn-timeout))
  (assert (isinstance one.end Completed) (repr one.end))
  (assert (in "ALPHA-1" one.end.result-text) one.end.result-text)
  (assert (= (lfor kind (kinds-of one.lines Init) kind.session-id) [sid]))
  (<- status (ClaudeSessionStatus s.base.home s.base.cwd sid))
  (assert (isinstance status.state Idle) (repr status))
  (assert (isinstance status.transcript TranscriptPresent) (repr status))
  (<- second (start (ResumeSession sid) s.base (recall-prompt)))
  (assert (= second.turn (ClaudeTurn sid 2)))
  (<- two (read-to-end second.turn s.turn-timeout))
  (assert (isinstance two.end Completed) (repr two.end))
  (assert (in CODEWORD two.end.result-text) two.end.result-text)
  (assert (> (. (get two.lines 0) seq) (. (get one.lines -1) seq))))


(deftest test-input-added-to-a-running-turn-is-read-at-the-tool-boundary
  ;; 走っている手番に足す: 運命(InputFate)が started になり、手番は 1 つの終わり(Completed)で閉じる。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (<- started (start (FreshSession (new-id)) s.base (sleep-prompt 8 "SLEPT")))
  (<- _ (read-to-tool-start started.turn s.turn-timeout))
  (<- queued (ClaudeInjectInput started.turn (typed (extra-prompt) "inj-extra")))
  (assert (= queued (InputQueued "inj-extra")) (repr queued))
  (<- done (read-to-end started.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in (InputFate "inj-extra" "started") (kinds-of done.lines InputFate)) (repr (kinds-of done.lines InputFate)))
  (assert (in EXTRA-WORD done.end.result-text) done.end.result-text))


(deftest test-interrupt-ends-the-turn-as-interrupted-and-the-session-continues
  ;; 止める(読まれていない注入が無い = SIGINT の形): 終わりは Interrupted(失敗ではない — claude 2.1.282 の SIGINT は
  ;; error_during_execution / aborted_streaming の result を出してから降りる。#603 の直し)。会話は ResumeSession で続く。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv sid (new-id))
  (<- started (start (FreshSession sid) s.base (sleep-prompt LONG-SLEEP "NEVER")))
  (<- _ (read-to-tool-start started.turn s.turn-timeout))
  (<- asked (ClaudeInterruptTurn started.turn))
  (assert (isinstance asked InterruptRequested) (repr asked))
  (<- stopped (read-to-end started.turn s.turn-timeout))
  (assert (= stopped.end (Interrupted)) (repr stopped.end))
  (<- after (start (ResumeSession sid) s.base (reply-prompt "AFTER")))
  (<- done (read-to-end after.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in "AFTER" done.end.result-text) done.end.result-text))


(deftest test-interrupt-with-unread-input-hands-the-input-to-the-next-turn
  ;; 足してから止める(control_request の形): 今の手番は Interrupted で終わり、生き残った入力が次の手番(continued-by)で走る。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv sid (new-id))
  (<- started (start (FreshSession sid) s.base (sleep-prompt LONG-SLEEP "NEVER")))
  (<- _ (read-to-tool-start started.turn s.turn-timeout))
  (<- _ (ClaudeInjectInput started.turn (typed (extra-prompt) "inj-survivor")))
  (<- asked (ClaudeInterruptTurn started.turn))
  (assert (isinstance asked InterruptRequested) (repr asked))
  (<- stopped (read-to-end started.turn s.turn-timeout))
  (assert (isinstance stopped.end Interrupted) (repr stopped.end))
  (assert (= stopped.end.surviving-refs #("inj-survivor")) (repr stopped.end))
  (assert (= stopped.end.continued-by (ClaudeTurn sid 2)) (repr stopped.end))
  (<- next-turn (read-to-end stopped.end.continued-by s.turn-timeout))
  (assert (isinstance next-turn.end Completed) (repr next-turn.end))
  (assert (in EXTRA-WORD next-turn.end.result-text) next-turn.end.result-text))


(deftest test-a-permission-question-is-answered-by-the-host
  ;; 許可の問いに答える: AskHost の会話は道具の前に PermissionRequested を出し、答えるまで道具は走らない。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv spec (replace s.base :permission (AskHost)))
  (setv target (os.path.join s.work-dir "perm.txt"))
  (<- started (start (FreshSession (new-id)) spec (touch-prompt target "TOUCHED")))
  (<- asked (read-until-permission started.turn s.turn-timeout))
  (assert (= asked.tool-name "Bash") (repr asked))
  (<- wrong (ClaudeAnswerPermission started.turn "no-such-request" (Allow)))
  (assert (= wrong (NoSuchRequest "no-such-request")) (repr wrong))
  (<- answered (ClaudeAnswerPermission started.turn asked.request-id (Allow)))
  (assert (isinstance answered Answered) (repr answered))
  (<- done (read-to-end started.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in "TOUCHED" done.end.result-text) done.end.result-text))


(deftest test-closing-a-session-is-idempotent-and-the-session-can-be-resumed
  ;; 閉じる: 走っている手番は Interrupted で終わる・2 度目は was-running 偽・閉じた会話も transcript が在れば ResumeSession で続く。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv sid (new-id))
  (<- started (start (FreshSession sid) s.base (sleep-prompt LONG-SLEEP "NEVER")))
  (<- _ (read-to-tool-start started.turn s.turn-timeout))
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (= closed (SessionClosed True)) (repr closed))
  (<- ended (read-to-end started.turn s.turn-timeout))
  (assert (isinstance ended.end Interrupted) (repr ended.end))
  (<- again (ClaudeCloseSession sid "test"))
  (assert (= again (SessionClosed False)) (repr again))
  (<- status (ClaudeSessionStatus s.base.home s.base.cwd sid))
  (assert (isinstance status.state Closed) (repr status))
  (<- resumed (start (ResumeSession sid) s.base (reply-prompt "REOPENED")))
  (<- done (read-to-end resumed.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (<- after (ClaudeSessionStatus s.base.home s.base.cwd sid))
  (assert (isinstance after.state Idle) (repr after)))


(deftest test-a-process-lost-mid-turn-ends-as-backend-lost-and-resume-works
  ;; 手番の途中に process が消えた: 終わりは BackendLost。上の層は同じ ResumeSession を頼むだけ(起こし直しは handler の中)。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv sid (new-id))
  (<- started (start (FreshSession sid) s.base (sleep-prompt LONG-SLEEP "NEVER")))
  (<- _ (read-to-tool-start started.turn s.turn-timeout))
  (<- dropped (ClaudeDropProcess sid))
  (assert (is dropped True))
  (<- lost (read-to-end started.turn s.turn-timeout))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (<- back (start (ResumeSession sid) s.base (reply-prompt "BACK")))
  (<- done (read-to-end back.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in "BACK" done.end.result-text) done.end.result-text))


(deftest test-refusals-come-back-as-types
  ;; 断りの型: 知らない会話の ResumeSession・既に在る FreshSession の id・走っている会話への 2 つ目の手番・手番の外の足す / 止める・
  ;; 知らない手番の読み・受けない添付。
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv missing (new-id))
  (<- not-found (ClaudeStartTurn (ResumeSession missing) s.base (typed (reply-prompt "X"))))
  (assert (= not-found (SessionNotFound missing)) (repr not-found))
  (<- refused (ClaudeStartTurn (FreshSession (new-id)) s.base
                               (TurnInput "look" (new-id) #((ImageAttachment "image/bmp" "AAAA")))))
  (assert (= refused (AttachmentRefused "image/bmp")) (repr refused))
  (setv sid (new-id))
  (<- first (start (FreshSession sid) s.base (reply-prompt "FIRST")))
  (<- _ (read-to-end first.turn s.turn-timeout))
  (<- in-use (ClaudeStartTurn (FreshSession sid) s.base (typed (reply-prompt "X"))))
  (assert (= in-use (SessionIdInUse sid)) (repr in-use))
  (<- late-inject (ClaudeInjectInput first.turn (typed "late")))
  (assert (= late-inject (NoTurnInFlight sid)) (repr late-inject))
  (<- late-stop (ClaudeInterruptTurn first.turn))
  (assert (= late-stop (NoTurnInFlight sid)) (repr late-stop))
  (<- unknown (ClaudeReadTurnEvents (ClaudeTurn sid 99) -1 0.0))
  (assert (= unknown (UnknownTurn (ClaudeTurn sid 99))) (repr unknown))
  (<- long (start (ResumeSession sid) s.base (sleep-prompt LONG-SLEEP "NEVER")))
  (<- _ (read-to-tool-start long.turn s.turn-timeout))
  (<- busy (ClaudeStartTurn (ResumeSession sid) s.base (typed (reply-prompt "X"))))
  (assert (= busy (TurnInFlight long.turn)) (repr busy))
  (<- status (ClaudeSessionStatus s.base.home s.base.cwd sid))
  (assert (= status.state (TurnRunning long.turn)) (repr status))
  (<- _ (ClaudeCloseSession sid "test"))
  (<- absent (ClaudeSessionStatus s.base.home s.base.cwd missing))
  (assert (isinstance absent.transcript TranscriptAbsent) (repr absent)))


(deftest test-a-fork-gets-a-new-id-and-keeps-the-parent-memory
  {:interpreters ["fake" "stub" "real"]}
  (<- s (settings))
  (setv parent (new-id))
  (<- first (start (FreshSession parent) s.base (remember-prompt "ALPHA-1")))
  (<- _ (read-to-end first.turn s.turn-timeout))
  (<- forked (start (ForkSession parent) s.base (recall-prompt)))
  (assert (!= forked.session-id parent) (repr forked))
  (assert (= forked.turn (ClaudeTurn forked.session-id 1)) (repr forked))
  (<- done (read-to-end forked.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in CODEWORD done.end.result-text) done.end.result-text))


(deftest test-a-transcript-carried-from-another-home-is-resumed
  ;; 持ち込み(別の家の transcript を symlink で): 本物の claude は別の家の資格が要るので fake と替え玉だけ。
  {:interpreters ["fake" "stub"]}
  (<- s (settings))
  (setv sid (new-id))
  (setv other (replace s.base :home s.other-home))
  (<- first (start (FreshSession sid) other (remember-prompt "ALPHA-1")))
  (<- _ (read-to-end first.turn s.turn-timeout))
  (<- none (ClaudeStartTurn (ResumeSession sid) s.base (typed (recall-prompt))))
  (assert (= none (SessionNotFound sid)) (repr none))
  (<- carried (start (ResumeSession sid :carry (LinkFromHome s.other-home)) s.base (recall-prompt)))
  (<- done (read-to-end carried.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in CODEWORD done.end.result-text) done.end.result-text)
  (setv rebuilt (new-id))
  (<- written (start (ResumeSession rebuilt :carry (Rebuilt "{\"type\":\"user\",\"text\":\"earlier\"}\n")) s.base
                     (reply-prompt "REBUILT")))
  (<- again (read-to-end written.turn s.turn-timeout))
  (assert (isinstance again.end Completed) (repr again.end)))


(deftest test-an-exported-transcript-carried-into-an-empty-home-continues-the-session
  ;; 写しの往復: 家から transcript の写しを取り出し(ClaudeExportSession)、空の別の家へ ResumeSession(carry = Rebuilt(写し))で
  ;; 持ち込むと、会話の記憶が続く。写しが無い id は SessionNotFound。本物の claude は別の家の資格が要るので fake と替え玉だけ。
  {:interpreters ["fake" "stub"]}
  (<- s (settings))
  (val sid (new-id))
  (<- first (start (FreshSession sid) s.base (remember-prompt "ALPHA-1")))
  (<- _ (read-to-end first.turn s.turn-timeout))
  (<- exported (ClaudeExportSession s.base.home s.base.cwd sid))
  (assert (isinstance exported SessionExported) (repr exported))
  (val missing (new-id))
  (<- absent (ClaudeExportSession s.base.home s.base.cwd missing))
  (assert (= absent (SessionNotFound missing)) (repr absent))
  (val other (replace s.base :home s.other-home))
  (<- none (ClaudeStartTurn (ResumeSession sid) other (typed (recall-prompt))))
  (assert (= none (SessionNotFound sid)) (repr none))
  (<- carried (start (ResumeSession sid :carry (Rebuilt exported.jsonl-text)) other (recall-prompt)))
  (<- done (read-to-end carried.turn s.turn-timeout))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (in CODEWORD done.end.result-text) done.end.result-text)
  (<- again (ClaudeExportSession s.other-home s.base.cwd sid))
  (assert (.startswith again.jsonl-text exported.jsonl-text) (repr again)))
