;;; 手元の 1 台の cluster の検(tests/test_local_machine.hy の Redeclare・Crash — #3040)が、検の git の repo へこの package の中と同じ
;;; path(tests/fixtures/machine_app.hy・空の __init__.py 2 つと一緒に)で写して push する app の module。worker はこの module を、宣言の版
;;; (その repo の commit)の木から取り出して import する(配備と同じ版の木の道 — CODE_REPO_URL)。検の process は同じ名
;;; tests.fixtures.machine_app で静的に import して系を組む — 詰めた Program は module の最上位の関数を名で運ぶので、送り手と子が同じ名で
;;; import する。土台は本番の形(tests/fixtures/cluster_foundation_programs.hy の production-foundation と同じ並び)。
(require doeff-hy.macros [defk <- val])
(require doeff-cluster.shared.entry.macros [defsystem])
(import collections.abc [Callable])
(import doeff [DoExpr with-handlers])
(import doeff_core_effects [Ask])
(import doeff_core_effects [await-handler slog-handler state])
(import doeff_core_effects [http-production-handler])
(import doeff_core_effects [scheduled])
(import doeff_time [Delay async-time-handler])
(import doeff_hy._json_value [OpaqueJson])
(import doeff_cluster [os-environ-reader])
(import doeff_cluster.shared.entry.host_reader [host-reader])
(import doeff_cluster.shared.entry.cluster_foundation [with-cluster-handlers])
(import doeff_cluster.shared.intent.readiness_model [ReportReady])
(import doeff_cluster.shared.intent.shared_model [WriteShared])

;; 版の入れ替えの検(tests/test_worker_version_swap.hy — card ki-6e83e03d146d)の盤の行の頭と、worker が子へ渡す版の環境変数の名
;; (検は WORKER_PASS_ENV にこの名を置く — 子は自分を走らせた worker の世代の版を読む)。
(val SWAP-PREFIX "swap/")
(val COMMIT-ENV "WORKER_DOEFF_COMMIT")


(defk machine-foundation [body]
  {:pre [(: body DoExpr)] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の形の土台: scheduler・構造化ログ・HTTP・session の置き場・環境変数の読み・宿の読み・実時計の外側に、coordinator に話す組を並べる
   (準備の報告を本物の coordinator へ送るため)。"
  (<- answer (scheduled (with-handlers [await-handler slog-handler (http-production-handler) (state) (os-environ-reader) host-reader (async-time-handler)]
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
  (ping (ping-job foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"local"} :readiness {"windowSeconds" 5}))


;; --- 版の入れ替えの検の系(card ki-6e83e03d146d)---------------------------------------------------------------------------

(defk generation-work-body [key seconds]
  {:pre [(: key str) (: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "task: 走らせた worker の世代の doeff の版を盤の swap/<key>/begun に書き、seconds 秒待ってから swap/<key>/done に同じ版を書くため
   (版の入れ替えをまたいで、走っていた task が切られずに終わりまで走るか・どの版の世代が走らせたかを見る)。"
  (<- commit str (Ask COMMIT-ENV))
  (<- (WriteShared (+ SWAP-PREFIX key "/begun") (OpaqueJson.of {"commit" commit})))
  (<- (Delay seconds))
  (<- (WriteShared (+ SWAP-PREFIX key "/done") (OpaqueJson.of {"commit" commit})))
  commit)


(defk generation-work [foundation key seconds]
  {:pre [(: foundation Callable) (: key str) (: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "task の入口: generation-work-body を土台で包む。"
  (<- commit str (foundation (generation-work-body key seconds)))
  commit)


(defsystem handoff-pings [#^ Callable foundation]
  "配備の cluster の handler の検の系: 準備を報告し続ける service 1 つ(入れ替えの形は handoff — 版を替えずに重ねて起こし直せる)"
  (ping (ping-job foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"local"} :readiness {"windowSeconds" 5} :update "handoff" :environ {"STEP" "1"}))
