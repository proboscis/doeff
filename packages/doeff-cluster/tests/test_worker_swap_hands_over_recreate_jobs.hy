;; 同じ名の worker の新しい世代を先に起こす入れ替え(本番の Deployment の RollingUpdate — AL-D2f / AL-D2k)
;; で、他の worker へ移せない recreate の job を、新しい世代が起こせるまで、かつ前の世代の Pod の止まりが始まるまで
;; 前の世代に残す。本番の Service は worker の Pod を振り先にし、Terminating の Pod を振り先から外すので、job が動く Pod と振り先の Pod が
;; 食い違う間は、その job へ届かない。
;;
;; 筋書き: worker w1 だけが job keeper(needs cluster-net・update recreate・止められるまで動く — tests/fixtures/working_programs.hy)を
;; 持てる。準備に 8 秒かかる(prepare-seconds — 新しい世代も準備し直す)。keeper が動き出した後に w1 の新しい世代を先に起こし
;; (RollWorker — 新しい世代は重なりを名乗る)、新しい Pod が Ready になった後(readinessProbe の代役 — coordinator の GET /workers/w1 の
;; ready)に旧い世代の preStop(DrainGeneration — Deployment が旧い Pod の削除を始め、Service の振り先が新しい Pod へ移る刻)を回し、
;; 旧い世代を止める(StopElder)。
;; 求める姿: 新しい Pod は、新しい世代が keeper を起こせる(準備と検めが揃った)まで Ready にならない。前の世代の keeper は振り先が
;; 移るまで動き続け、その後に止まる。新しい世代の keeper は前の世代の keeper が止まった後に起きる(2 つは重ならない)。振り先が移って
;; から新しい世代の keeper が起きるまでは、移しの間だけ(MAX-GAP-MS より短い)。
;; D2f の前(doeff L2027 の前): 新しい世代の名乗りと同時に前の世代の keeper が止められ、新しい世代の準備の 8 秒の間 誰も動かさなかった
;; (本番 10-11 17:36 JST の zeus で 6 分)。D2k の前: 新しい Pod は最初の返事で Ready になり、振り先が移ってから新しい世代の準備の
;; 8 秒の後まで keeper へ届かなかった(本番 10-11 18:44 JST の zeus で 2〜7 分)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [PlainText])
(import doeff_cluster [sim-cluster SimWorker SimProcess ProcessesOf RollWorker DrainGeneration StopElder ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [keeping-hosts])

(val PREPARE-SECONDS 8.0)
(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0 :prepare-seconds PREPARE-SECONDS))
;; 受け渡しの間の上限: 振り先が移って(前の世代の preStop の drain)から、前の世代の止め(stop-grace の内で終わる)と、新しい世代が印の
;; 外れを次の heartbeat の返事で受けて起こすまで。準備の秒(8 秒)より短い — 直す前の形(準備の秒だけ空く)を赤にする。
(val MAX-GAP-MS 5000)


(defrecord HandOverSeen
  "筋書きが読んだ姿: processes = keeper の process(起きた順)・switched-ms = 旧い Pod の preStop が始まった刻(Service の振り先が新しい
   Pod へ移る刻)・drained-ms = 旧い Pod の preStop が drained を受けた刻。"
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ int switched-ms)
  (#^ int drained-ms))


(defk generation-drained [name boot]
  {:pre [(: name str) (: boot str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "旧い Pod の preStop の代役: 世代 boot の drain を 1 秒ごとに頼み直し、drained を受けた刻を返す(上限 120 回)。"
  (var drained None)
  (var tries 0)
  (while (and (is drained None) (< tries 120))
    (<- answer dict (DrainGeneration name boot))
    (assert (= (get answer "status") 200) answer)
    (if (get answer "body" "drain" "drained")
        (do (<- at int (now-epoch-ms))
            (:= drained at))
        (do (:= tries (+ tries 1))
            (<- (Delay 1.0)))))
  (assert (is-not drained None) "旧い Pod の drain が終わらない")
  drained)


(defk generation-ready [name elder]
  {:pre [(: name str) (: elder str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の新しい Pod の readinessProbe の代役: coordinator が名 name の今の世代を elder でない世代と読み、ready(前の世代の job を全部
   受け取れる — heartbeat の返事の handoverPending が無く、worker が ready の file を書く判断と同じ)と答えるまで 1 秒ごとに待つ
   (上限 30 回)。"
  (var boot elder)
  (var ready False)
  (var tries 0)
  (while (and (or (= boot elder) (not ready)) (< tries 30))
    (<- read (| dict PlainText) (ReadCoordinator (+ "/workers/" name)))
    (:= boot (if (isinstance read dict) (.get read "boot" elder) elder))
    (:= ready (and (isinstance read dict) (is (.get read "ready") True)))
    (when (or (= boot elder) (not ready))
      (:= tries (+ tries 1))
      (<- (Delay 1.0))))
  (assert (and (!= boot elder) ready) "新しい世代が Ready にならない")
  boot)


(defk rolled-while-keeping []
  {:pre [] :post [(: % HandOverSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: keeper が動き出した後(準備の秒 + 4 秒)に w1 の新しい世代を先に起こし、新しい Pod が Ready になった後に旧い世代の
   preStop(drained まで)と止めを回し、2 秒待って読む。"
  (<- (Delay (+ PREPARE-SECONDS 4.0)))
  (<- elder (| str None) (RollWorker "w1"))
  (assert (is-not elder None))
  (<- _new str (generation-ready "w1" elder))
  (<- switched int (now-epoch-ms))
  (<- drained int (generation-drained "w1" elder))
  (<- (StopElder "w1" elder))
  (<- (Delay 2.0))
  (<- processes tuple (ProcessesOf "keeper"))
  (HandOverSeen :processes (tuple (sorted processes :key (fn [p] p.started-ms))) :switched-ms switched :drained-ms drained))


(deftest test-a-recreate-job-moves-to-the-new-generation-only-when-it-can-start-there
  (<- seen HandOverSeen (sim-cluster :notice-broker (MemoryBroker) :workers #(W1) (keeping-hosts sim-foundation) (rolled-while-keeping)))
  (assert (= (len seen.processes) 2) seen.processes)
  (val elder (get seen.processes 0))
  (val fresh (get seen.processes 1))
  ;; 前の世代の keeper は止まり、新しい世代の keeper はその後に起きて動き続ける(2 つは重ならない)。
  (assert (is-not elder.ended-ms None) elder)
  (assert (>= fresh.started-ms elder.ended-ms) #(elder fresh))
  (assert (is fresh.ended-ms None) fresh)
  ;; 前の世代の keeper は新しい世代の準備の間も、振り先が移るまで動き続け(振り先の Pod に keeper が居る)、振り先が移ってから
  ;; 新しい世代の keeper が起きるまでは移しの間だけ。
  (assert (>= elder.ended-ms seen.switched-ms) #(seen.switched-ms elder))
  (assert (< (- fresh.started-ms seen.switched-ms) MAX-GAP-MS) #((- fresh.started-ms seen.switched-ms) seen.switched-ms fresh))
  ;; 旧い Pod の preStop は、前の世代の keeper が止まった後に drained を受けた。
  (assert (>= seen.drained-ms elder.ended-ms) #(seen.drained-ms elder)))
