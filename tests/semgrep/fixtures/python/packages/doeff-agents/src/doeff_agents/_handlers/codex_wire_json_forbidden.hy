;; Failing shape (doeff-agents-decodes-no-wire-json): layer 3 decodes the codex item JSON with its own wire type.
(import doeff_hy._wire [parse Malformed])
(import doeff_hy._json_value [OpaqueJson])
(defwire McpToolCallWire
  {:tags {:context "headless-adapter" :role "type"} :names :camel :unknown :ignore}
  (#^ str server) (#^ str tool))
(setv wire (parse-json McpToolCallWire line))
;; Passing shape: read only layer 2 typed records.
(import doeff_codex [ItemStarted CommandRun])
