;; worker の調整ループ。毎拍「宣言・観測・記憶」から action を導いて実行する。
;; 子 process もコードの準備も観測で追うので、どの job の処理もループ(停止の経路)を塞がない。
(require doeff-hy.macros [defk <-])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import .worker_model [WorkerPolicy WorkerState WorldView DesiredJobs DesiredUnreadable
  ReadDesired ObserveWorld WorkerStopRequested PublishStatus JobPhase])
(import .worker_policy [plan records-after statuses])

(defk worker-tick [state policy stopping]
  {:pre [(: state WorkerState) (: policy WorkerPolicy) (: stopping bool)] :post [(: % tuple)]}
  ;; 結果 = #(次の状態 まだ終了を待つ子 process の数)
  (<- read (| DesiredJobs DesiredUnreadable) (ReadDesired))
  ;; 読めない宣言を空と読まない。直前に読めた宣言を使い続ける。
  (setv desired (cond
    stopping #()
    (isinstance read DesiredJobs) read.jobs
    True state.desired))
  (<- now int (now-epoch-ms))
  (<- world WorldView (ObserveWorld))
  (setv warm (cond stopping #() (isinstance read DesiredJobs) read.warm True state.warm))
  (setv actions (plan now desired world state.records policy :warm warm))
  (for [action actions] (<- action))
  (setv records (records-after now state.records actions policy))
  ;; 状態の表示は action の後の観測から作る(起動・回収を 1 拍遅れで見せない)。
  (setv after world)
  (when actions
    (<- observed WorldView (ObserveWorld))
    (setv after observed))
  (setv report (statuses now desired after records policy))
  (<- (PublishStatus report (if (isinstance read DesiredUnreadable) read.reason "")))
  #((WorkerState (if (isinstance read DesiredJobs) read.jobs state.desired) records warm)
    ;; 停止を確認できない process は待ち続けない(状態表示に残す)。
    (len (lfor s report :if (in s.phase #(JobPhase.RUNNING JobPhase.STOPPING)) s))))

(defk run-worker [policy]
  {:pre [(: policy WorkerPolicy)] :post [(: % WorkerState)]}
  ;; worker の停止要求を受けたら宣言を空として扱い、全 job を同じ停止の手順で回収する。
  (setv state (WorkerState))
  (while True
    (<- stopping bool (WorkerStopRequested))
    (<- ticked tuple (worker-tick state policy stopping))
    (setv #(state alive) ticked)
    (when (and stopping (= alive 0)) (return state))
    (<- (Delay policy.tick-seconds))))
