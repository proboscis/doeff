(require doeff-hy.macros [deftest defk <-])

(import threading)
(import doeff.mcp [McpParamSchema McpToolDef])
(import doeff-agents.handlers.mcp-server-loop [mcp-server-loop])
(import doeff-agents.mcp-server [McpToolRequest McpToolServer])
(import doeff_core_effects.scheduler [CreateExternalPromise PRIORITY_IDLE Wait])


(defk _echo-tool-handler [msg]
  {:pre [(: msg str)] :post [(: % str)]}
  (+ "echo: " msg))


(defk _never-tool-handler []
  {:pre [] :post [(: % str)]}
  (<- promise (CreateExternalPromise))
  (<- _ (Wait promise.future :priority PRIORITY_IDLE))
  "never")


(setv _ECHO-TOOL
      (McpToolDef
        :name "echo"
        :description "Echo back the message"
        :params #((McpParamSchema
                    :name "msg"
                    :type "string"
                    :description "message"))
        :handler _echo-tool-handler))


(setv _NEVER-TOOL
      (McpToolDef
        :name "never"
        :description "Never completes unless cancelled"
        :params #()
        :handler _never-tool-handler))


(defn _push-one-request [server tool-name args]
  (setv req
        (McpToolRequest
          :tool-name tool-name
          :arguments args
          :event (threading.Event)
          :holder []))
  (.put server.request-queue req)
  (setv ep (.get server.wakeup-mailbox :timeout 5.0))
  (.complete ep None)
  (assert (.wait req.event :timeout 5.0) "Tool call did not complete in time")
  req)


(defn _wake-for-shutdown [server]
  (setv server.shutting-down True)
  (try
    (setv ep (.get server.wakeup-mailbox :timeout 5.0))
    (.complete ep None)
    (except [Exception]
      None)))


(deftest test-mcp-server-loop-tool-timeout-is-owned-inside-vm []
  (setv server (McpToolServer :tools #(_NEVER-TOOL _ECHO-TOOL)))
  (setv server.tool-vm-timeout 0.05)
  (setv results [])
  (setv driver-errors [])

  (defn driver []
    (try
      (setv timeout-req (_push-one-request server "never" {}))
      (.append results (get timeout-req.holder 0))
      (setv echo-req (_push-one-request server "echo" {"msg" "after"}))
      (.append results (get echo-req.holder 0))
      (except [e Exception]
        (.append driver-errors e))
      (finally
        (_wake-for-shutdown server))))

  (setv driver-thread (threading.Thread :target driver :daemon True))
  (.start driver-thread)

  (<- _ (mcp-server-loop server []))
  (.join driver-thread :timeout 1.0)
  (assert (not (.is-alive driver-thread)) "driver thread did not stop")
  (assert (= driver-errors []) (str driver-errors))
  (setv timeout-result (get results 0))
  (assert (= (get timeout-result 0) False))
  (assert (in "timed out inside doeff VM" (get timeout-result 1)))
  (assert (= (get results 1) #(True "echo: after"))))
