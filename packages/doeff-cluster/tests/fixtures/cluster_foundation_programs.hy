;;; 本番の形の土台(cluster-handlers を並べる)の見本 — クラスタの約束の effect を出す service が、この土台で閉じていることを確かめる。
(require doeff-hy.macros [defk <- val])
(import doeff [DoExpr with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.host_contract [host-reader environ-reader])
(import doeff_cluster.cluster_foundation [with-cluster-handlers])
(import doeff_cluster.readiness_model [ReportReady])
(import doeff_cluster.shared_model [ReadShared WriteShared])


(defk production-foundation [body]
  {:pre [(: body DoExpr)] :post [(: % "body の答え")] :needs #{"cluster-net"} :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の形の土台: scheduler・session の置き場・環境変数の読み・宿の読み・実時計の外側に、coordinator に話す組を並べる。"
  (<- answer (scheduled (with-handlers [(await-handler) (state) (environ-reader) host-reader (async-time-handler)]
                          (with-cluster-handlers body))))
  answer)


(defk beacon-body []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "盤に書き、読み、準備できたと報告して眠る(クラスタの約束の effect と時計と設定を出す)。"
  (<- rounds (Ask "BEACON_ROUNDS"))
  (<- (WriteShared "beacon/n" 1))
  (<- rows (ReadShared "beacon/"))
  (<- (ReportReady True "ok" "active"))
  (<- (Delay 1.0))
  None)


(defk beacon-job [foundation]
  {:pre [(: foundation (| type DoExpr))] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台で本体を包む job。"
  (<- (foundation (beacon-body)))
  None)
