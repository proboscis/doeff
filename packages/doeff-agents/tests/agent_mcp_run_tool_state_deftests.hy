(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [deftest defk <-])

(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff.mcp [McpParamSchema McpToolDef])
(import doeff_agents.handlers.effectful [_make-run-tool])


(setv LazyToolEffect
  ((dataclass :frozen True)
    (type "LazyToolEffect"
          #(EffectBase)
          {"__annotations__" {"text" str}})))


(defhandler lazy-prefix-handler []
  (lazy-val prefix "cached")
  (LazyToolEffect [text]
    (resume (+ prefix ":" text))))


(defk lazy-tool [text]
  {:pre [(: text str)] :post [(: % str)]}
  (<- result (LazyToolEffect :text text))
  result)


(deftest test-agent-mcp-run-tool-provides-state-for-captured-lazy-handlers []
  "Regression: LaunchSession MCP run_tool executes later in a callback VM.
   Captured handlers may use defhandler lazy-val/lazy-var, whose Get/Put effects
   flow outside the handler. run_tool must install state even when GetHandlers(k)
   did not capture the caller's outer state handler."
  (setv tool
    (McpToolDef
      :name "lazy-tool"
      :description "Tool that exercises captured handler lazy state"
      :params #( (McpParamSchema
                    :name "text"
                    :type "string"
                    :description "Input text"))
      :handler lazy-tool))
  (setv run-tool (_make-run-tool [(lazy-prefix-handler)]))
  (assert (= (run-tool tool {"text" "ok"}) "cached:ok")))
