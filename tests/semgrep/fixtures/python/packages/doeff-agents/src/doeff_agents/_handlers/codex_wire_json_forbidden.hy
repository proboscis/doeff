;; 失敗の形(doeff-agents-decodes-no-wire-json): 層 3 が codex の item の JSON を自前の wire の型で解く。
(import doeff_hy._wire [parse Malformed])
(import doeff_hy._json_value [OpaqueJson])
(defwire McpToolCallWire
  {:tags {:context "headless-adapter" :role "type"} :names :camel :unknown :ignore}
  (#^ str server) (#^ str tool))
(setv wire (parse-json McpToolCallWire line))
;; 通る形: 層 2 の型つきの記録だけを読む。
(import doeff_codex [ItemStarted CommandRun])
