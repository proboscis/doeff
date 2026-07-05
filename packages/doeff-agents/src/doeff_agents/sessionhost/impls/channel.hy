;;; result channel の凍結語彙(ADR 0035 / S13 / main.rs:1319 mcp_command_args)。
;;;
;;; 結果は常に report-result-mcp データチャネル経由 — pane を result として
;;; parse することは禁止(result-first)。server 名・subcommand・argv 形状は
;;; conformance S13 が oracle に対して green 済みの凍結物理。

(require doeff-hy.macros [deff])


(setv REPORT-RESULT-MCP-SERVER "doeff_result")
(setv REPORT-RESULT-MCP-SUBCOMMAND "report-result-mcp")


(deff result-channel-spec [command session-id socket-path]
  {:pre [(: command str) (> (len command) 0)
         (: session-id str) (: socket-path str)]
   :post [(: % dict)]}
  "stdio MCP server として agent に spawn させる channel spec
   (oracle ResultChannel::mcp_command_args と同物理)。"
  {"command" command
   "args" [REPORT-RESULT-MCP-SUBCOMMAND
           "--session" session-id
           "--socket" socket-path]})
