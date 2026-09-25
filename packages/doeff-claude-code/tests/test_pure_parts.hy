;; argv の組み立て・「手番を始める」の判断・行の分類の検 — 純関数だけ。
(require doeff-hy.macros [deftest])
(import json)
(import pytest)
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn BypassAll AskHost DenyUnlisted McpSse McpStdio
                                  AutocompactAuto AutocompactTokens FreshSession ResumeSession ForkSession TurnInput])
(import doeff_claude_code.argv [launch-argv cold-resume-argv transcript-path process-env])
(import doeff_claude_code.decision [SessionView Refuse Launch start-decision])
(import doeff_claude_code.effects [SessionIdInUse SessionNotFound TurnInFlight])
(import doeff_claude_code.lines [classify-record Init AssistantMessage ToolResult InputFate PermissionRequested TaskEvent
                                 RateLimit TurnResult PartialMessage Other])

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
  ;; 手番ごとに降ろす handler の物理: background の仕事を持たせない(宣言の env と合流する)。
  (assert (= settings {"disableAllHooks" True "env" {"A" "1" "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" "1"}}))
  (assert (= (get argv (+ (.index argv "--model") 1)) "haiku"))
  (assert (= (get argv (+ (.index argv "--autocompact") 1)) "400000"))
  (assert (in "--strict-mcp-config" argv))
  (assert (= (cut argv -2 None) ["--session-id" SID]))
  (assert (= (cut (launch-argv #("claude") spec (ResumeSession SID)) -2 None) ["--resume" SID]))
  (assert (= (cut (launch-argv #("claude") spec (ForkSession SID)) -3 None) ["--resume" SID "--fork-session"])))


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
  ;; 設計 7 節の表: 起こし直しの判断はここ 1 か所。
  (setv turn (ClaudeTurn SID 3))
  (assert (= (start-decision (FreshSession SID) (SessionView) False False) (Launch)))
  (assert (= (start-decision (FreshSession SID) (SessionView :known True) False False) (Refuse (SessionIdInUse SID))))
  (assert (= (start-decision (FreshSession SID) (SessionView) True False) (Refuse (SessionIdInUse SID))))
  (assert (= (start-decision (ResumeSession SID) (SessionView) True False) (Launch)))
  (assert (= (start-decision (ResumeSession SID) (SessionView) False False) (Refuse (SessionNotFound SID))))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :running-turn turn) True False)
             (Refuse (TurnInFlight turn))))
  (assert (= (start-decision (ResumeSession SID) (SessionView :known True :retiring True) True True)
             (Launch :wait-retire True :cold-resume True)))
  (assert (= (start-decision (ForkSession SID) (SessionView :known True :running-turn turn) True False) (Launch)))
  (assert (= (start-decision (ForkSession SID) (SessionView) False False) (Refuse (SessionNotFound SID)))))


(deftest test-line-classification
  (assert (= (classify-record {"type" "system" "subtype" "init" "session_id" SID "capabilities" ["msg_lifecycle_v1"]
                               "model" "m" "permissionMode" "default" "mcp_servers" [{"name" "s"}]})
             (Init SID #("msg_lifecycle_v1") "m" "default" #("s"))))
  (assert (= (classify-record {"type" "assistant" "message" {"content" [{"type" "text" "text" "a"}
                                                                        {"type" "tool_use" "name" "Bash"}]}})
             (AssistantMessage "a" #("Bash"))))
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
  (assert (= (classify-record {"type" "brand_new" "subtype" "s"}) (Other "brand_new" "s"))))
