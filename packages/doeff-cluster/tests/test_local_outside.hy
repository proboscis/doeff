;;; sim の外の世界(SimOutside)の検 — service は外の系(業務の store の模擬)を effect を通してだけ共有し、柵は SimOutside の effects に
;;; 載った型だけを外へ通す(載っていなければ本番の子と同じ未処理で落ちる)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_time [Delay])
(import doeff_cluster.local [sim-cluster SimOutside ProcessesOf])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.outside_programs [shared-store memory-store StorePut StoreGet])


(defk wait-seconds [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きが仮想の時計で待つ。"
  (<- (Delay seconds))
  None)


(deftest test-services-share-the-outside-store-only-through-effects
  (val rows {})
  (<- answer (sim-cluster (shared-store sim-foundation) (wait-seconds 30.0)
                          :outside (SimOutside :handlers [(memory-store rows)] :effects #(StorePut StoreGet))))
  (assert (>= (.get rows "count" 0) 10) rows)
  (assert (>= (.get rows "seen" 0) 5) rows))


(defk crashed-processes [name]
  {:pre [(: name str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "30 秒待ってから、job の process の終わりを読む。"
  (<- (Delay 30.0))
  (<- processes tuple (ProcessesOf name))
  processes)


(deftest test-without-the-outside-world-the-store-effect-is-unanswered
  (<- processes tuple (sim-cluster (shared-store sim-foundation) (crashed-processes "writer")))
  (assert processes)
  (assert (any (gfor p processes (and (is-not p.exit-code None) (!= p.exit-code 0) (in "StorePut" p.detail)))) processes))
