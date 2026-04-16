;;; mcp_server_loop — doeff Program that dispatches MCP tool requests
;;; inside the main VM.
;;;
;;; Runs as a task spawned by claude_handler after starting McpToolServer.
;;; Bridges the HTTP/SSE transport thread to the VM:
;;;
;;;   HTTP thread                VM task (this file)
;;;   ----------                 -------------------
;;;   POST /message              (loop)
;;;     push req to queue   ───▶ drain request_queue
;;;     ep = mailbox.get()       ⇐ post ep to mailbox
;;;     ep.complete(None)   ───▶ Wait(ep.future) returns
;;;                              Spawn(run-tool-with-stack req)
;;;     event.wait() returns ⇐─ req.event.set()
;;;     ...                      (continue loop)
;;;
;;; Running the tool inside the main VM means all shared state (sim_time
;;; clock, scheduler task pool, state handler, lazy_ask env) is the SAME
;;; across the pipeline and each tool invocation — which is what makes
;;; WaitUntil, GetTime, and other time-sensitive effects actually work.

(require doeff-hy.macros [<- defk])
(import doeff [WithHandler])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait Spawn PRIORITY_IDLE])
(import doeff_agents.mcp-server [McpToolServer McpToolRequest])


(defk run-tool-with-stack [server full-stack req]
  "Execute one tool call with the captured handler stack, then wake the
   HTTP thread by writing req.holder and setting req.event.

   `full-stack` is the handler list captured at the Launch site
   (GetHandlers(k) + GetOuterHandlers()). Installing them here rebuilds
   the same dynamic handler environment the user's Program had."
  {:pre [(: server McpToolServer) (: full-stack list) (: req McpToolRequest)] :post [(: % (type None))]}
  (setv tool (.get server._tools req.tool-name))
  (when (is tool None)
    (.append req.holder #(False f"unknown tool: {req.tool-name}"))
    (.set req.event)
    (return None))
  (setv args (lfor name (.param-names tool) (.get req.arguments name)))
  (setv program (tool.handler #* args))
  (for [h full-stack]
    (setv program (WithHandler h program)))
  (setv ok True)
  (setv result None)
  (setv error-msg None)
  (try
    (<- value program)
    (setv result value)
    (except [e Exception]
      (setv ok False)
      (setv error-msg (str e))))
  (if ok
      (.append req.holder #(True result))
      (.append req.holder #(False error-msg)))
  (.set req.event)
  None)


(defk mcp-server-loop [server full-stack]
  "Main dispatch loop for McpToolServer running inside the VM.

   Posts an ExternalPromise to server.wakeup_mailbox each iteration so the
   HTTP thread has a way to signal new requests. Drains the request_queue
   after waking and Spawns a task per request so the loop itself never
   blocks on slow tool handlers.

   Exits when server.shutting_down is set (the shutdown path completes the
   most recent wakeup ep to force one more iteration)."
  {:pre [(: server McpToolServer) (: full-stack list)] :post [(: % (type None))]}
  (while (not server.shutting-down)
    (<- wakeup-ep (CreateExternalPromise))
    (.put server.wakeup-mailbox wakeup-ep)
    ;; Race guard: if shutdown was signaled between the while check and the
    ;; put, exit now rather than parking on an ep nobody will complete.
    (when server.shutting-down
      (break))
    ;; Background wait: don't block the sim_time clock driver while idle.
    ;; The HTTP thread completes this promise when a request arrives, and
    ;; drain() picks that up without forcing a foreground external wait.
    (<- _ (Wait wakeup-ep.future :priority PRIORITY_IDLE))
    (while (not (.empty server.request-queue))
      (setv req None)
      (try
        (setv req (.get-nowait server.request-queue))
        (except [_ Exception]
          (setv req None)))
      (when (is-not req None)
        (<- _ (Spawn (run-tool-with-stack server full-stack req))))))
  None)
