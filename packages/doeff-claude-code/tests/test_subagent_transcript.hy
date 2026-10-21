;; subagent の transcript から subagent の呼びごとの消費を読む検(card ki-e798888dce4e)。
;;
;; 背景の task を止めた CLI(本番の起こし方)は subagent の呼びの行を stdout に出さないので、handler は subagent の task の報せの行で
;; その subagent の transcript を読み、呼びごとに畳んだ SubagentTranscript の行を手番の記録へ足す。読めない transcript は呼びを
;; 載せずに訳を載せ、log の行に残す。前半は transcript の読みの純関数、後半は替え玉の CLI(実物と同じく transcript を書く)で本番の
;; handler を回す。
(require doeff-hy.macros [deftest defk <- val])
(import datetime [datetime timezone])
(import json)
(import sys)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.effects [Listen SlogEffect])
(import doeff_core_effects.handlers [listen-handler slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession TurnInput])
(import doeff_claude_code.lines [BackendLost Completed SubagentTranscript SubagentTranscriptProblem TaskEvent ToolResult TurnResult
                                 classify-record subagent-transcript-of])
(import doeff_claude_code.effects [ClaudeStartTurn])
(import doeff_claude_code.faults [ClaudeDropProcess])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [SUBAGENT-TRANSCRIPT-LOG ClaudeCodeHost claude-code-handler])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [sleep-prompt])
(import tests.scenario_steps [TurnRecord read-to-end read-to-tool-start])


;; --- transcript の読み(純関数) ---------------------------------------------------------------------

(val AT "2026-10-21T09:34:23.797Z")
(val LATER "2026-10-21T09:34:24.100Z")

(defn line-of [record]
  (+ (json.dumps record) "\n"))

(defn assistant [message-id model usage at [content None]]
  ;; 実物(CLI 2.1.292 の subagent の transcript)と同じ形の assistant の行。
  {"type" "assistant" "isSidechain" True "agentId" "a1" "timestamp" at
   "message" {"id" message-id "model" model "role" "assistant" "content" (or content [{"type" "text" "text" "x"}]) "usage" usage}})

(val FIRST-USAGE {"input_tokens" 2 "cache_creation_input_tokens" 23566 "cache_read_input_tokens" 0 "output_tokens" 5})
(val FINAL-USAGE {"input_tokens" 2 "cache_creation_input_tokens" 23566 "cache_read_input_tokens" 0 "output_tokens" 209})
(val OTHER-USAGE {"input_tokens" 3 "cache_creation_input_tokens" 10 "cache_read_input_tokens" 23566 "output_tokens" 40})

