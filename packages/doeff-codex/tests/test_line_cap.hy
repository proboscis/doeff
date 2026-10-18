;; stdout の 1 行の上限の検 — 替え玉の app-server(stub_cli/codex_app_server.py)が thread/resume の答えに session の履歴を載せて
;; 200 MB を超える 1 行で返しても、本体(この検の process)の確保の最大が行の大きさの 1/10 未満のまま、launch が thread の id を返し、
;; その thread でターンが始まる事。答えの形は録った codex 0.162.1 の物(thread の id が頭に・履歴 turns が thread の最後の欄に在る)。
;; 確保は process の RSS の最大で測る: 1 度目の起動で import と型の組み立てを済ませ、/proc/self/clear_refs で VmHWM を今の RSS へ
;; 戻してから、その後の VmHWM の伸びを読む(tracemalloc は free-threading の python で読み手の thread と時計の thread を止めた)。
;; 上限は検の大きさに合わせて小さく渡す — 確保の最大は上限で決まり、行の大きさでは決まらない事を見る(初めの上限 64 MiB は
;; test_process.hy が見る)。直す前の読み手は同じ 1 行で RSS が 1.72 GB(行の 8.2 倍)伸びた。
;; 出自 = card ki-83f24000d86c(描き手の会話の resume の答え 1 行で、zeus の turn-host の本体が 27 GB に膨らんだ)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import pathlib [Path])
(import sys)
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
(val WARM-THREAD "thread-to-warm-up")
(val THREAD "thread-with-a-long-history")
(val TMP "/tmp")
(val SPEC (CodexSessionSpec :home (CodexHome :env {"PATH" "/usr/bin:/bin" "HOME" TMP "PYTHONDONTWRITEBYTECODE" "1" "CODEX_HOME" TMP})
                            :cwd TMP :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY))


(defk status-bytes [#^ str field]
  {:pre [(: field str)] :post [(: % int)] :tags {:context "codex-test" :role "foundation"}}
  "この process の memory の欄(/proc/self/status の VmRSS・VmHWM — kB で書かれる)を bytes で読むため。"
  (val line (next (gfor line (.splitlines (.read-text (Path "/proc/self/status"))) :if (.startswith line (+ field ":")) line)))
  (* 1024 (int (get (.split line) 1))))


(deftest test-a-resume-answer-far-over-the-line-cap-opens-the-thread-without-growing-the-host
  (val warm-handlers [(sync-time-handler) (codex-handler (CodexHost #(sys.executable STUB-PATH) :launch-timeout 60.0
                                                                    :line-cap LINE-CAP))])
  (val warmed (run (scheduled (with_handlers warm-handlers (CodexStartTurn (ResumeThread :thread-id WARM-THREAD) SPEC
                                                                           (CodexInput :text "hi"))))))
  (run (scheduled (with_handlers warm-handlers (CodexCloseSession WARM-THREAD "温めの終わり"))))
  (assert (isinstance warmed TurnStarted) warmed)
  (val host (CodexHost #(sys.executable STUB-PATH "--resume-history-bytes" (str HISTORY-BYTES)) :launch-timeout 60.0
                       :line-cap LINE-CAP))
  (val handlers [(sync-time-handler) (codex-handler host)])
  (.write-text (Path "/proc/self/clear_refs") "5")
  (<- base (status-bytes "VmRSS"))
  (val started (run (scheduled (with_handlers handlers (CodexStartTurn (ResumeThread :thread-id THREAD) SPEC
                                                                       (CodexInput :text "hi"))))))
  (<- peak (status-bytes "VmHWM"))
  (val process (. (.runtime host THREAD) process))
  (val closed (run (scheduled (with_handlers handlers (CodexCloseSession THREAD "検の終わり")))))
  ;; launch が答えの頭から thread の id を読み、その thread でターンが始まった。
  (assert (isinstance started TurnStarted) started)
  (assert (= started.turn.thread-id THREAD) started)
  ;; 本体の確保の最大は、行の大きさの 1/10 未満(履歴を丸ごと読めば行の大きさを越える)。
  (assert (< (- peak base) (/ HISTORY-BYTES 10)) #(base peak))
  ;; 捨てたのは resume の答えの 1 行だけ。
  (assert (= process.cut-lines 1) process.cut-lines)
  (assert (isinstance closed SessionClosed) closed))
