(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [deftest defk <-])

(import asyncio)
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [Await])
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


(defhandler async-lazy-prefix-handler []
  (lazy-val prefix "cached")
  (LazyToolEffect [text]
    (<- result (Await (asyncio.sleep 0 :result (+ prefix ":" text))))
    (resume result)))


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


(deftest test-agent-mcp-run-tool-provides-await-for-captured-handler-effects []
  "Regression: captured handlers can perform Await while handling MCP tools
   (for example Slack notifications). run_tool must install await-handler in
   its callback VM because the caller's outer await-handler is not reliably
   captured by GetHandlers(k)."
  (setv tool
    (McpToolDef
      :name "async-lazy-tool"
      :description "Tool that exercises captured handler Await"
      :params #( (McpParamSchema
                    :name "text"
                    :type "string"
                    :description "Input text"))
      :handler lazy-tool))
  (setv run-tool (_make-run-tool [(async-lazy-prefix-handler)]))
  (assert (= (run-tool tool {"text" "ok"}) "cached:ok")))
