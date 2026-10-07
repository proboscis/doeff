;; argv の組み立て・「手番を始める」の判断・行の分類の検 — 純関数だけ。
(require doeff-hy.macros [deftest val <-])
(import json)
(import pytest)
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn BypassAll AskHost DenyUnlisted HomeSettings McpSse McpStdio
                                  AutocompactAuto AutocompactTokens FreshSession ResumeSession ForkSession TurnInput])
(import doeff_claude_code.argv [launch-argv launch-key cold-resume-argv transcript-path process-env])
(import doeff_claude_code.decision [SessionView Refuse Reuse Launch start-decision])
(import doeff_claude_code.effects [SessionIdInUse SessionNotFound TurnInFlight])
(import doeff_claude_code.lines [classify-record parse-record Init AssistantMessage ToolCall ToolResult InputFate PermissionRequested TaskEvent
                                 RateLimit TurnResult PartialMessage Other ControlResponse Usage StopHookFeedback recorded-cost])
(import doeff_claude_code.values [Allow])
(import doeff_claude_code [lines])

(setv SID "560828de-2992-4635-ab21-c6e06b0c6eb8")
(setv HOME (ClaudeHome "/h/.claude" {"PATH" "/bin"}))


(deftest test-the-launch-argv
  (setv spec (ClaudeSessionSpec :home HOME :cwd "/w" :model "haiku" :effort "low"
                                :settings {"disableAllHooks" True "env" {"A" "1"}}
                                :mcp-servers {"s" (McpSse "http://x")}
                                :autocompact (AutocompactTokens 400000)))
  (setv argv (launch-argv #("claude") spec (FreshSession SID)))
  (assert (= (cut argv 0 8) ["claude" "-p" "--input-format" "stream-json" "--output-format" "stream-json" "--verbose"
                             "--include-partial-messages"]))
  (assert (= (cut argv 8 9) ["--dangerously-skip-permissions"]))
  (setv settings (json.loads (get argv (+ (.index argv "--settings") 1))))
  ;; 手番の外で CLI に仕事をさせない handler の物理: background の仕事を持たせない(宣言の env と合流する)。
  (assert (= settings {"disableAllHooks" True "env" {"A" "1" "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" "1"}}))
  (assert (= (get argv (+ (.index argv "--model") 1)) "haiku"))
  (assert (= (get argv (+ (.index argv "--autocompact") 1)) "400000"))
  (assert (in "--strict-mcp-config" argv))
  (assert (= (cut argv -2 None) ["--session-id" SID]))
  (assert (= (cut (launch-argv #("claude") spec (ResumeSession SID)) -2 None) ["--resume" SID]))
  (assert (= (cut (launch-argv #("claude") spec (ForkSession SID)) -3 None) ["--resume" SID "--fork-session"])))


(deftest test-the-launch-argv-takes-monitor-out-of-the-tools
  ;; 守り(#3672・#517 の事故の形): 背景の仕事を消す環境変数は Monitor を残す(2.1.289 の実測)ので、Monitor は起こす引数で外す。
  ;; 名簿の許可(DenyUnlisted の --allowedTools)と重ねても外れる形(--disallowedTools は許可より先に効く)。
  (for [policy [(BypassAll) (AskHost) (DenyUnlisted #("Read" "Monitor")) (HomeSettings)]]
    (val argv (launch-argv #("claude") (ClaudeSessionSpec :home HOME :cwd "/w" :permission policy) (FreshSession SID)))
    (assert (in "--disallowedTools" argv) argv)
    (assert (in "Monitor" (.split (get argv (+ (.index argv "--disallowedTools") 1)) ",")) argv)))


(deftest test-the-launch-key-follows-the-launch-conditions
  ;; 起こした時の条件の鍵(#3672): argv(会話の始まり方を除く)・cwd・env が同じなら同じ鍵、どれかが違えば違う鍵。資格の値は鍵に
  ;; 文字として残らない(指紋だけ)。
  (val base (ClaudeSessionSpec :home (ClaudeHome "/h/.claude" {"CLAUDE_CODE_OAUTH_TOKEN" "secret-token-1"}) :cwd "/w" :model "opus"))
  (<- same (launch-key #("claude") base))
  (<- again (launch-key #("claude") (ClaudeSessionSpec :home base.home :cwd "/w" :model "opus")))
  (assert (= same again))
  (assert (not-in "secret-token-1" same))
  (for [other [(ClaudeSessionSpec :home base.home :cwd "/w" :model "haiku")
               (ClaudeSessionSpec :home base.home :cwd "/w2" :model "opus")
               (ClaudeSessionSpec :home (ClaudeHome "/h/.claude" {"CLAUDE_CODE_OAUTH_TOKEN" "secret-token-2"}) :cwd "/w" :model "opus")
               (ClaudeSessionSpec :home base.home :cwd "/w" :model "opus" :effort "high")]]
    (<- differs (launch-key #("claude") other))
    (assert (!= same differs) other))
  (<- other-command (launch-key #("claude-2") base))
  (assert (!= same other-command)))


(deftest test-the-launch-argv-never-inherits-home-mcp
  ;; 手番の MCP は宣言の 1 点が正: 宣言が無い時も --strict-mcp-config を付け(家の MCP と claude.ai の connector を
  ;; 継がせない — 起動が約 0.6〜0.8 秒縮む)、--mcp-config は付けない。宣言が有る時は宣言した server だけを渡して同じ旗を付ける。
  (val bare (launch-argv #("claude") (ClaudeSessionSpec :home HOME :cwd "/w") (FreshSession SID)))
  (val declared (launch-argv #("claude") (ClaudeSessionSpec :home HOME :cwd "/w" :mcp-servers {"s" (McpSse "http://x")})
                              (FreshSession SID)))
  (assert (= (.count bare "--strict-mcp-config") 1) bare)
  (assert (not-in "--mcp-config" bare) bare)
  (assert (= (.count declared "--strict-mcp-config") 1) declared)
  (assert (= (json.loads (get declared (+ (.index declared "--mcp-config") 1))) {"mcpServers" {"s" {"type" "sse" "url" "http://x"}}})
          declared)
  ;; 冷えた続きの前の 1 回きりの process も同じ基礎の旗を通る。
  (assert (in "--strict-mcp-config" (cold-resume-argv #("claude") (ClaudeSessionSpec :home HOME :cwd "/w" :cold-resume-prompt "/x") SID))))


(deftest test-permission-flags
  (defn flags [policy] (launch-argv #("c") (ClaudeSessionSpec :home HOME :cwd "/w" :permission policy) (FreshSession SID)))
  (assert (in "--dangerously-skip-permissions" (flags (BypassAll))))
  (setv ask (flags (AskHost)))
  (assert (and (in "--permission-prompt-tool" ask) (not-in "--permission-mode" ask)
               (not-in "--dangerously-skip-permissions" ask)))
  (assert (= (get (setx plan (flags (AskHost "plan"))) (+ (.index plan "--permission-mode") 1)) "plan"))
  (setv deny (flags (DenyUnlisted #("Read" "Grep"))))
  (assert (= (get deny (+ (.index deny "--allowedTools") 1)) "Read,Grep"))
  (assert (= (get deny (+ (.index deny "--permission-prompts") 1)) "none")))


(deftest test-home-settings-puts-no-permission-flag
  ;; #3753: 許可を設定 dir(CLAUDE_CONFIG_DIR)の settings.json の permissions に任せる形(HomeSettings)は、起動の引数に
  ;; 許可の旗を 1 つも載せない(旗が在ると settings.json の permissions より旗が勝つ)。冷えた続きの前の 1 回きりの process も同じ。
  ;; 宣言の既定は BypassAll のまま(既存の使い手の振る舞いを変えない)。
  (val spec (ClaudeSessionSpec :home HOME :cwd "/w" :permission (HomeSettings) :cold-resume-prompt "/x"))
  (val launched (launch-argv #("claude") spec (FreshSession SID)))
  (val cold (cold-resume-argv #("claude") spec SID))
  (for [argv [launched cold]]
    (for [flag ["--dangerously-skip-permissions" "--permission-mode" "--permission-prompts" "--permission-prompt-tool"
                "--allowedTools"]]
      (assert (not-in flag argv) argv)))
  (assert (= (. (ClaudeSessionSpec :home HOME :cwd "/w") permission) (BypassAll))))


(deftest test-the-cold-resume-argv-drops-disable-all-hooks
  (setv spec (ClaudeSessionSpec :home HOME :cwd "/w" :settings {"disableAllHooks" True}
                                :cold-resume-prompt "/compact if-cold"))
  (setv argv (cold-resume-argv #("claude") spec SID))
  (assert (= (cut argv -4 None) ["-p" "/compact if-cold" "--resume" SID]))
  (assert (not-in "stream-json" argv))
  (assert (not-in "disableAllHooks" (get argv (+ (.index argv "--settings") 1)))))


(deftest test-the-transcript-place-and-the-process-env
  (assert (= (transcript-path "/h/.claude" "/tmp/work.dir" SID)
             (.format "/h/.claude/projects/-tmp-work-dir/{}.jsonl" SID)))
  ;; 計時の env(#3855)は家の env の後に足す — 起動と要求までの区間を CLI に名乗らせる handler の物理。
  (assert (= (process-env HOME) {"PATH" "/bin" "CLAUDE_CODE_EMIT_STARTUP_TIMING" "1" "CLAUDE_CONFIG_DIR" "/h/.claude"})))


(deftest test-values-refuse-what-the-cli-refuses
  (with [(pytest.raises ValueError)] (FreshSession "not-a-uuid"))
  (with [(pytest.raises ValueError)] (AutocompactTokens 80000))
  (with [(pytest.raises ValueError)] (AskHost "bypassPermissions"))
  (with [(pytest.raises ValueError)] (TurnInput "x" "")))


(deftest test-the-start-decision-table
  ;; 設計 7 節の表: 起こすか使い回すかの判断はここ 1 か所。続き(ResumeSession)は、生きて待つ process の起こした時の条件の鍵が
  ;; この手番の鍵と同じ時だけ使い回す(#3672)。違えば降ろしてから起こす。枝(ForkSession)・新しい会話は使い回さない。
  (setv turn (ClaudeTurn SID 3))
  (setv key "k1")
  (assert (= (start-decision (FreshSession SID) (SessionView) False False key) (Launch)))
  (assert (= (start-decision (FreshSession SID) (SessionView :known True) False False key) (Refuse (SessionIdInUse SID))))
  (assert (= (start-decision (FreshSession SID) (SessionView) True False key) (Refuse (SessionIdInUse SID))))
  (assert (= (start-decision (ResumeSession SID) (SessionView) True False key) (Launch)))
  (assert (= (start-decision (ResumeSession SID) (SessionView) False False key) (Refuse (SessionNotFound SID))))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :running-turn turn) True False key)
             (Refuse (TurnInFlight turn))))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :retiring True) True True key)
             (Launch :wait-retire True :cold-resume True)))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :idle-key key) True True key) (Reuse)))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :idle-key "k0") True True key)
             (Launch :wait-retire True :retire-idle True :cold-resume True)))
  (assert (= (start-decision (ForkSession SID) (SessionView :known True :idle-key key) True False key) (Launch)))
  (assert (= (start-decision (ForkSession SID) (SessionView :known True :running-turn turn) True False key) (Launch)))
  (assert (= (start-decision (ForkSession SID) (SessionView) False False key) (Refuse (SessionNotFound SID)))))


(deftest test-tool-use-blocks-keep-their-id-and-name-in-block-order
  ;; #3518: tool_use の block の id は、続く tool_result の tool_use_id と突き合わせる鍵。名だけ読む分類は赤。
  (val raw (json.dumps {"type" "assistant"
                        "message" {"content" [{"type" "tool_use" "id" "toolu_1" "name" "Bash" "input" {"command" "ls"}}
                                              {"type" "tool_use" "id" "toolu_2" "name" "Read" "input" {"file_path" "/a"}}]}}))
  (val kind (classify-record (parse-record raw)))
  (assert (isinstance kind AssistantMessage) (repr kind))
  (assert (= kind.tool-calls #((ToolCall "toolu_1" "Bash" {"command" "ls"}) (ToolCall "toolu_2" "Read" {"file_path" "/a"})))
          (repr kind)))

(deftest test-a-tool-use-block-without-an-id-is-refused-by-name
  ;; id の無い・空の tool_use の block を空の id の呼びとして通さない — 名指しの Other で断る。
  (for [block [{"type" "tool_use" "name" "Bash"} {"type" "tool_use" "id" "" "name" "Bash"}]]
    (assert (= (classify-record {"type" "assistant"
                                 "message" {"content" [{"type" "tool_use" "id" "toolu_1" "name" "Read"} block]}})
               (Other "assistant" "tool_use_without_id"))
            block))
  (assert (= (classify-record {"type" "assistant" "message" {"content" [{"type" "tool_use" "id" "toolu_1"}]}})
             (Other "assistant" "tool_use_without_name")))
  (with [(pytest.raises ValueError :match "ToolCall.id")]
    (ToolCall "" "Bash")))

(deftest test-tool-calls-keep-their-input-and-tool-results-keep-their-content
  ;; #3744: 会話の画面の道具の行を開くには、道具の呼びの命令(tool_use の block の input)と結果の中身(tool_result の
  ;; content と is_error)が記録まで届く事が要る。分類で input と content を捨てる形は赤。
  (val called (classify-record {"type" "assistant"
                                "message" {"content" [{"type" "tool_use" "id" "toolu_1" "name" "Bash"
                                                       "input" {"command" "ls -la" "timeout" 5 "flags" ["a" "b"]}}]}}))
  (assert (isinstance called AssistantMessage) (repr called))
  (val call (get called.tool-calls 0))
  ;; input は JSON の値のまま深く凍らせて持つ(配列は tuple)。作った後に書き換えられない。
  (assert (= call.input {"command" "ls -la" "timeout" 5 "flags" #("a" "b")}) (repr call))
  (with [(pytest.raises TypeError)] (setv (get call.input "command") "rm -rf /"))
  ;; tool_result の content は文字列のことも block の列のこともある。text の block は改行で連ね、text でない block(画像など)は
  ;; 捨てずに種類の名を non-text-kinds に残す。content の無い結果は空の本文。is_error が真の結果だけ is-error。
  (val answered (classify-record
                  {"type" "user"
                   "message" {"content" [{"type" "tool_result" "tool_use_id" "toolu_1" "content" "total 0\n" "is_error" False}
                                         {"type" "tool_result" "tool_use_id" "toolu_2" "content" "Exit code 2" "is_error" True}
                                         {"type" "tool_result" "tool_use_id" "toolu_3"
                                          "content" [{"type" "text" "text" "line 1"}
                                                     {"type" "image" "source" {"type" "base64" "media_type" "image/png" "data" "AA=="}}
                                                     {"type" "text" "text" "line 2"}]}
                                         {"type" "tool_result" "tool_use_id" "toolu_4"}]}}))
  (assert (isinstance answered ToolResult) (repr answered))
  (assert (= answered.answers #((lines.ToolAnswer "toolu_1" "total 0\n" False)
                                (lines.ToolAnswer "toolu_2" "Exit code 2" True)
                                (lines.ToolAnswer "toolu_3" "line 1\nline 2" False #("image"))
                                (lines.ToolAnswer "toolu_4" "" False)))
          (repr answered)))

(deftest test-a-tool-block-without-its-input-or-its-id-is-refused-by-name
  ;; input の無い・写像でない tool_use の block を空の命令の呼びとして通さない。tool_use_id の無い・空の tool_result を空の id の結果として
  ;; 通さない — どちらも名指しの Other で断る(#3744)。
  (for [block [{"type" "tool_use" "id" "toolu_2" "name" "Bash"} {"type" "tool_use" "id" "toolu_2" "name" "Bash" "input" "ls"}]]
    (assert (= (classify-record {"type" "assistant"
                                 "message" {"content" [{"type" "tool_use" "id" "toolu_1" "name" "Read" "input" {}} block]}})
               (Other "assistant" "tool_use_without_input"))
            block))
  (for [block [{"type" "tool_result" "content" "x"} {"type" "tool_result" "tool_use_id" "" "content" "x"}]]
    (assert (= (classify-record {"type" "user"
                                 "message" {"content" [{"type" "tool_result" "tool_use_id" "t1" "content" "ok"} block]}})
               (Other "user" "tool_result_without_id"))
            block))
  (with [(pytest.raises ValueError :match "ToolAnswer.id")]
    (lines.ToolAnswer "" "x" False)))

(deftest test-assistant-lines-keep-their-usage-model-and-parent-and-results-keep-the-model-windows
  ;; #3744: 会話の今の context の大きさを出すため、assistant の行の message.usage(名乗らない欄は None)・message.model・
  ;; parent_tool_use_id(null = 本体の会話・在れば subagent の行)と、result の行の modelUsage の model ごとの contextWindow・
  ;; maxOutputTokens を型へ載せる。読まずに捨てる分類は赤。
  (val main (classify-record {"type" "assistant" "parent_tool_use_id" None
                              "message" {"id" "msg_1" "model" "claude-opus-4-5" "content" [{"type" "text" "text" "a"}]
                                         "usage" {"input_tokens" 3 "cache_creation_input_tokens" 100
                                                  "cache_read_input_tokens" 2000 "output_tokens" 1}}}))
  (assert (= #(main.usage main.model main.parent-tool-use-id)
             #((Usage :input-tokens 3 :cache-creation-input-tokens 100 :cache-read-input-tokens 2000 :output-tokens 1)
               "claude-opus-4-5" None))
          (repr main))
  ;; usage の object が無い行は None(空の数を発明しない)・subagent の行は parent_tool_use_id を持つ。
  (val sub (classify-record {"type" "assistant" "parent_tool_use_id" "toolu_9"
                             "message" {"model" "claude-haiku-4-5" "content" [{"type" "text" "text" "b"}]}}))
  (assert (= #(sub.usage sub.model sub.parent-tool-use-id) #(None "claude-haiku-4-5" "toolu_9")) (repr sub))
  ;; modelUsage の model ごとの値を名のある値の列へ(並びは object の鍵の順・名乗らない欄は None)。
  (val result (classify-record {"type" "result" "subtype" "success" "is_error" False
                                "modelUsage" {"claude-opus-4-5" {"inputTokens" 3 "outputTokens" 1 "contextWindow" 200000
                                                                 "maxOutputTokens" 64000 "costUSD" 0.1}
                                              "claude-haiku-4-5" {"inputTokens" 5 "contextWindow" 200000}}}))
  (assert (= result.model-windows #((lines.ModelWindow "claude-opus-4-5" 200000 64000)
                                    (lines.ModelWindow "claude-haiku-4-5" 200000 None)))
          (repr result))
  (assert (= (. (classify-record {"type" "result" "subtype" "success" "is_error" False}) model-windows) #())))

(deftest test-stream-event-deltas-are-split-by-their-kind
  ;; #3746 (a): --include-partial-messages の stream_event の delta を種類の閉じた型(DeltaKind)で分けて読む — text_delta だけを本文の
  ;; 差分にして、ほかを本文の空の行に畳む分類では、考えている間(thinking_delta)と道具の命令を書いている間(input_json_delta)の行が
  ;; 数えられず赤。本文の差分の欄 text-delta は今のまま(text_delta の本文だけ)。
  (val read (lfor event [{"type" "content_block_delta" "index" 0 "delta" {"type" "text_delta" "text" "he"}}
                         {"type" "content_block_delta" "index" 0 "delta" {"type" "thinking_delta" "thinking" "hmm"}}
                         {"type" "content_block_delta" "index" 1 "delta" {"type" "input_json_delta" "partial_json" "{\"comm"}}
                         {"type" "content_block_delta" "index" 0 "delta" {"type" "signature_delta" "signature" "c2ln"}}
                         {"type" "message_delta" "delta" {"stop_reason" "end_turn"}}
                         {"type" "message_start" "message" {"role" "assistant" "content" []}}]
                  (classify-record {"type" "stream_event" "parent_tool_use_id" None "event" event})))
  (assert (= (lfor kind read #(kind.delta kind.text-delta))
             [#(lines.DeltaKind.TEXT "he") #(lines.DeltaKind.THINKING "") #(lines.DeltaKind.TOOL-INPUT "")
              #(lines.DeltaKind.OTHER "") #(lines.DeltaKind.NO-DELTA "") #(lines.DeltaKind.NO-DELTA "")])
          (repr read)))

(deftest test-a-thinking-delta-carries-its-text
  ;; #3789: 考えている間の差分(thinking_delta)は、種類 THINKING と一緒に考えの文字列を運ぶ — 上の層が「考えている」と
  ;; 分かる表示を、本文の前に画面へ出すため(前は種類だけで中身を落とし、ここで赤)。本文の差分の欄は空のまま。考えの文字列は種類
  ;; THINKING の行だけが持つ(作り手が種類を名乗り忘れた行は断る)。
  (val read (classify-record {"type" "stream_event" "event" {"delta" {"type" "thinking_delta" "thinking" "hmm"}}}))
  (assert (= #(read.delta read.thinking-delta read.text-delta) #(lines.DeltaKind.THINKING "hmm" "")) (repr read))
  (with [(pytest.raises ValueError)]
    (PartialMessage :delta lines.DeltaKind.TEXT :thinking-delta "hmm")))

(deftest test-a-tool-call-start-and-its-input-pieces-are-read
  ;; #3974 の 3(利用者 2026-10-07 14:2x「呼んでいると分からないといけない」): 担当が道具の命令を書いている間を画面に出す
  ;; ため、content_block_start の tool_use は呼びの id と道具の名(tool-start)を、input_json_delta は命令の切れ端(partial_json — JSON の
  ;; 途中で単独では読めない文字列)を運ぶ。前は種類 TOOL-INPUT だけで中身を落とし、道具の名も読まなかったので赤。
  (val start (classify-record {"type" "stream_event"
                               "event" {"type" "content_block_start" "index" 1
                                        "content_block" {"type" "tool_use" "id" "toolu_a" "name" "Bash" "input" {}}}}))
  (assert (= #(start.delta start.tool-start start.tool-input-delta) #(lines.DeltaKind.NO-DELTA (ToolCall "toolu_a" "Bash") ""))
          (repr start))
  (val piece (classify-record {"type" "stream_event"
                               "event" {"type" "content_block_delta" "index" 1
                                        "delta" {"type" "input_json_delta" "partial_json" "{\"comm"}}}))
  (assert (= #(piece.delta piece.tool-input-delta piece.tool-start) #(lines.DeltaKind.TOOL-INPUT "{\"comm" None)) (repr piece))
  ;; 本文の block の始まりは道具の始まりではない
  (val text-start (classify-record {"type" "stream_event"
                                    "event" {"type" "content_block_start" "index" 0 "content_block" {"type" "text" "text" ""}}}))
  (assert (is text-start.tool-start None) (repr text-start))
  ;; 命令の切れ端は種類 TOOL-INPUT の行だけが持つ
  (with [(pytest.raises ValueError)]
    (PartialMessage :delta lines.DeltaKind.TEXT :tool-input-delta "{")))

(deftest test-line-classification
  (assert (= (classify-record {"type" "system" "subtype" "init" "session_id" SID "capabilities" ["msg_lifecycle_v1"]
                               "model" "m" "permissionMode" "default" "mcp_servers" [{"name" "s"}]})
             (Init SID #("msg_lifecycle_v1") "m" "default" #("s"))))
  (assert (= (classify-record {"type" "assistant" "message" {"content" [{"type" "text" "text" "a"}
                                                                        {"type" "tool_use" "id" "toolu_a" "name" "Bash" "input" {}}]}})
             (AssistantMessage "a" #((ToolCall "toolu_a" "Bash")))))
  (assert (= (classify-record {"type" "user" "message" {"content" [{"type" "tool_result" "tool_use_id" "t1"}]}})
             (ToolResult #((lines.ToolAnswer "t1" "" False)))))
  (assert (= (classify-record {"type" "command_lifecycle" "command_uuid" "r" "state" "started"}) (InputFate "r" "started")))
  (assert (= (classify-record {"type" "command_lifecycle" "command_uuid" "r" "state" "weird"})
             (Other "command_lifecycle" "weird")))
  (assert (= (classify-record {"type" "control_request" "request_id" "q"
                               "request" {"subtype" "can_use_tool" "tool_name" "Bash" "input" {"command" "ls"}}})
             (PermissionRequested "q" "Bash" {"command" "ls"})))
  (assert (= (classify-record {"type" "system" "subtype" "task_notification" "task_id" "t" "status" "stopped"})
             (TaskEvent "t" "stopped")))
  (assert (= (classify-record {"type" "rate_limit_event"
                               "rate_limit_info" {"rateLimitType" "five_hour" "resetsAt" 1790348400
                                                  "unifiedWindows" {"five_hour" {"utilization" 0.07}}}})
             (RateLimit "five_hour" 0.07 1790348400)))
  (assert (= (classify-record {"type" "result" "subtype" "error_during_execution" "is_error" True
                               "terminal_reason" "aborted_streaming"})
             (TurnResult "error_during_execution" True "aborted_streaming" "")))
  (assert (= (classify-record {"type" "stream_event" "event" {"delta" {"type" "text_delta" "text" "x"}}})
             (PartialMessage "x" lines.DeltaKind.TEXT)))
  (assert (= (classify-record {"type" "brand_new" "subtype" "s"}) (Other "brand_new" "s")))
  (assert (= (classify-record {"type" "control_response"
                               "response" {"subtype" "success" "request_id" "rid" "response" {"still_queued" ["i1"]}}})
             (ControlResponse "rid" "success" #("i1"))))
  (assert (= (classify-record {"type" "result" "subtype" "success" "is_error" False "result" "ok" "total_cost_usd" 1
                               "api_error_status" None "user_message_uuids" ["m1"]
                               "usage" {"input_tokens" 3 "output_tokens" 5 "cache_creation_input_tokens" 11
                                        "cache_read_input_tokens" 13 "service_tier" "standard"
                                        "cache_creation" {"ephemeral_5m_input_tokens" 2 "ephemeral_1h_input_tokens" 9}
                                        "server_tool_use" {"web_search_requests" 1} "unknown_field" 99}})
             (TurnResult "success" False :result-text "ok" :cost-usd 1.0 :input-refs #("m1")
                         :usage (Usage :input-tokens 3 :output-tokens 5 :cache-creation-input-tokens 11
                                       :cache-read-input-tokens 13 :cache-creation-5m-input-tokens 2
                                       :cache-creation-1h-input-tokens 9 :web-search-requests 1
                                       :service-tier "standard")))))


(deftest test-the-cli-timing-fields-are-read-not-dropped
  ;; #3855: 送ってから最初の字までを、CLI の起動・要求を送るまで(入力ごとの hook を含む)・要求から message_start まで・モデルが
  ;; 考えた間に割るため、CLI(実測 2.1.292)が名乗る計時の欄を捨てずに型へ読む。行の形は実物の 1 通の写し(値は丸めた)。
  ;; 前は init の startup_timing・message_start の ttft_ms・result の time_to_request_ms などを捨てていて、ここで赤。
  (val init (classify-record {"type" "system" "subtype" "init" "session_id" SID
                              "startup_timing" {"phases" {"node_boot_ms" 790 "hooks_init_ms" 1103 "input_ready_ms" 2883}
                                                "phase_start_ms" {"node_boot_ms" 0 "hooks_init_ms" 1763}
                                                "time_origin_ms" 1791385479741.7954}}))
  (assert (= init.startup-phases #((lines.TimedPhase :name "node_boot_ms" :ms 790 :start-ms 0)
                                   (lines.TimedPhase :name "hooks_init_ms" :ms 1103 :start-ms 1763)
                                   (lines.TimedPhase :name "input_ready_ms" :ms 2883 :start-ms None)))
          (repr init))
  (assert (= init.startup-origin-ms 1791385479741) (repr init))
  (val start (classify-record {"type" "stream_event" "ttft_ms" 836 "event" {"type" "message_start" "message" {}}}))
  (assert (= start.ttft-ms 836) (repr start))
  (val result (classify-record {"type" "result" "subtype" "success" "is_error" False "duration_ms" 2025 "duration_api_ms" 1288
                                "ttft_ms" 1657 "ttft_stream_ms" 1117 "time_to_request_ms" 441 "first_content_frame_ms" 1119
                                "time_to_request_phases_ms" {"input_hooks" 252 "system_prompt" 36 "other" 153}}))
  (assert (= result.timing
             (lines.RequestTiming :time-to-request-ms 441 :ttft-stream-ms 1117 :first-content-frame-ms 1119 :ttft-ms 1657
                                  :duration-ms 2025 :duration-api-ms 1288
                                  :request-phases #((lines.TimedPhase :name "input_hooks" :ms 252)
                                                    (lines.TimedPhase :name "system_prompt" :ms 36)
                                                    (lines.TimedPhase :name "other" :ms 153))))
          (repr result))
  ;; 名乗らない欄は None・区間は空(0 を発明しない)— env の無い process の init と、計時の欄の無い result。
  (val bare-init (classify-record {"type" "system" "subtype" "init" "session_id" SID}))
  (assert (= #(bare-init.startup-phases bare-init.startup-origin-ms) #(#() None)) (repr bare-init))
  (val bare (classify-record {"type" "result" "subtype" "success" "is_error" False}))
  (assert (= bare.timing (lines.RequestTiming)) (repr bare)))


(deftest test-usage-adds-field-by-field-without-inventing-zero
  ;; 1 つの host の手番に result の行が 2 つある時の usage の和: 欄ごとに足し、どちらも名乗らない欄は None のまま、
  ;; 片方だけが名乗った欄はその数。service-tier は後の行の物(後の行が名乗らなければ前の行の物)。
  (val earlier (Usage :input-tokens 10 :output-tokens 54 :cache-creation-input-tokens 15322 :service-tier "standard"))
  (val later (Usage :input-tokens 10 :output-tokens 48 :cache-read-input-tokens 29011 :service-tier "priority"))
  (assert (= (+ earlier later)
             (Usage :input-tokens 20 :output-tokens 102 :cache-creation-input-tokens 15322 :cache-read-input-tokens 29011
                    :service-tier "priority")))
  (assert (= (. (+ earlier (Usage)) service-tier) "standard"))
  (assert (= (+ (Usage) (Usage)) (Usage)))
  (with [(pytest.raises TypeError)]
    (+ earlier 1)))


(deftest test-the-recorded-cost-is-the-last-cost-state-line
  ;; 続き・枝の CLI が数え始める額 = transcript の最後の cost-state の行の totalCostUSD(行の形は実測 2.1.283 の逐語から欄を抜いた)。
  ;; 本文に cost-state の語を含む発話の行は額の行ではない。
  (val sid "4fa6f85c-9739-49cf-b36d-802e36398843")
  (val records [{"type" "user" "message" {"role" "user" "content" "cost-state の話"}}
                {"type" "cost-state" "sessionId" sid "totalCostUSD" 0.035648 "modelUsage" {}}
                {"type" "assistant"}
                {"type" "cost-state" "sessionId" sid "totalCostUSD" 0.038911299999999996 "hasUnknownModelCost" False}
                {"type" "user" "message" {"role" "user" "content" "\"cost-state\" と書いた発話"}}])
  (<- found (recorded-cost (.join "\n" (gfor record records (json.dumps record :ensure-ascii False :separators #("," ":"))))))
  (assert (= found 0.038911299999999996) found)
  ;; 額の行が無い・空の本文 = None(0 を発明しない)。
  (<- absent (recorded-cost "{\"type\":\"user\",\"text\":\"x\"}\n"))
  (assert (is absent None))
  (<- empty (recorded-cost ""))
  (assert (is empty None))
  ;; 最後の額の行の値が数でない(形が変わった)= None — 前の行の額へ倒れて誤った起点を作らない。bool も数えない。
  (<- changed (recorded-cost "{\"type\":\"cost-state\",\"totalCostUSD\":0.5}\n{\"type\":\"cost-state\",\"totalCostUSD\":\"0.7\"}\n"))
  (assert (is changed None))
  (<- flag (recorded-cost "{\"type\":\"cost-state\",\"totalCostUSD\":true}"))
  (assert (is flag None))
  ;; 壊れた(JSON にならない)行は行として数えない。
  (<- torn (recorded-cost "{\"type\":\"cost-state\",\"totalCostUSD\":0.5}\n{\"type\":\"cost-state\",\"totalCo"))
  (assert (= torn 0.5) torn))


(deftest test-open-maps-are-frozen-when-built
  ;; 鍵の集合が開いた写像(env・settings・道具の入力)は作る時に写し取って凍らせる — 作った後に中身が変わらない。
  (setv env {"PATH" "/bin"})
  (setv home (ClaudeHome "/h" env))
  (setv (get env "PATH") "/changed")
  (assert (= (get home.env "PATH") "/bin"))
  (with [(pytest.raises TypeError)] (setv (get home.env "X") "1"))
  (with [(pytest.raises TypeError)] (ClaudeHome "/h" {"PATH" 1}))
  (with [(pytest.raises TypeError)] (McpStdio "cmd" :env {"A" 1}))
  (setv spec (ClaudeSessionSpec :home HOME :cwd "/w" :settings {"env" {"A" "1"} "list" [1 2]}
                                :mcp-servers {"s" (McpStdio "cmd" :env {"B" "2"})}))
  (with [(pytest.raises TypeError)] (setv (get spec.settings "env" "A") "2"))
  (assert (= (get spec.settings "list") #(1 2)))
  (with [(pytest.raises TypeError)] (setv (get spec.mcp-servers "t") (McpSse "http://x")))
  (setv mcp (json.loads (get (setx argv (launch-argv #("claude") spec (FreshSession SID))) (+ (.index argv "--mcp-config") 1))))
  (assert (= mcp {"mcpServers" {"s" {"type" "stdio" "command" "cmd" "args" [] "env" {"B" "2"}}}}))
  (setv asked (PermissionRequested "q" "Bash" {"command" {"nested" ["a"]}}))
  (with [(pytest.raises TypeError)] (setv (get asked.input "command" "nested") "b"))
  (assert (= (. (Allow {"command" "x"}) updated-input) {"command" "x"}))
  (with [(pytest.raises TypeError)] (setv (get (. (Allow {"command" "x"}) updated-input) "command") "y")))


(deftest test-a-stop-hook-feedback-line-is-classified-with-its-reason
  ;; #4020: Stop hook が答えを差し戻すと、CLI は手番に差し戻しの行を注入して答え直させる(実測 CLI 2.1.292・旗なしの既定の
  ;; 出力 — isSynthetic の user の行で、本文の text の block が「Stop hook feedback:」で始まる)。名前だけの Other に捨てると、上の層は
  ;; 差し戻された答えと答え直しを見分けられず、画面に答えが 2 つ並ぶ。理由(接頭の後)を持つ型で受ける。
  (val measured {"type" "user"
                 "message" {"role" "user" "content" [{"type" "text" "text" "Stop hook feedback:\n確かめ用: もう 1 文だけ「追記です」と書いてください"}]}
                 "parent_tool_use_id" None "session_id" "96d008f1-9055-400c-8ac3-f5b71ccb5e93"
                 "uuid" "e9a31461-e363-4d8e-8270-e4a80fcc9ea0" "timestamp" "2026-10-07T15:14:09.691Z" "isSynthetic" True})
  (assert (= (classify-record (parse-record (json.dumps measured :ensure-ascii False)))
             (StopHookFeedback :reason "確かめ用: もう 1 文だけ「追記です」と書いてください")))
  ;; 印の片方だけの行は今までどおり Other(type = user): 接頭はあるが isSynthetic でない user の行(人が同じ文を打った)と、
  ;; isSynthetic だが接頭の無い行(CLI のほかの注入)。
  (assert (= (classify-record {"type" "user" "message" {"content" [{"type" "text" "text" "Stop hook feedback:\nx"}]}}) (Other :type "user")))
  (assert (= (classify-record {"type" "user" "isSynthetic" True "message" {"content" [{"type" "text" "text" "other"}]}})
             (Other :type "user"))))
