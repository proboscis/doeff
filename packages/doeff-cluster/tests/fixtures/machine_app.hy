;;; 手元の 1 台の cluster の検(tests/test_local_machine.hy の Redeclare・Crash — #3040)が、検の git の repo へこの package の中と同じ
;;; path(tests/fixtures/machine_app.hy・空の __init__.py 2 つと一緒に)で写して push する app の module。worker はこの module を、宣言の版
;;; (その repo の commit)の木から取り出して import する(配備と同じ版の木の道 — CODE_REPO_URL)。検の process は同じ名
;;; tests.fixtures.machine_app で静的に import して系を組む — 詰めた Program は module の最上位の関数を名で運ぶので、送り手と子が同じ名で
;;; import する。土台は本番の形(tests/fixtures/cluster_foundation_programs.hy の production-foundation と同じ並び)。
(require doeff-hy.macros [defk <- val])
(require doeff-cluster.macros [defsystem])
(import collections.abc [Callable])
(import doeff [DoExpr with-handlers])
(import doeff_core_effects [Ask])
(import doeff_core_effects [await-handler slog-handler state])
(import doeff_core_effects [http-production-handler])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [Spawn Task])
(import doeff_time [Delay async-time-handler])
(import doeff_hy._json_value [OpaqueJson])
(import doeff_cluster [os-environ-reader])
(import doeff_cluster [host-reader])
(import doeff_cluster [with-cluster-handlers])
(import doeff_cluster [ReportReady])
(import doeff_cluster [WriteShared])
(import doeff_cluster [AwaitRetirement])
(import doeff_cluster [pipe-retirement-notices])
(import doeff_cluster [Retired HandoffAbandoned])

;; 版の入れ替えの検(tests/test_worker_version_swap.hy)の盤の行の頭と、worker が子へ渡す版の環境変数の名
;; (検は WORKER_PASS_ENV にこの名を置く — 子は自分を走らせた worker の世代の版を読む)。
(val SWAP-PREFIX "swap/")
(val COMMIT-ENV "WORKER_DOEFF_COMMIT")


(defk machine-foundation [body]
  {:pre [(: body DoExpr)] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の形の土台: scheduler・構造化ログ・HTTP・session の置き場・環境変数の読み・宿の読み・実時計の外側に、coordinator に話す組を並べる
   (準備の報告を本物の coordinator へ送るため)。退く知らせ(AwaitRetirement)の本番の答え手 pipe-retirement-notices も並べる(状態の
   handler と scheduler の内側 — 入口 job_entry は handler を足さず、土台が並べる)。"
  (<- answer (scheduled (with-handlers [await-handler slog-handler (http-production-handler) (state) (os-environ-reader) host-reader (async-time-handler)
                                        pipe-retirement-notices]
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


;; --- 版の入れ替えの検の系---------------------------------------------------------------------------

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


;; --- 版の入れ替えで、退く handoff の service の仕事が終わりまで回るかの検の系(tests/test_worker_version_swap_keeps_handoff_work.hy) ---

(defk ready-beats-body []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備できたと 1 秒ごとに報告し続けるため(止められるまで — 仕事の間も Ready を名乗る)。"
  (while True
    (<- (ReportReady True "up" "active"))
    (<- (Delay 1.0)))
  None)


(defk handoff-work-body [seconds]
  {:pre [(: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "service: 準備の報告を並べ、走らせた worker の世代の版 <版> で盤の swap/hold/<版>/begun を書き、seconds 秒働いて swap/hold/<版>/done を
   書き、退く知らせ(AwaitRetirement)を待って swap/hold/<版>/left を書いて自分で終わるため(退く知らせを仕事の途中で受けても、仕事は
   終わりまで回す — 走っている命令を持つ受け口の代役)。"
  (<- commit str (Ask COMMIT-ENV))
  (<- _beats Task (Spawn (ready-beats-body)))
  (val row (+ SWAP-PREFIX "hold/" commit "/"))
  (<- (WriteShared (+ row "begun") (OpaqueJson.of {"commit" commit})))
  (<- (Delay seconds))
  (<- (WriteShared (+ row "done") (OpaqueJson.of {"commit" commit})))
  (<- _told (| Retired HandoffAbandoned) (AwaitRetirement))
  (<- (WriteShared (+ row "left") (OpaqueJson.of {"commit" commit})))
  commit)


(defk handoff-work-job [foundation seconds]
  {:pre [(: foundation Callable) (: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の入口: handoff-work-body を土台で包む。"
  (<- commit str (foundation (handoff-work-body seconds)))
  commit)


;; 仕事は 20 秒(検は preStop の drain の上限 DRAIN_DEADLINE を 2 秒にし、worker の止めの猶予より長い)。退いた process の寿命の上限
;; retiredSeconds 600 は検の長さより長い(上限の止めは検の外)。
(defsystem handoff-works [#^ Callable foundation]
  "版の入れ替えの検の系: handoff で入れ替える、20 秒の仕事を持つ service 1 つ(退いた旧の寿命の上限 600 秒)"
  (hold (handoff-work-job foundation 20.0) :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"local"} :update "handoff"
        :readiness {"windowSeconds" 5 "retiredSeconds" 600}))
