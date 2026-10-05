;; argv の組み立て・「手番を始める」の判断・行の分類の検 — 純関数だけ。
(require doeff-hy.macros [deftest val <-])
(import json)
(import pytest)
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn BypassAll AskHost DenyUnlisted McpSse McpStdio
                                  AutocompactAuto AutocompactTokens FreshSession ResumeSession ForkSession TurnInput])
(import doeff_claude_code.argv [launch-argv launch-key cold-resume-argv transcript-path process-env])
(import doeff_claude_code.decision [SessionView Refuse Reuse Launch start-decision])
(import doeff_claude_code.effects [SessionIdInUse SessionNotFound TurnInFlight])
(import doeff_claude_code.lines [classify-record parse-record Init AssistantMessage ToolCall ToolResult InputFate PermissionRequested TaskEvent
                                 RateLimit TurnResult PartialMessage Other ControlResponse Usage recorded-cost])
(import doeff_claude_code.values [Allow])

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
  (for [policy [(BypassAll) (AskHost) (DenyUnlisted #("Read" "Monitor"))]]
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
  (assert (= (process-env HOME) {"PATH" "/bin" "CLAUDE_CONFIG_DIR" "/h/.claude"})))


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
                        "message" {"content" [{"type" "tool_use" "id" "toolu_1" "name" "Bash"}
                                              {"type" "tool_use" "id" "toolu_2" "name" "Read"}]}}))
  (val kind (classify-record (parse-record raw)))
  (assert (isinstance kind AssistantMessage) (repr kind))
  (assert (= kind.tool-calls #((ToolCall "toolu_1" "Bash") (ToolCall "toolu_2" "Read"))) (repr kind)))

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

(deftest test-line-classification
  (assert (= (classify-record {"type" "system" "subtype" "init" "session_id" SID "capabilities" ["msg_lifecycle_v1"]
                               "model" "m" "permissionMode" "default" "mcp_servers" [{"name" "s"}]})
             (Init SID #("msg_lifecycle_v1") "m" "default" #("s"))))
  (assert (= (classify-record {"type" "assistant" "message" {"content" [{"type" "text" "text" "a"}
                                                                        {"type" "tool_use" "id" "toolu_a" "name" "Bash"}]}})
             (AssistantMessage "a" #((ToolCall "toolu_a" "Bash")))))
  (assert (= (classify-record {"type" "user" "message" {"content" [{"type" "tool_result" "tool_use_id" "t1"}]}})
             (ToolResult #("t1"))))
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
             (PartialMessage "x")))
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
