;;; 手元の 1 台の cluster の検(tests/test_local_machine.hy の Redeclare・Crash — #3040)が、検の git の repo へこの package の中と同じ
;;; path(tests/fixtures/machine_app.hy・空の __init__.py 2 つと一緒に)で写して push する app の module。worker はこの module を、宣言の版
;;; (その repo の commit)の木から取り出して import する(配備と同じ版の木の道 — CODE_REPO_URL)。検の process は同じ名
;;; tests.fixtures.machine_app で静的に import して系を組む — 詰めた Program は module の最上位の関数を名で運ぶので、送り手と子が同じ名で
;;; import する。土台は本番の形(tests/fixtures/cluster_foundation_programs.hy の production-foundation と同じ並び)。
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [DoExpr with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler state])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.foundation.host_contract [os-environ-reader])
(import doeff_cluster.shared.entry.host_reader [host-reader])
(import doeff_cluster.shared.entry.cluster_foundation [with-cluster-handlers])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])


(defk machine-foundation [body]
  {:pre [(: body DoExpr)] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の形の土台: scheduler・構造化ログ・HTTP・session の置き場・環境変数の読み・宿の読み・実時計の外側に、coordinator に話す組を並べる
   (準備の報告を本物の coordinator へ送るため)。"
  (<- answer (scheduled (with-handlers [(await-handler) slog-handler (http-production-handler) (state) (os-environ-reader) host-reader (async-time-handler)]
                          (with-cluster-handlers body))))
  answer)


(defk ping-body []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備できたと 1 秒ごとに報告し続けるため(落とされるまで終わらない service — Ready と、落とした後の起こし直しを見る)。"
  (while True
    (<- (ReportReady True "up" "active"))
    (<- (Delay 1.0)))
  None)


(defk ping-job [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台で本体を包む job(土台は系の引数で受ける)。"
  (<- (foundation (ping-body)))
  None)


(defsystem pings [#^ Callable foundation]
  "手元の 1 台の検の系: 準備を報告し続ける service 1 つ"
  (ping (ping-job foundation) :replicas 1 :needs #{"local"} :readiness {"windowSeconds" 5}))
