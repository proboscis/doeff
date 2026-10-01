;; task の子 process が 0 で終わった直後・worker の次の heartbeat の前に worker が死ぬ窓(#1387・#989 の (3))。
;;
;; 前の形: 子 process は結果を worker の disk(<task-dir>/<id>.result)に書いて 0 で終わり、worker が次の heartbeat の状態の行に載せて
;; coordinator へ運んだ。その間に worker が死ぬと結果は coordinator に届かず、同じ名で起き直した worker の新しい世代へ coordinator が
;; 同じ task を渡し直して、成功した task が 2 度走った(切り離した task は置いた世代にしか渡さないので、走らせ直さずに lease 切れで lost)。
;; 今の形: 子 process は終わる前に結果を coordinator へ直に届け(POST /tasks/<id>/result)、届かなかった時だけ file と heartbeat が運ぶ。
;; coordinator は同じ task の 2 度目の結果(heartbeat が運んだ物)を冪等に受ける。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [sim-cluster SimWorker ProcessesOf KillWorker StartWorker ReadCoordinator])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded])
(import doeff_cluster.shared.core.remote_rules [remote-job])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses slow-task sim-task-foundation NET])

(val WORKER "w1")
;; task の長さ。worker の拍(0.5 秒)の刻みに乗らない長さにして、task の終わりの刻と次の heartbeat の刻を分ける。
(val TASK-SECONDS 2.2)
;; worker の Pod が同じ名で起き直すまでの秒(coordinator が担い手の沈黙とみなす 45 秒より十分短い)。
(val RESTART-SECONDS 3.0)


(defrecord WindowRun
  "窓の検の読み: answer = 呼び手の答え・ended-ms = task の最初の process が終わった刻・killed-ms = worker を殺した刻・
   runs = task の process の記録(起きた順)。"
  (#^ object answer)
  (#^ int ended-ms)
  (#^ int killed-ms)
  (#^ tuple runs))


(defk remote-caller [name]
  {:pre [(: name str)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの部品: 呼び手として TASK-SECONDS 秒眠る task を name で 1 本出し、答えを待つ。"
  (<- answer float (remote-job (slow-task sim-task-foundation TASK-SECONDS) :needs NET :name name))
  answer)


(defk task-id-named [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態から、name で出した task の id を読むため(1 本だけ在ることを確かめる)。"
  (<- state dict (ReadCoordinator "/state"))
  (val ids (lfor t (get state "tasks") :if (= (get t "name") name) (get t "id")))
  (assert (= (len ids) 1) (get state "tasks"))
  (get ids 0))


(defk kill-after-exit [job]
  {:pre [(: job str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの部品: job の最初の process が終わった刻に(worker の次の heartbeat の前に)worker を殺し、RESTART-SECONDS 秒後に同じ名で
   起こすため。答え = #(process が終わった刻 殺した刻)。"
  (<- (AwaitProcessEnded job))
  (<- killed-ms int (now-epoch-ms))
  (<- (KillWorker WORKER))
  (<- runs tuple (ProcessesOf job))
  (<- (Delay RESTART-SECONDS))
  (<- (StartWorker WORKER))
  #((. (get runs 0) ended-ms) killed-ms))


(defk remote-window []
  {:pre [] :post [(: % WindowRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 呼び手が task を出し、task の process が 0 で終わった刻に worker を殺して起こし直し、呼び手の答えと task の process の
   記録を読む。"
  (<- caller Task (Spawn (remote-caller "once")))
  (<- (Delay 0.1))
  (<- id str (task-id-named "once"))
  (<- times tuple (kill-after-exit (+ "task/" id)))
  (<- answer (Wait caller))
  (<- runs tuple (ProcessesOf (+ "task/" id)))
  (WindowRun :answer answer :ended-ms (get times 0) :killed-ms (get times 1) :runs runs))


(defk detached-window [key]
  {:pre [(: key str)] :post [(: % WindowRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 切り離した task を key で出し、task の process が 0 で終わった刻に worker を殺して起こし直し、待ちの答えと task の
   process の記録を読む。"
  (<- (submit-detached-task (slow-task sim-task-foundation TASK-SECONDS) :key key :needs NET :name "once-detached"))
  (<- (Delay 0.1))
  (<- id str (task-id-named "once-detached"))
  (<- times tuple (kill-after-exit (+ "task/" id)))
  (<- answer (AwaitDetached key))
  (<- runs tuple (ProcessesOf (+ "task/" id)))
  (WindowRun :answer answer :ended-ms (get times 0) :killed-ms (get times 1) :runs runs))


(deftest test-a-task-that-exited-0-runs-once-and-answers-although-its-worker-dies-before-the-next-heartbeat
  ;; task の process が 0 で終わった刻(次の heartbeat の前)に worker を殺し、同じ名で起こし直す: task は 1 度だけ走り、呼び手に
  ;; 答えが届く。前の形では結果が死んだ worker の disk に残ったまま届かず、起き直した worker が同じ task をもう 1 度走らせた。
  (<- seen WindowRun (sim-cluster (pulses sim-foundation) (remote-window) :workers #((SimWorker :name WORKER :provides NET))))
  (assert (= seen.killed-ms seen.ended-ms) seen)
  (assert (= (. (get seen.runs 0) exit-code) 0) seen.runs)
  (assert (= (len seen.runs) 1) seen.runs)
  (assert (= seen.answer TASK-SECONDS) seen))


(deftest test-a-detached-task-that-exited-0-keeps-its-answer-although-its-worker-dies-before-the-next-heartbeat
  ;; 切り離した task でも同じ窓で答えを失わない。前の形では、置いた世代の worker が死んだので lease 切れで lost(DetachedLost)になった。
  (<- seen WindowRun (sim-cluster (pulses sim-foundation) (detached-window "once") :workers #((SimWorker :name WORKER :provides NET))))
  (assert (= seen.killed-ms seen.ended-ms) seen)
  (assert (= (. (get seen.runs 0) exit-code) 0) seen.runs)
  (assert (= (len seen.runs) 1) seen.runs)
  (assert (= seen.answer (DetachedSucceeded TASK-SECONDS)) seen))
