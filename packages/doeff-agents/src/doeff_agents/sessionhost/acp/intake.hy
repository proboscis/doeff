;;; 受け付けの係と直列の区間の handler(card acp:kanban-issue:ki-e786e72e2ae7・不変量は effects.py の「受け付けの係」節)。
;;;
;;; 機構だけを持つ — どの job を受けるか・何を写すかの判断は agentd.hy(receive-bound-jobs / collect-intakes)と
;;; judgment.hy(merged-intake)の側。ここの handler は 2 組:
;;;   * spawned-intake / serial-sections — 本番の loop(worker_loop.concurrent-worker)。受け付けを同じ VM の別の task として
;;;     起こし(Spawn — task は起こした所の handler の列の下で走る)、直列の区間は scheduler の semaphore で守る。
;;;   * inline-intake / inline-serial — 1 拍の入口(runtime.run_tick・停止の拍 run_close_for_stop — 検も同じ入口)。受け付けを
;;;     その場で走らせ、終わったものとして引き取らせる(今日の直列の拍と同じ順・同じ結果)。
;;; ⚠ どちらの組も program を handler の中で走らせるので、I/O の handler より**内側**に据える(program の要求は外側の
;;; handler へ届く)。受け付けの係は直列の区間の係より内側(起こした task が直列の区間の係の下で走るため)。

(require doeff-hy.macros [defk defhandler <-])
(import doeff [Program])
(import doeff_core_effects.scheduler [Spawn Wait AcquireSemaphore ReleaseSemaphore])
(import .effects [AgentdState IntakeStart IntakeCollect IntakeOutcome IntakeReport SerialSection])


(defclass IntakeBook []
  "受け付けの係の帳面(1 つの loop に 1 冊): 走っているもの(job の id → 会話の id)と、終わって引き取られていないもの。
   書くのは VM の thread だけ(task の続きは VM の thread でしか走らない)。"
  (defn __init__ [self]
    (setv self.pending {})
    (setv self.tasks {})
    (setv self.done [])))


(defn report-of [book]
  "帳面から答えを組み、終わったものを帳面から外す。"
  (setv done (tuple book.done))
  (.clear book.done)
  (for [outcome done]
    (.pop book.tasks outcome.job-id None))
  (IntakeReport :done done :pending (tuple (gfor #(job-id subject) (.items book.pending) #(job-id subject)))))


(defk run-intake [book job-id subject program]
  {:pre [(: book IntakeBook) (: job-id str) (: subject str) (: program Program)]
   :post [(: % bool)]}
  "受け付けの 1 本を走らせて結末を帳面に置く。失敗(例外)は捕まえて文で置く — 拍の loop の縁と同じ扱い(行は claim が
   着いていれば Running のまま残り、係の手を離れた次の受けの拍に拾い直しの腕が組み直す = 今日の受けの腕の失敗と同じ)。
   戻り = 状態を返したか。"
  (setv outcome None)
  (try
    (<- after AgentdState program)
    (setv outcome (IntakeOutcome :job-id job-id :subject subject :state after :error ""))
    (except [error Exception]
      (setv outcome (IntakeOutcome :job-id job-id :subject subject :state None
                                   :error f"{(. (type error) __name__)}: {error}"))))
  (.pop book.pending job-id None)
  (.append book.done outcome)
  (is-not outcome.state None))


(defhandler inline-intake [#^ IntakeBook book]
  (IntakeStart [job-id subject program]
    (setv (get book.pending job-id) subject)
    (<- (run-intake book job-id subject program))
    (resume None))
  (IntakeCollect [wait]
    (resume (report-of book))))


(defhandler spawned-intake [#^ IntakeBook book]
  (IntakeStart [job-id subject program]
    (setv (get book.pending job-id) subject)
    (<- task (Spawn (run-intake book job-id subject program)))
    (setv (get book.tasks job-id) task)
    (resume None))
  (IntakeCollect [wait]
    (when wait
      (for [task (list (.values book.tasks))]
        (<- (Wait task))))
    (resume (report-of book))))


(defhandler inline-serial []
  (SerialSection [name program]
    (<- value program)
    (resume value)))


(defhandler serial-sections [semaphore]
  (SerialSection [name program]
    (<- (AcquireSemaphore semaphore))
    (try
      (<- value program)
      (except [error Exception]
        (<- (ReleaseSemaphore semaphore))
        (raise)))
    (<- (ReleaseSemaphore semaphore))
    (resume value)))


(defn with-inline-intake [program]
  "1 拍の入口の handler の組(受け付けはその場で・直列の区間は何もしない)を program に被せる。"
  ((inline-serial) ((inline-intake (IntakeBook)) program)))
