;; thread/resume の答えが session の履歴で 200 MB を超える時の、本体(この検の process)の確保の検 — 替え玉の app-server
;; (stub_cli/codex_app_server.py)が履歴を 1 行で返す形を 2 つ見る(答えの形は録った codex 0.162.1 の物 — thread の id が頭に・履歴
;; turns が thread の最後の欄に在る)。
;;
;; - 要求が履歴を求めない(excludeTurns: true — 本物の codex はこの時 履歴を載せない)なら、既定の上限(64 MiB)のまま、行が上限に
;;   触れず、確保の最大が 50 MB 未満のまま launch が thread の id を返す。直す前の要求は excludeTurns を付けず、替え玉が 210 MB の
;;   履歴を 1 行で返して、確保の最大が上限(64 MiB)まで伸びた。
;; - この欄を無視する版の codex(替え玉の --ignore-exclude-turns)が同じ 1 行を返しても、上限が守る: 確保の最大が行の大きさの 1/10
;;   未満のまま、launch が thread の id を返し、その thread でターンが始まる。上限は検の大きさに合わせて小さく渡す — 確保の最大は
;;   上限で決まり、行の大きさでは決まらない事を見る(初めの上限 64 MiB は test_process.hy が見る)。直す前の読み手は同じ 1 行で RSS が
;;   1.72 GB(行の 8.2 倍)伸びた。
;;
;; 確保は process の RSS の最大で測る: 1 度目の起動で import と型の組み立てを済ませ、/proc/self/clear_refs で VmHWM を今の RSS へ
;; 戻してから、その後の VmHWM の伸びを読む(tracemalloc は free-threading の python で読み手の thread と時計の thread を止めた)。
;; 出自 = card ki-83f24000d86c(描き手の会話の resume の答え 1 行で、zeus の turn-host の本体が 27 GB に膨らんだ)と、その元を断つ
;; excludeTurns(同じ card の続き — 答えの履歴は本体が使わない)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import pathlib [Path])
(import sys)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_codex.values [CodexSessionSpec CodexHome ResumeThread CodexInput])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_codex.process [LINE-CAP-BYTES])
(import doeff_codex.handler [CodexHost codex-handler])
(import doeff_codex.effects [CodexStartTurn CodexCloseSession TurnStarted SessionClosed])
(import tests.interpreters [STUB-PATH])

(val HISTORY-BYTES 210000000)
(val SMALL-LINE-CAP (* 4 1024 1024))
;; 履歴を求めない要求で許す確保の最大の伸び(履歴を丸ごと読めば行の大きさ 210 MB を越え、上限で切っても 64 MiB まで伸びる)。
(val RESUME-GROWTH-LIMIT 50000000)
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


(defk resume-with-history [#^ tuple history-flags #^ int line-cap]
  {:pre [(: history-flags tuple) (: line-cap int)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "履歴を載せる替え玉(history-flags = 替え玉への引数)へ上限 line-cap の host で thread/resume を送り、ターンを始めるため。
   答えは #(ターンの始まり 確保の最大の伸び(bytes) 上限を超えて切った行の数 閉じた答え)。"
  (val warm-handlers [(sync-time-handler) (codex-handler (CodexHost #(sys.executable STUB-PATH) :launch-timeout 60.0
                                                                    :line-cap line-cap))])
  (val warmed (run (scheduled (with_handlers warm-handlers (CodexStartTurn (ResumeThread :thread-id WARM-THREAD) SPEC
                                                                           (CodexInput :text "hi"))))))
  (run (scheduled (with_handlers warm-handlers (CodexCloseSession WARM-THREAD "温めの終わり"))))
  (assert (isinstance warmed TurnStarted) warmed)
  (val host (CodexHost #(sys.executable STUB-PATH #* history-flags) :launch-timeout 60.0 :line-cap line-cap))
  (val handlers [(sync-time-handler) (codex-handler host)])
  (.write-text (Path "/proc/self/clear_refs") "5")
  (<- base (status-bytes "VmRSS"))
  (val started (run (scheduled (with_handlers handlers (CodexStartTurn (ResumeThread :thread-id THREAD) SPEC
                                                                       (CodexInput :text "hi"))))))
  (<- peak (status-bytes "VmHWM"))
  (val process (. (.runtime host THREAD) process))
  (val closed (run (scheduled (with_handlers handlers (CodexCloseSession THREAD "検の終わり")))))
  #(started (- peak base) process.cut-lines closed))


(deftest test-a-resume-request-that-asks-no-history-opens-the-thread-without-growing-the-host
  ;; 既定の上限のまま、本体の要求が excludeTurns: true を名乗るので、替え玉は履歴を載せず、答えは小さい 1 行。
  (<- #(started growth cut-lines closed) tuple (resume-with-history #("--resume-history-bytes" (str HISTORY-BYTES)) LINE-CAP-BYTES))
  (assert (isinstance started TurnStarted) started)
  (assert (= started.turn.thread-id THREAD) started)
  (assert (< growth RESUME-GROWTH-LIMIT) growth)
  ;; 上限に触れた行は 1 つも無い(履歴の 1 行は来ていない)。
  (assert (= cut-lines 0) cut-lines)
  (assert (isinstance closed SessionClosed) closed))


(deftest test-a-resume-answer-far-over-the-line-cap-opens-the-thread-without-growing-the-host
  ;; excludeTurns を無視する版の codex が 210 MB の 1 行を返しても、上限が確保を守る。
  (<- #(started growth cut-lines closed) tuple (resume-with-history #("--resume-history-bytes" (str HISTORY-BYTES) "--ignore-exclude-turns")
                                                              SMALL-LINE-CAP))
  ;; launch が答えの頭から thread の id を読み、その thread でターンが始まった。
  (assert (isinstance started TurnStarted) started)
  (assert (= started.turn.thread-id THREAD) started)
  ;; 本体の確保の最大は、行の大きさの 1/10 未満(履歴を丸ごと読めば行の大きさを越える)。
  (assert (< growth (/ HISTORY-BYTES 10)) growth)
  ;; 捨てたのは resume の答えの 1 行だけ。
  (assert (= cut-lines 1) cut-lines)
  (assert (isinstance closed SessionClosed) closed))
