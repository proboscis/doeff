;;; result channel の語彙(ADR 0035 / S13 / main.rs:1319 mcp_command_args)。
;;;
;;; 結果は常に結果の MCP server(データチャネル)経由 — pane を result として
;;; parse することは禁止(result-first)。server 名と、--session / --socket の
;;; argv の形は conformance S13 が守る。起こす物は役ごとの入口の module
;;; (result_mcp.py)で、host の CLI のサブコマンドではない。

(require doeff-hy.macros [deff])


(setv REPORT-RESULT-MCP-SERVER "doeff_result")
;; 結果の MCP server の入口(python -m の的)。host の入口(sessionhost.host)とは別の module。
(setv _RESULT-MCP-MODULE "doeff_agents._sessionhost.result_mcp")


(deff result-channel-spec [python session-id socket-path]
  {:pre [(: python str) (> (len python) 0)
         (: session-id str) (: socket-path str)]
   :post [(: % dict)]}
  "stdio MCP server として agent に spawn させる channel spec — WireResultChannel の
   handler が agent の MCP の設定に書く(python は host を走らせている interpreter)。"
  {"command" python
   "args" ["-m" _RESULT-MCP-MODULE
           "--session" session-id
           "--socket" socket-path]})
