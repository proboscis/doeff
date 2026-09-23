;;; Cancel が defk の task に TaskCancelledError を投げ込み、except と finally を走らせることの例。
;;; 呼び口は tests/test_cancel_runs_finally.py(pytest は .hy を集めないので、そこから呼ぶ)。

(require doeff-hy.macros [defk <-])

(import doeff_core_effects.scheduler [Cancel CompletePromise CreatePromise Promise Spawn
                                      Task TaskCancelledError Wait])


(defk noop []
  {:pre [True] :post [(: % "None")]}
  None)


(defk outcome [task]
  {:pre [(: task Task)] :post [(: % str)]}
  (try
    (<- (Wait task))
    "completed"
    (except [TaskCancelledError]
      "cancelled")))


(defk parked-worker [gate events]
  {:pre [(: gate Promise) (: events list)] :post [(: % "None")]}
  (try
    (.append events "start")
    (<- (Wait gate.future))
    (.append events "resumed")
    None
    (except [TaskCancelledError]
      (.append events "except")
      (raise))
    (finally
      (.append events "finally"))))


(defk cancel-parked-worker [events]
  {:pre [(: events list)] :post [(: % str)]}
  (<- gate (CreatePromise))
  (<- task (Spawn (parked-worker gate events)))
  (<- helper (Spawn (noop)))
  (<- (Wait helper))
  (<- (Cancel task))
  (<- result (outcome task))
  result)


(defk release-lease []
  {:pre [True] :post [(: % str)]}
  "released")


(defk worker-with-effectful-cleanup [gate done events]
  {:pre [(: gate Promise) (: done Promise) (: events list)] :post [(: % "None")]}
  (try
    (<- (Wait gate.future))
    (finally
      (.append events "cleanup start")
      (<- lease-task (Spawn (release-lease)))
      (<- released (Wait lease-task))
      (<- (CompletePromise done released))
      (.append events "cleanup end"))))


(defk cancel-worker-with-effectful-cleanup [events]
  {:pre [(: events list)] :post [(: % list)]}
  (<- gate (CreatePromise))
  (<- done (CreatePromise))
  (<- task (Spawn (worker-with-effectful-cleanup gate done events)))
  (<- helper (Spawn (noop)))
  (<- (Wait helper))
  (<- (Cancel task))
  (<- result (outcome task))
  (<- released (Wait done.future))
  [result released])
