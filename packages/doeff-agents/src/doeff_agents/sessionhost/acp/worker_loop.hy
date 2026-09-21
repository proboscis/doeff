;;; 通常処理とキャッシュ維持を同じVMの独立taskとして動かす。
;;; state machineと復旧の判断は既存のProgram。I/Oの待ちだけasync-dispatchがAwaitにする。
(require doeff-hy.macros [defk defhandler <-])
(import asyncio)
(import dataclasses [replace])
(import doeff [Program])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Gather])
(import .async_dispatch [async-dispatch])
(import .loop_model [LoopControl LoopPorts ReadLoopControl PublishLoopState LoopDelay LoopLog])
(import .effects [AgentdSettings AgentdState AcpGet AcpRow NODE-KIND SessionSend SessionRefused])
(import ..cache_host_model [CACHE-MAINTENANCE-ACTIVE])
(import .agentd [agentd-tick cache-credential-handler])
(import .judgment [node-row-named])
(import .cache_live [cache-live-handler maintain-node-cache])

(defhandler loop-control [#^ LoopPorts ports]
  (ReadLoopControl [] (resume (LoopControl (ports.stopping) (ports.draining))))
  (PublishLoopState [state] (ports.publish state) (resume None))
  (LoopLog [text] (ports.log text) (resume None))
  (LoopDelay [seconds]
    (<- (Await (asyncio.sleep seconds)))
    (resume None)))

(defhandler cache-send-serialization []
  (SessionSend []
    (<- answer (| str None SessionRefused) effect)
    ;; 未送信が保証された専用操作との競合だけを待つ。通常の拒否・通信断は再送しない。
    (while (and (isinstance answer SessionRefused)
                (= answer.error-code CACHE-MAINTENANCE-ACTIVE))
      (<- (LoopDelay 0.25))
      (<- answer (| str None SessionRefused) effect))
    (resume answer)))

(defk normal-worker-loop [settings state]
  {:pre [(: settings AgentdSettings) (: state AgentdState)] :post [(: % "None")]}
  (setv backoff 1.0)
  (while True
    (<- control LoopControl (ReadLoopControl))
    (when control.stopping (return None))
    (try
      (<- state AgentdState (agentd-tick (replace settings :draining control.draining) state))
      (<- (PublishLoopState state))
      (setv backoff 1.0)
      (except [error Exception]
        (<- (LoopLog f"agentd: tick failed: {(. (type error) __name__)}: {error}"))
        (<- (LoopDelay backoff))
        (setv backoff (min 30.0 (* 2 backoff))))))
  None)

(defk cache-worker-loop [settings]
  {:pre [(: settings AgentdSettings)] :post [(: % "None")]}
  ;; 通常turnの資格journalと別の所有者。同じfileへのread-modify-writeを競合させない。
  (setv settings (replace settings :lease-journal-path
    (if settings.lease-journal-path (+ settings.lease-journal-path ".cache") "")))
  (while True
    (<- control LoopControl (ReadLoopControl))
    (when control.stopping (return None))
    (try
      (<- nodes tuple (AcpGet :kind NODE-KIND))
      (<- node (| AcpRow None) (node-row-named nodes settings.node-name))
      (when node
        (<- ((cache-credential-handler settings)
          ((cache-live-handler settings node.resource-id) (maintain-node-cache node.resource-id)))))
      (except [error Exception]
        (<- (LoopLog f"agentd: cache maintenance failed: {(. (type error) __name__)}: {error}"))))
    (<- (LoopDelay 1.0)))
  None)

(defn #^ Program async-stack [#^ tuple dispatchers #^ Program program]
  (for [dispatcher (reversed dispatchers)]
    (setv program ((async-dispatch dispatcher "doeff_agents.sessionhost.acp.") program)))
  program)

(defk run-worker-programs [normal maintenance]
  {:pre [(: normal Program) (: maintenance (| Program None))] :post [(: % "None")]}
  (<- worker (Spawn normal))
  (if maintenance
    (do
      (<- cache (Spawn maintenance))
      (<- (Gather worker cache)))
    (<- (Gather worker)))
  None)

(defk concurrent-worker [settings state normal-dispatchers cache-dispatchers ports]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: normal-dispatchers tuple)
         (: cache-dispatchers tuple) (: ports LoopPorts)] :post [(: % "None")]}
  (setv normal (async-stack normal-dispatchers
    ((cache-send-serialization) (normal-worker-loop settings state))))
  (setv maintenance (if cache-dispatchers
    (async-stack cache-dispatchers (cache-worker-loop settings)) None))
  (<- (scheduled ((await-handler) ((loop-control ports) (run-worker-programs normal maintenance)))))
  None)
