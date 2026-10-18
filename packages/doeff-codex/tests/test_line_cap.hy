;; stdout の 1 行の上限の検 — 替え玉の app-server(stub_cli/codex_app_server.py)が thread/resume の答えに session の履歴を載せて
;; 200 MB を超える 1 行で返しても、本体(この検の process)の確保の最大が行の大きさの 1/10 未満のまま、launch が thread の id を返し、
;; その thread でターンが始まる事。答えの形は録った codex 0.162.1 の物(thread の id が頭に・履歴 turns が thread の最後の欄に在る)。
;; 確保は tracemalloc で測る(読み手の thread を含む python の確保の全部)。上限は検の大きさに合わせて小さく渡す — 確保の最大は
;; 上限で決まり、行の大きさでは決まらない事を見る(初めの上限 64 MiB は test_process.hy が見る)。
;; 出自 = card ki-83f24000d86c(描き手の会話の resume の答え 1 行で、zeus の turn-host の本体が 27 GB に膨らんだ)。
(require doeff-hy.macros [deftest val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import sys)
(import tracemalloc)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_codex.values [CodexSessionSpec CodexHome ResumeThread CodexInput])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_codex.handler [CodexHost codex-handler])
(import doeff_codex.effects [CodexStartTurn CodexCloseSession TurnStarted SessionClosed])
(import tests.interpreters [STUB-PATH])

(val HISTORY-BYTES 210000000)
(val LINE-CAP (* 4 1024 1024))
(val THREAD "thread-with-a-long-history")
(val TMP "/tmp")
(val SPEC (CodexSessionSpec :home (CodexHome :env {"PATH" "/usr/bin:/bin" "HOME" TMP "PYTHONDONTWRITEBYTECODE" "1" "CODEX_HOME" TMP})
                            :cwd TMP :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY))


(deftest test-a-resume-answer-far-over-the-line-cap-opens-the-thread-without-growing-the-host
  (val host (CodexHost #(sys.executable STUB-PATH "--resume-history-bytes" (str HISTORY-BYTES)) :launch-timeout 60.0
                       :line-cap LINE-CAP))
  (val handlers [(sync-time-handler) (codex-handler host)])
  (tracemalloc.start)
  (try
    (val base (get (tracemalloc.get-traced-memory) 0))
    (tracemalloc.reset-peak)
    (val started (run (scheduled (with_handlers handlers (CodexStartTurn (ResumeThread :thread-id THREAD) SPEC (CodexInput :text "hi"))))))
    (val grown (- (get (tracemalloc.get-traced-memory) 1) base))
    (finally
      (tracemalloc.stop)))
  (val process (. (.runtime host THREAD) process))
  (val closed (run (scheduled (with_handlers handlers (CodexCloseSession THREAD "検の終わり")))))
  ;; launch が答えの頭から thread の id を読み、その thread でターンが始まった。
  (assert (isinstance started TurnStarted) started)
  (assert (= started.turn.thread-id THREAD) started)
  ;; 本体の確保の最大は、行の大きさの 1/10 未満(履歴を丸ごと読めば行の大きさを越える)。
  (assert (< grown (/ HISTORY-BYTES 10)) grown)
  ;; 捨てたのは resume の答えの 1 行だけ。
  (assert (= process.cut-lines 1) process.cut-lines)
  (assert (isinstance closed SessionClosed) closed))