(defn read-of [#^ bytes data [expected None]]
  (run (subagent-transcript-of "a1" "toolu_parent" data expected)))


(deftest test-the-calls-of-a-subagent-transcript-are-folded-per-api-call
  ;; 1 つの API の呼びは content の block ごとに同じ message.id の行を書き、出力の token は呼びの途中の値(実測 — zeus の本番の
  ;; transcript の 1 本目は output 5・後の行は 209)。呼びごとに 1 つへ畳み、usage と model は最後の行・刻は最初の行の物にする。
  ;; CLI が呼ばずに書いた行(model <synthetic>)・usage の無い行・assistant でない行は呼びに数えない。書かれた順を保つ。
  (val data (.encode (.join "" [(line-of {"type" "user" "timestamp" AT "message" {"role" "user" "content" "task"}})
                                (line-of (assistant "msg_1" "claude-opus-5-5" FIRST-USAGE AT))
                                (line-of (assistant "msg_1" "claude-opus-5-5" FINAL-USAGE LATER))
                                (line-of {"type" "attachment" "timestamp" LATER})
                                (line-of (assistant "msg_synthetic" "<synthetic>" {"input_tokens" 0 "output_tokens" 0} LATER))
                                (line-of (assistant "msg_2" "claude-haiku-5-5" OTHER-USAGE LATER))])
                     "utf-8"))
  (val read (read-of data))
  (assert (is read.problem None) (repr read))
  (assert (= #(read.agent-id read.parent-tool-use-id) #("a1" "toolu_parent")) (repr read))
  (assert (= (lfor call read.calls #(call.call-id call.model call.at))
             [#("msg_1" "claude-opus-5-5" (datetime.fromisoformat AT))
              #("msg_2" "claude-haiku-5-5" (datetime.fromisoformat LATER))])
          (repr read.calls))
  (val first (get read.calls 0))
  (assert (= #(first.usage.input-tokens first.usage.output-tokens first.usage.cache-creation-input-tokens
               first.usage.cache-read-input-tokens)
             #(2 209 23566 0))
          (repr first))
  (assert (is-not first.at.tzinfo None) (repr first)))

(deftest test-lines-without-a-call-id-are-each-their-own-call
  ;; message.id の無い行は畳まない(どの呼びと同じか分からない — 1 行を 1 回と数える)。
  (val bare (assistant "" "m" OTHER-USAGE AT))
  (val read (read-of (.encode (+ (line-of bare) (line-of bare)) "utf-8")))
  (assert (= (lfor call read.calls call.call-id) [None None]) (repr read)))

(deftest test-a-transcript-is-whole-when-its-last-call-totals-what-the-notification-reported
  ;; 報せの行の total_tokens は subagent の最後の呼びの 4 種 token の和(実測 2.1.292)。CLI は transcript を報せの後に書き足すので、
  ;; 最後の呼びの和が合う読みだけを書き切った読みとし、合わない読み(最後の呼びの行がまだ無い)は TRUNCATED で呼びを載せない。
  (val first-call (line-of (assistant "msg_1" "m" FINAL-USAGE AT)))
  (val second-call (line-of (assistant "msg_2" "m" OTHER-USAGE LATER)))
  (val whole (read-of (.encode (+ first-call second-call) "utf-8") (+ 3 10 23566 40)))
  (assert (= #((len whole.calls) whole.problem) #(2 None)) (repr whole))
  (val cut-short (read-of (.encode first-call "utf-8") (+ 3 10 23566 40)))
  (assert (= #(cut-short.calls cut-short.problem) #(#() SubagentTranscriptProblem.TRUNCATED)) (repr cut-short))
  (assert (in "23619" cut-short.detail) (repr cut-short))
  (val nothing-yet (read-of b"" 5))
  (assert (= #(nothing-yet.calls nothing-yet.problem) #(#() SubagentTranscriptProblem.TRUNCATED)) (repr nothing-yet)))

(deftest test-an-empty-read-has-no-calls-and-no-problem
  ;; 前の報せの後に伸びた分が無い読み(続きの報せ)は、呼びの無い読み(読めなかった事ではない)。
  (val read (read-of b""))
  (assert (= #(read.calls read.problem) #(#() None)) (repr read)))

(deftest test-an-unreadable-transcript-carries-no-calls-and-names-why
  ;; 失敗ケース: 最後の行が途中で切れた・途中の行が JSON でない・UTF-8 でない・呼びの行が時差つきの刻を名乗らない transcript は、
  ;; 途中まで読めた呼びも載せず(呼びを発明しない)、読めなかった訳を載せる。
  (val good (line-of (assistant "msg_1" "m" FINAL-USAGE AT)))
  (val cases [#((.encode (+ good (.rstrip good "\n")) "utf-8") SubagentTranscriptProblem.TRUNCATED)
              #((.encode (+ good "{\"type\": \"assistant\", \"cut\n" good) "utf-8") SubagentTranscriptProblem.UNREADABLE)
              #((+ (.encode good "utf-8") b"\xff\xfe\n") SubagentTranscriptProblem.UNREADABLE)
              #((.encode (line-of (assistant "msg_1" "m" FINAL-USAGE "2026-10-21T09:34:23")) "utf-8") SubagentTranscriptProblem.UNREADABLE)
              #((.encode (line-of (assistant "msg_1" "m" FINAL-USAGE "")) "utf-8") SubagentTranscriptProblem.UNREADABLE)])
  (for [#(data problem) cases]
    (val read (read-of data))
    (assert (= #(read.calls read.problem) #(#() problem)) (repr #(data read)))
    (assert read.detail (repr read))))

(deftest test-the-task-lines-name-the-subagent-and-its-parent-call
  ;; 実物(CLI 2.1.292・本番と同じ引数で偽の API へ向けた実測)の subagent の task の始まりと報せの行: task の id(= transcript の file の
  ;; 名の id)・親の道具の呼びの id・始まりの task_type local_agent を読む。
  (val started (classify-record {"type" "system" "subtype" "task_started" "task_id" "a6f7c8296c8db9f1e" "tool_use_id" "toolu_main1"
                                 "description" "probe" "subagent_type" "general-purpose" "is_backgrounded" False "spawn_depth" 1
                                 "task_type" "local_agent" "prompt" "say hi"}))
  (assert (= started (TaskEvent "a6f7c8296c8db9f1e" "started" :tool-use-id "toolu_main1" :task-type "local_agent")) (repr started))
  (val notified (classify-record {"type" "system" "subtype" "task_notification" "task_id" "a6f7c8296c8db9f1e"
                                  "tool_use_id" "toolu_main1" "status" "completed" "summary" "hi"
                                  "usage" {"total_tokens" 1133 "tool_uses" 0 "duration_ms" 156}}))
  (assert (= notified (TaskEvent "a6f7c8296c8db9f1e" "completed" :tool-use-id "toolu_main1" :total-tokens 1133)) (repr notified)))


;; --- 本番の handler(替え玉の CLI) ------------------------------------------------------------------

(val STUB-COMMAND #(sys.executable "-m" "hy" STUB-PATH))

(defn spec-with [tmp-path [shape None]]
  ;; 替え玉の CLI の会話の宣言。shape = 替え玉に書かせる読めない transcript の形(env STUB_CLI_SUBAGENT_TRANSCRIPT・None = 読める)。
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv env (| (child-env "") (if (is shape None) {} {"STUB_CLI_SUBAGENT_TRANSCRIPT" shape})))
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) env) :cwd (str work) :settings {"disableAllHooks" True}))

(defn host-of []
  (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) 8 7200.0 :launch-timeout 30.0))

(defk tool-turn [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "道具の途中に subagent を 1 つ走らせる手番を最後まで読むため。"
  (<- started (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) spec (TurnInput (sleep-prompt 1 "TOOLED") (str (uuid.uuid4)))))
  (<- done (read-to-end started.turn 30.0))
  done)

(defn subagent-lines [#^ TurnRecord record]
  (lfor line record.lines :if (isinstance line.kind SubagentTranscript) line))


(deftest test-a-subagent-call-is-read-from-its-transcript-before-the-turn-result [tmp-path]
  ;; 替え玉の CLI は本番の CLI と同じく subagent の呼びの行を stdout に出さず、transcript に書く。handler は subagent の task の報せの
  ;; 後の CLI の手番の終わり(result の行)の直前に SubagentTranscript の行を 1 つ足し、その呼び(実 model・4 種 token — 出力は最後の
  ;; 値・呼びの id・親の道具の呼び・呼びの刻)を運ぶ。失敗ケース = 変更前は subagent の呼びがどの行にも無い。
  (<- done (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler (host-of))] (tool-turn (spec-with tmp-path))))
  (assert (isinstance done.end Completed) (repr done.end))
  (val found (subagent-lines done))
  (assert (= (len found) 1) (repr done.lines))
  (val line (get found 0))
  (val kinds (lfor other done.lines other.kind))
  (val at (.index kinds line.kind))
  (assert (isinstance (get kinds (+ at 1)) TurnResult) (repr kinds))
  (val notified (lfor #(index kind) (enumerate kinds) :if (and (isinstance kind TaskEvent) (= kind.status "completed")) index))
  (assert (and notified (< (get notified 0) at)) (repr kinds))
  (assert (any (gfor kind (cut kinds 0 at) (isinstance kind ToolResult))) (repr kinds))
  (val transcript line.kind)
  (assert (= #(transcript.parent-tool-use-id transcript.problem line.raw) #("toolu_stub" None "")) (repr transcript))
  (assert (= (len transcript.calls) 1) (repr transcript))
  (val call (get transcript.calls 0))
  (assert (= #(call.model call.usage.input-tokens call.usage.output-tokens call.usage.cache-creation-input-tokens
               call.usage.cache-read-input-tokens)
             #("claude-stub-sub" 1 900 0 50000))
          (repr call))
  (assert (.startswith call.call-id "msg_stub_sub_") (repr call))
  (assert (= call.at.tzinfo timezone.utc) (repr call)))


(deftest test-an-unreadable-subagent-transcript-carries-no-calls-and-leaves-a-log-line [tmp-path]
  ;; 失敗ケース: transcript が無い・途中で切れた・途中の行が壊れた時は、呼びを発明せず(calls は空)、訳を行に載せ、上の層へ渡す所で
  ;; log の行(SUBAGENT-TRANSCRIPT-LOG・event unread)に訳と手がかりを残す。無い・切れた transcript は CLI が書き終えるのを待つ上限
  ;; (SUBAGENT-SETTLE-SECONDS)まで読み直してから訳を載せる。手番そのものは成功のまま。
  (for [#(shape problem) [#("absent" SubagentTranscriptProblem.ABSENT) #("truncated" SubagentTranscriptProblem.TRUNCATED)
                          #("broken" SubagentTranscriptProblem.UNREADABLE)]]
    (val case-path (/ tmp-path shape))
    (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler (host-of))]
                (Listen (tool-turn (spec-with case-path shape)) :types #(SlogEffect))))
    (val done (get heard 0))
    (assert (isinstance done.end Completed) (repr done.end))
    (val found (subagent-lines done))
    (assert (= (lfor line found #(line.kind.calls line.kind.problem)) [#(#() problem)]) (repr found))
    (val logged (lfor effect (get heard 1) :if (= effect.msg SUBAGENT-TRANSCRIPT-LOG) effect.kwargs))
    (assert (= (lfor kwargs logged #((.get kwargs "event") (.get kwargs "problem") (.get kwargs "parent_tool_use_id")))
               [#("unread" problem.value "toolu_stub")])
            (repr logged))
    (assert (.get (get logged 0) "detail") (repr logged))))


(defk dropped-mid-subagent [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "subagent を走らせている道具の途中(task の報せの前)で process を SIGKILL で消し、手番を最後まで読むため。"
  (val sid (str (uuid.uuid4)))
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- (ClaudeDropProcess sid))
  (<- lost (read-to-end started.turn 30.0))
  lost)


(deftest test-a-subagent-whose-process-went-down-before-the-notification-is-read-at-the-exit [tmp-path]
  ;; 報せの行の前に process が降りた subagent も、その transcript を手番の終わりの前に読む(降りる前に subagent が使った分も記録に
  ;; 残す)。
  (<- lost (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler (host-of))]
             (dropped-mid-subagent (spec-with tmp-path))))
  (assert (isinstance lost.end BackendLost) (repr lost.end))
  (val found (subagent-lines lost))
  (assert (= (lfor line found #(line.kind.problem (len line.kind.calls) line.kind.parent-tool-use-id)) [#(None 1 "toolu_stub")])
          (repr lost.lines)))
