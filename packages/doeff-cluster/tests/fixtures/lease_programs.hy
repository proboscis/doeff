;;; 名前付きの lease を持つ service の見本(test_lease_handoff.hy)— 入れ替え(handoff)で、旧い版が lease を持ったまま止まった後に、
;;; 新しい版が lease の期限を待たずに取れることを sim-cluster で確かめる。
;;;
;;; 土台は sim の土台(state だけ)に、本番の土台(cluster_foundation.cluster-handlers)と同じ名前付きの lease の担い手を並べる:
;;; cluster-semaphore と、担い手の名 = lease-holder-of(宿の契約の run-context — sim の宿が答える)。lease の要求(LeaseOp)は sim の宿の
;;; クラスタの約束の答えが coordinator へ送る。
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [with-handlers EffectBase Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [AcquireSemaphore Semaphore])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import doeff_cluster.cluster_foundation [lease-holder-of])
(import doeff_cluster.host_contract [HOST-CONTRACT])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.semaphore_handlers [cluster-semaphore SemaphoreSession])
(import doeff_cluster.shared.intent.semaphore_model [CreateNamedSemaphore])
(import doeff_cluster.shared.intent.shared_model [WriteShared])

;; 担い手が取る lease の名と、取った世代と刻を書く盤の行。
(setv LOCK "writer-lock")
(setv HOLDER-ROW "lease/holder")


(defk lease-sim-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の土台に、本番の土台と同じ名前付きの lease の担い手(cluster-semaphore・担い手の名は lease-holder-of)を並べて本体を走らせるため。
   scheduler と時計は sim-cluster の外側が答える。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- holder str (lease-holder-of ctx))
  (<- answer (with-handlers [(state) (cluster-semaphore (SemaphoreSession holder))] body))
  answer)


(defk hold-the-lease [every]
  {:pre [(: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "持ったまま止まる担い手の見本: 準備できたと報告してから(入れ替えの新しい版は旧い版が止まるまで待機)名前付きの lease を取り、取った
   世代と刻を盤に書き、止められるまで lease を返さずに報告を続ける(止めの合図で終わる時も自分では返さない)。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- (ReportReady True "待機"))
  (<- lock Semaphore (CreateNamedSemaphore LOCK))
  (<- (AcquireSemaphore lock))
  (<- at int (now-epoch-ms))
  (<- (WriteShared HOLDER-ROW {"instance" ctx.instance "at" at}))
  (var beats 0)
  (while True
    (:= beats (+ beats 1))
    (<- (ReportReady True "lease を持つ"))
    (<- (Delay every)))
  beats)


(defk lease-writer [foundation every]
  {:pre [(: foundation Callable) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: hold-the-lease を土台で包む。"
  (<- beats int (foundation (hold-the-lease every)))
  beats)


(defsystem lease-writers [foundation]
  "見本の系: 名前付きの lease を持つ service を handoff で入れ替える(版 1)"
  (writer (lease-writer foundation 1.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5} :update "handoff"))


(defsystem lease-writers-v2 [foundation]
  "lease-writers の版 2(本体の引数 every を変えた — 入れ替わる)"
  (writer (lease-writer foundation 2.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5} :update "handoff"))
