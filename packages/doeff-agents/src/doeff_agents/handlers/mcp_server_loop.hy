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
(import doeff [handler :as _program-handler])
(import doeff_core_effects.scheduler [
  CreateExternalPromise Wait Spawn Race Cancel PRIORITY_IDLE])
(import doeff_agents.mcp-server [
  McpToolServer McpToolRequest TOOL_VM_TIMEOUT])
(import queue [Empty])
(import threading)


(defn _scheduler-prompt? [h]
  "True when h is (or wraps) the scheduler's own prompt handler.
   scheduler.make_handler marks its raw handler with
   __doeff_scheduler_prompt__; captured stacks may carry either the raw
   handler or its Program->Program install wrapper
   (__doeff_handler_data__)."
  (or (getattr h "__doeff_scheduler_prompt__" False)
      (getattr (getattr h "__doeff_handler_data__" None)
               "__doeff_scheduler_prompt__" False)))


(defk _timeout-after [seconds timer-box]
  {:pre [(: seconds float) (: timer-box dict)] :post [(: % tuple)]}
  (<- timeout-ep (CreateExternalPromise))
  (setv timer
        (threading.Timer
          seconds
          (fn [] (.complete timeout-ep None))))
  (setv (get timer-box "timer") timer)
  (.start timer)
  (<- _ (Wait timeout-ep.future :priority PRIORITY_IDLE))
  #("timeout" seconds))


(defk _tool-result-with-stack [server full-stack req]
  {:pre [(: server McpToolServer) (: full-stack list) (: req McpToolRequest)]
   :post [(: % tuple)]}
  (setv tool (.get server._tools req.tool-name))
  (when (is tool None)
    (return #("tool" False f"unknown tool: {req.tool-name}")))
  (setv args (lfor name (.param-names tool) (.get req.arguments name)))
  (setv program (tool.handler #* args))
  (for [h full-stack]
    ;; Never reinstall the scheduler's own prompt handler (marked by
    ;; scheduler.make_handler): the VM has exactly ONE scheduler prompt.
    ;; A duplicate inside this tool task makes Transfer-resumed pipeline
    ;; continuations unwind their exceptions into THIS task's except
    ;; below — the pipeline error is swallowed as a tool error while the
    ;; orphaned tool value becomes the run() result (2026-07-07 SBI live
    ;; exit-0 incident).
    (when (not (_scheduler-prompt? h))
      (setv program ((_program-handler h) program))))
  (setv ok True)
  (setv result None)
  (setv error-msg None)
  (try
    (<- value program)
    (setv result value)
    (except [e Exception]
      (setv ok False)
      (setv error-msg (str e))))
  #("tool" ok (if ok result error-msg)))


(defk run-tool-with-stack [server full-stack req * [timeout-seconds TOOL_VM_TIMEOUT]]
  "Execute one tool call with the captured handler stack, then wake the
   HTTP thread by writing req.holder and setting req.event.

   `full-stack` is the handler list captured at the Launch site
   (GetHandlers(k) + GetOuterHandlers()). Installing them here rebuilds
   the same dynamic handler environment the user's Program had.

   The tool body itself is deadline-owned inside the doeff VM. The HTTP/SSE
   thread timeout is only a transport backstop; cooperative tool waits such as
   WaitUntil must be cancelled by this VM-side timeout."
  {:pre [(: server McpToolServer) (: full-stack list) (: req McpToolRequest)
         (: timeout-seconds float)]
   :post [(: % (type None))]}
  (<- tool-task (Spawn (_tool-result-with-stack server full-stack req)))
  (setv timer-box {})
  (<- timeout-task (Spawn (_timeout-after timeout-seconds timer-box)))
  (<- outcome (Race tool-task timeout-task))
  (setv timer (.get timer-box "timer"))
  (when timer
    (.cancel timer))
  (setv tag (get outcome 0))
  (if (= tag "timeout")
      (do
        (<- _ (Cancel tool-task))
        (.append req.holder
                 #(False
                   (+ "tool call timed out inside doeff VM after "
                      (str timeout-seconds) "s")))
        (.set req.event)
        None)
      (do
        (<- _ (Cancel timeout-task))
        (setv ok (get outcome 1))
        (setv value (get outcome 2))
        (if ok
            (.append req.holder #(True value))
            (.append req.holder #(False value)))
        (.set req.event)
        None)))


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
        (except [_ Empty]
          (setv req None)))
      (when (is-not req None)
        (<- _ (Spawn
                (run-tool-with-stack
                  server full-stack req
                  :timeout-seconds (float server.tool-vm-timeout)))))))
  None)
