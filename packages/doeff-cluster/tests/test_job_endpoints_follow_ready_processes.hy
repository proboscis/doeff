;; job の Service の振り先(EndpointSlice)を coordinator が job ごとの Ready から書く(coordinator/core/endpoints_policy)。同じ名の worker の
;; 新しい世代を先に起こす入れ替え(本番の Deployment の RollingUpdate — AL-D2)の間に旧い世代の process が落ちても、job の API に届かない
;; 間が無い。
;;
;; 出自: 2026-11-06 の RollingUpdate。job の Service は Pod の Ready ∧ Pod の label job.doeff.dev/<job>=placed で振っていた。新しい
;; Pod は受け渡しが済むまで NotReady(handover-pending — 遅い job が Ready を名乗るまで)で、振り先は旧い Pod だけ。その間に旧い Pod の
;; 先に移った job の process が 06:53:43 に落ち、新しい Pod にその job の Ready な process が在るのに、新しい Pod が Ready になる
;; 06:57:10 まで 3 分半その job の API に届かなかった。
;;
;; 筋書き: worker w1 が handoff の job を 2 つ持つ — server(起きてすぐ Ready)と slow(起きてから 60 秒 Ready を名乗らない — 受け渡しを
;; 止める。tests/fixtures/working_programs.hy の serving-beside-slow-start)。server には偽の k8s の job の Service apps/server(label
;; JOB-ENDPOINTS-LABEL)が在る。両方が Ready になった後に w1 の新しい世代を先に起こし(RollWorker)、新しい世代の server が Ready になった
;; 後に、旧い世代の server の process だけを落とす(CrashGeneration — 旧い Pod の process が落ちた形)。落とした刻から 1 秒ごとに
;; 20 秒、次の 3 つを読む:
;;   - 真実: Ready で active な server の process を持つ世代の Pod の IP(宿の process・準備の報告)
;;   - coordinator が書いた振り先(偽の k8s が受けた slice の書きの、その刻までの最後の物)
;;   - 前の形の振り先の模型: Ready な Pod の IP(今の世代は coordinator の GET /workers/w1 の ready — 新しい Pod の readinessProbe の
;;     代役・退いた世代は止めを頼まれるまで Ready)。どちらの Pod も label を持つ(job は名 w1 に置かれている)。
;; 求める姿: 各刻の coordinator の振り先に、Ready で active な server の process の在る Pod が在る(届かない間 0 秒)。落ちた旧い Pod は
;; 振り先から外れる。
;; 前の形の模型はこの筋書きで届かない刻を持つ(赤 — 筋書きが 2026-11-06 の形を再現している事の検。直す前の coordinator は slice を
;; 書かないので、上の求める姿の検も赤)。
;;
;; coordinator が止まっている間の振り先(endpoints_policy の頭の註 — 前の形より遅れる場面): coordinator が止まっている間に worker が
;; node ごと死んでも、slice は前のまま残る(死んだ Pod の IP が振り先に残る — 前の形は kubelet の readinessProbe が coordinator と無関係に
;; 外した)。coordinator が起き直し、準備の報告の window と lease の窓が過ぎた後に外す。
;;
;; 時間の設定は本番の既定(ClusterTiming — lease の窓 10 秒): 比で延ばした既定の世界では lease の窓が長く、振り先を書き始める刻と
;; 黙った世代を外す刻がこの筋書きの長さを越える。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [PlainText])
(import doeff_cluster._shared.intent.kube_service_model [JOB-ENDPOINTS-LABEL])
(import doeff_cluster [sim-cluster SimWorker SimGeneration RollWorker CrashGeneration GenerationsOf ProcessesOf ReportsOf ReadCoordinator
                       KubeCalls StopCoordinator KillWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [serving-beside-slow-start serving-hosts])

(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"])))
;; 偽の k8s の job の Service(上に載る系の deploy の宣言の形 — selector を持たず、label で job を名指す)。
(val SERVICES {"apps/server" {"metadata" {"name" "server" "namespace" "apps" "uid" "uid-server" "labels" {JOB-ENDPOINTS-LABEL "server"}}
                              "spec" {"type" "ClusterIP" "ports" [{"name" "http" "port" 80 "targetPort" 8080 "protocol" "TCP"}]}}})
(val SLICE-KEY "apps/server")
;; 落とした後に読む秒(1 秒ごと)。
(val WATCH-SECONDS 20)


(defrecord RouteSample
  "落とした後の 1 刻の姿: at = 刻・serving = Ready で active な server の process を持つ世代の Pod の IP・routed = coordinator が書いた
   振り先・modelled = 前の形の振り先の模型(Ready な Pod の IP)。"
  (#^ int at)
  (#^ frozenset serving)
  (#^ frozenset routed)
  (#^ frozenset modelled))


(defrecord RollSeen
  "筋書きが読んだ姿: elder = 旧い世代・crashed = 落とした process の数・samples = 落とした後の刻ごとの姿(古い順)。"
  (#^ str elder)
  (#^ int crashed)
  (#^ (get tuple #(RouteSample ...)) samples))


(defk serving-ips [job]
  {:pre [(: job str)] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "program"}}
  "真実: job の process のうち生きていて、ready で active の準備の報告を coordinator に届けた物を持つ w1 の世代の Pod の IP の組。"
  (<- processes tuple (ProcessesOf job))
  (<- reports tuple (ReportsOf job))
  (<- generations tuple (GenerationsOf "w1"))
  (val ready (frozenset (gfor r reports :if (and (= r.kind "readiness") r.ready (!= r.role "standby")) r.instance)))
  (val live (frozenset (gfor p processes :if (and (is p.ended-ms None) (in p.instance ready)) p.pid)))
  (frozenset (gfor g generations :if (and (not g.down) (any (gfor pid g.pids (in pid live)))) g.pod-ip)))


(defk routed-ips [at]
  {:pre [(: at int)] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator が刻 at までに書いた apps/server の振り先(偽の k8s が受けた slice の書きの最後の物 — 書いていなければ空)。"
  (<- calls tuple (KubeCalls))
  (val written (tuple (gfor c calls :if (and (= (get c "op") "slice") (= (get c "key") SLICE-KEY) (<= (get c "at") at)) c)))
  (if written (frozenset (get (get written -1) "addresses")) (frozenset)))


(defk modelled-ips []
  {:pre [] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "program"}}
  "前の形の振り先の模型: Ready な Pod の IP の組。今の世代の Pod は coordinator の GET /workers/w1 の ready(受け渡しが済み drain の
   無い — 新しい Pod の readinessProbe の代役)の間・退いた世代の Pod は止めを頼まれるまで Ready。どちらも label を持つ。"
  (<- read (| dict PlainText) (ReadCoordinator "/workers/w1"))
  (val current-ready (and (isinstance read dict) (is (.get read "ready") True)))
  (<- generations tuple (GenerationsOf "w1"))
  (frozenset (gfor g generations
                   :if (and (not g.down) (if g.elder (not g.stopping) current-ready))
                   g.pod-ip)))


(defk sample-routes [seconds]
  {:pre [(: seconds int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 秒ごとに seconds 回、刻ごとの姿(RouteSample)を読む。"
  (var samples #())
  (for [_ (range seconds)]
    (<- (Delay 1.0))
    (<- at int (now-epoch-ms))
    (<- serving frozenset (serving-ips "server"))
    (<- routed frozenset (routed-ips at))
    (<- modelled frozenset (modelled-ips))
    (:= samples (+ samples #((RouteSample :at at :serving serving :routed routed :modelled modelled)))))
  samples)


(defk elder-crashed-during-handover []
  {:pre [] :post [(: % RollSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 両方の job が Ready になった後(70 秒)に w1 の新しい世代を先に起こし、新しい世代の server が Ready になった後(10 秒 —
   slow は新しい世代でまだ Ready を名乗らないので受け渡しは済まない)に旧い世代の server の process だけを落とし、20 秒読む。"
  (<- (Delay 70.0))
  (<- elder (| str None) (RollWorker "w1"))
  (assert (is-not elder None))
  (<- (Delay 10.0))
  (<- crashed int (CrashGeneration "server" "w1" elder))
  (<- samples tuple (sample-routes WATCH-SECONDS))
  (RollSeen :elder elder :crashed crashed :samples samples))


(deftest test-the-coordinator-routes-a-job-to-a-ready-process-while-the-old-pod-loses-it-during-a-handover
  (<- seen RollSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :services SERVICES :timing (ClusterTiming)
                                 (serving-beside-slow-start sim-foundation) (elder-crashed-during-handover)))
  (assert (= seen.crashed 1) seen)
  ;; 落とした後の各刻に、Ready で active な server の process の在る Pod が振り先に在る(届かない間 0 秒)。
  (for [sample seen.samples]
    (assert sample.serving sample)
    (assert (& sample.routed sample.serving) sample))
  ;; 落ちた旧い Pod は振り先から外れる(最後の刻の振り先は真実と同じ)。
  (val last (get seen.samples -1))
  (assert (= last.routed last.serving) last))


(deftest test-the-pod-ready-and-label-routing-model-loses-the-job-in-the-same-handover
  ;; 筋書きが 2026-11-06 の形を再現している事の検: 前の形の振り先(Ready な Pod ∧ label)は、新しい Pod が受け渡しの間 NotReady なので
  ;; 旧い Pod だけを振り先にし、その process が落ちた後は Ready な process の在る Pod を振り先に持たない刻が在る。
  (<- seen RollSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :services SERVICES :timing (ClusterTiming)
                                 (serving-beside-slow-start sim-foundation) (elder-crashed-during-handover)))
  (assert (any (gfor sample seen.samples (and sample.serving (not (& sample.modelled sample.serving))))) seen.samples))


(defrecord StallSeen
  "coordinator を止めた筋書きの姿: before = 止める前の振り先・during = 止めている間(worker が死んだ後)の振り先・serving-during = その刻の
   真実・after = 起き直して落ち着いた後の振り先。"
  (#^ frozenset before)
  (#^ frozenset during)
  (#^ frozenset serving-during)
  (#^ frozenset after))


(defk worker-died-while-the-coordinator-is-stopped []
  {:pre [] :post [(: % StallSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: server が Ready になった後(30 秒)に coordinator を 40 秒止め、止めている間に w1 を node ごと殺す。止めている間と、起き直して
   40 秒後の振り先を読む。"
  (<- (Delay 30.0))
  (<- at int (now-epoch-ms))
  (<- before frozenset (routed-ips at))
  (<- (StopCoordinator 40.0))
  (<- (Delay 5.0))
  (<- _killed int (KillWorker "w1"))
  (<- (Delay 5.0))
  (<- now int (now-epoch-ms))
  (<- during frozenset (routed-ips now))
  (<- serving-during frozenset (serving-ips "server"))
  (<- (Delay 70.0))
  (<- later int (now-epoch-ms))
  (<- after frozenset (routed-ips later))
  (StallSeen :before before :during during :serving-during serving-during :after after))


(deftest test-the-route-keeps-a-dead-pod-while-the-coordinator-is-stopped-and-drops-it-after
  ;; 前の形より遅れる場面(endpoints_policy の頭の註): coordinator が止まっている間は、死んだ worker の Pod の IP が振り先に残る。
  (<- seen StallSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) :services SERVICES :timing (ClusterTiming)
                                  (serving-hosts sim-foundation) (worker-died-while-the-coordinator-is-stopped)))
  (assert seen.before seen)
  (assert (not seen.serving-during) seen)
  (assert (= seen.during seen.before) seen)
  ;; 起き直して落ち着いた後は、黙った世代の Pod を振り先から外す。
  (assert (= seen.after (frozenset)) seen))
