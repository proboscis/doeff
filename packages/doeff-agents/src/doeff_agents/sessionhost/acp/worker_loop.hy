;;; 通常処理とキャッシュ維持を同じVMの独立taskとして動かす。
;;; state machineと復旧の判断は既存のProgram。I/Oの待ちだけasync-dispatchがAwaitにする。
(require doeff-hy.macros [defk defhandler <-])
(import asyncio)
(import types [NoneType])
(import dataclasses [replace])
(import doeff [Program])
(import doeff_core_effects.effects [Await])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Gather CreateSemaphore])
(import collections.abc [Callable])
(import .async_dispatch [async-dispatch])
(import .loop_model [LoopControl LoopPorts ReadLoopControl PublishLoopState LoopDelay LoopLog])
(import .effects [AgentdSettings AgentdState AcpGet AcpRow NODE-KIND SessionSend SessionRefused])
(import ..cache_host_model [CACHE-MAINTENANCE-ACTIVE])
(import .agentd [agentd-tick cache-credential-handler collect-intakes])
(import .intake [IntakeBook spawned-intake serial-sections])
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
  {:pre [(: settings AgentdSettings) (: state AgentdState)] :post [(: % NoneType)]}
  (setv backoff 1.0)
  (while True
    (<- control LoopControl (ReadLoopControl))
    (when control.stopping
      ;; card acp:kanban-issue:ki-e786e72e2ae7(I5): 走っている受け付けを待って引き取ってから降りる — 停止の腕
      ;; (runtime.run_close_for_stop)が読む最後の状態に、係が起こした手番を必ず載せる。
      (try
        (<- drained AgentdState (collect-intakes settings state True))
        (<- (PublishLoopState drained))
        (except [error Exception]
          (<- (LoopLog f"agentd: intakes were not collected before the stop: {(. (type error) __name__)}: {error}"))))
      (return None))
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
  {:pre [(: settings AgentdSettings)] :post [(: % NoneType)]}
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

(defk run-worker-programs [make-normal make-maintenance]
  {:pre [(: make-normal Callable) (: make-maintenance (| Callable None))] :post [(: % NoneType)]}
  ;; card acp:kanban-issue:ki-e786e72e2ae7(I6): 直列の区間の semaphore は task を起こす前に 1 つだけ作り、通常処理
  ;; (拍と受け付けの係)と専用操作の両方に渡す。
  (<- semaphore (CreateSemaphore 1))
  (<- worker (Spawn (make-normal semaphore)))
  (if make-maintenance
    (do
      (<- cache (Spawn (make-maintenance semaphore)))
      (<- (Gather worker cache)))
    (<- (Gather worker)))
  None)

(defk concurrent-worker [settings state normal-dispatchers cache-dispatchers ports]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: normal-dispatchers tuple)
         (: cache-dispatchers tuple) (: ports LoopPorts)] :post [(: % NoneType)]}
  ;; card acp:kanban-issue:ki-e786e72e2ae7: 受け付けの係(spawned-intake)は拍の program のすぐ外 — 係が起こす task は
  ;; 起こした所の handler の列(直列の区間・送りの直列化・I/O の async-dispatch)の下で走る。
  (defn make-normal [semaphore]
    (async-stack normal-dispatchers
      ((cache-send-serialization)
        ((serial-sections semaphore)
          ((spawned-intake (IntakeBook)) (normal-worker-loop settings state))))))
  (defn make-maintenance [semaphore]
    (async-stack cache-dispatchers ((serial-sections semaphore) (cache-worker-loop settings))))
  (<- (scheduled ((await-handler) ((loop-control ports)
    (run-worker-programs make-normal (if cache-dispatchers make-maintenance None))))))
  None)
