;; 2026-10-02 の本番の途絶 3 つを、実際の秒と順序のまま模擬の世界(sim-cluster — 本物の coordinator と本物の worker を仮想の時計で回す)で
;; 再現する筋書き(#2805・#2803 の子 H2)。H1(#2804 — 移せる先の無い job は担い手の途絶でも置き先を外さず、返事の印で worker も長い方の
;; 柵まで止めない)の失敗ケース tests/test_keep_when_cut_off.hy は、判断の 1 つずつと、丸めた秒の筋書き(起動の 8 秒後から 120 秒の途絶・
;; 47 秒の止まり・途絶の 10 秒後の 2 台目)を持つ。ここは今日の表(#2803 の本文)の秒と順序をそのまま載せ、筋書きの全体で次の 3 つを見る:
;;   - job が止められない: 移せる先の無い job の process は、筋書きの間ずっと同じ世代のまま動き続け、起こし直しが 0 回。
;;   - 2 か所で走らない: 条 C2 one-place-per-job(process の生きていた区間の列 → 重なり)が筋書きの全体で重なりを名指さない。
;;   - 戻った後に仕事が続く: 途絶が明けた後、担い手の heartbeat が届き直し(coordinator の見え方で生きている)、Service の ready が Ready に
;;     戻り、同じ process の準備の報告が coordinator に届き続け、担い手へ新しく出した task が置かれて終わる。
;; H1 の検に無い所: 秒を担い手の最後の heartbeat から数えて本番と揃える(47 秒・115 秒・34 秒目から 20.2 秒の coordinator の止まり・30 秒目の
;; 2 台目)・置ける worker が 1 台の service が 4 つ並ぶ・途絶の最中の coordinator の止まり(置き場から読み直す)を約束が越える・移せる先の
;; 在る service を同じ筋書きに混ぜる・明けた後の仕事の続き。
;;
;; 模擬と本番の差(この file が受ける物):
;;   - 13:53:37〜57 の coordinator の 20.2 秒の止まりは、本番では処理の流れの詰まり(作り直しは無い)。模擬の世界には詰まりを入れる口が無い
;;     ので、Pod の止まり(StopCoordinator — 優雅に止め、20.2 秒の後に同じ置き場から読み直す)で表す。worker から見える事(その間の要求に
;;     返事が無い)は同じで、加えて約束(KeepMark)が置き場から読み直される事まで通る。読み直しは止まっていた長さだけ worker の沈黙をずらす
;;     (api_policy.resume-after-downtime)ので、coordinator の数える沈黙が移し替えの期限に届くのは途絶の「期限 + 20.2 秒」目(期限 60 秒 —
;;     #2806 — で 80.2 秒目。本番の当時の期限 45 秒では戻った直後の 53 秒目)— どちらも途絶の明ける 115 秒目より前で、移し替えの期限を
;;     越える事は変わらない。途絶の最中に読む秒(MID-AFTER-REASSIGN-SECONDS)は期限から作る。
;;   - 13:26 の処理の止まりは、本番では job の process も I/O で止まっていた見込み。模擬の StallWorker は worker の拍だけを止め、子は動き続ける。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.remote_rules [remote-job])
(import doeff_cluster.coordinator.core.coordinator_invariants [one-place-per-job ProcessSpan])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimProcess SimReadiness ProcessesOf ReadinessOf ReportsOf ReadCoordinator
                             CoordinatorRuns CutWorker StallWorker StartWorker StopCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [pulses solo-pulses add-task sim-task-foundation])

;; 本番と同じ時間の設定(生存の窓 10 秒・fence 20 秒・移し替え 60 秒・長い方の柵 240 秒)。
(val T (ClusterTiming))
;; 13:53 の網の途絶の最中に読む秒: coordinator の数える沈黙が移し替えの期限に届いた後(期限 + coordinator の止まり 20.2 秒 + 余白 5 秒)。
;; 途絶の明ける 115 秒目より前でなければ筋書きが成り立たない(期限 60 秒で 85.2 秒目)。
(val MID-AFTER-REASSIGN-SECONDS (+ (/ T.reassign-after-ms 1000) 20.2 5.0))


;; --- 筋書きの表(秒は担い手の最後の heartbeat から — #2803 の本文の表)---------------------------------------------------

(defrecord Cut
  "表の 1 行: worker hosts の網を、途絶の始まりから until 秒目まで切る(その worker と子の要求は coordinator に届かない — 本番の DNS の
   失敗と接続の失敗を含む)。"
  (#^ tuple hosts)
  (#^ float until))


(defrecord Stall
  "表の 1 行: worker host の処理を、途絶の始まりから until 秒目まで止める(heartbeat を送らない・送りの失敗が無いので fence も効かない)。"
  (#^ str host)
  (#^ float until))


(defrecord PauseCoordinator
  "表の 1 行: 途絶の at 秒目に coordinator を seconds 秒止める(止めて同じ置き場から読み直す — 頭の註の模擬と本番の差)。"
  (#^ float at)
  (#^ float seconds))


(defrecord Join
  "表の 1 行: 途絶の at 秒目に、止まったまま始まった worker name を起こす(能力の合う worker が登録される)。"
  (#^ float at)
  (#^ str name))


(defrecord Timeline
  "今日の途絶 1 つの筋書き(表): name = 本番の時刻と形・workers = 模擬の worker(SimWorker)・hosts = 途絶させる担い手(先頭の最後の
   heartbeat を秒の起点にする)・standby = job が置かれた後、途絶の前に起こす worker(移せる先の在る service の移り先)・steps = 表の行
   (Cut・Stall は途絶の始まりに、PauseCoordinator・Join は at 秒目に出す — 出す順)・silent = 担い手が coordinator に届かない秒・mid-at =
   途絶の最中に読む秒(移し替えの期限と coordinator の作り直しの後)・settle = 明けてから読むまでの秒・probe-needs = 明けた後に出す task の
   needs(先頭の担い手だけが持つ能力)。"
  (#^ str name)
  (#^ tuple workers)
  (#^ tuple hosts)
  (#^ tuple standby)
  (#^ tuple steps)
  (#^ float silent)
  (#^ float mid-at)
  (#^ float settle)
  (#^ frozenset probe-needs))


;; 13:26(処理の止まり): 13:26:01 頃の最後の heartbeat から 47 秒、担い手の処理が止まり heartbeat が送られない(送りの失敗も無い)。13:26:48 に
;; heartbeat が戻る。置ける worker は担い手 1 台。
(val STALL-1326
  (Timeline :name "13:26 処理の止まり 47 秒"
            :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])))
            :hosts #("w1") :standby #()
            :steps #((Stall :host "w1" :until 47.0))
            :silent 47.0 :mid-at 46.0 :settle 30.0 :probe-needs (frozenset ["cluster-net"])))

;; 13:26 の形(処理の止まり)で、止まりの長さを移し替えの期限 + 2 秒にした筋書き(今日の 47 秒は移し替え 45 秒を 2 秒越えた — #2806 で移し替えを
;; 60 秒にしたので、今日の 47 秒は期限の内になった)。「移せる先の無い job は、沈黙が移し替えを越えても外さない」を、期限の値を写さずに見る。
(val STALL-PAST-REASSIGN-SECONDS (+ (/ T.reassign-after-ms 1000) 2.0))
(val STALL-PAST-REASSIGN
  (Timeline :name "13:26 の形 処理の止まり 移し替え + 2 秒"
            :workers #((SimWorker :name "w1" :provides (frozenset ["cluster-net"])))
            :hosts #("w1") :standby #()
            :steps #((Stall :host "w1" :until STALL-PAST-REASSIGN-SECONDS))
            :silent STALL-PAST-REASSIGN-SECONDS :mid-at (- STALL-PAST-REASSIGN-SECONDS 1.0) :settle 30.0
            :probe-needs (frozenset ["cluster-net"])))

;; 13:53(網の途絶): 13:53:03 頃の最後の heartbeat から約 115 秒(13:54:59〜13:55:01 に再びつながる)、担い手の網が切れる。その最中の
;; 13:53:37(34 秒目)から 20.2 秒、coordinator が止まる。置ける worker が 1 台ずつの service 4 つ(本番の 4 job の形)と、移せる先の在る
;; service 1 つ(途絶の前に加わる w-spare へ移れる)を並べる。
(val CUT-HOSTS #("w-a" "w-b" "w-c" "w-d"))

(val CUT-1353
  (Timeline :name "13:53 網の途絶 115 秒・その 34 秒目から coordinator の止まり 20.2 秒"
            :workers #((SimWorker :name "w-a" :provides (frozenset ["solo-a" "cluster-net"]))
                       (SimWorker :name "w-b" :provides (frozenset ["solo-b" "cluster-net"]))
                       (SimWorker :name "w-c" :provides (frozenset ["solo-c" "cluster-net"]))
                       (SimWorker :name "w-d" :provides (frozenset ["solo-d" "cluster-net"]))
                       (SimWorker :name "w-spare" :provides (frozenset ["cluster-net"]) :starts-down True))
            :hosts CUT-HOSTS :standby #("w-spare")
            :steps #((Cut :hosts CUT-HOSTS :until 115.0) (PauseCoordinator :at 34.0 :seconds 20.2))
            :silent 115.0 :mid-at MID-AFTER-REASSIGN-SECONDS :settle 30.0 :probe-needs (frozenset ["solo-a"])))

;; 13:53 の秒のまま、途絶の 30 秒目(coordinator が止まる 4 秒前)に能力の合う 2 台目 w2 が登録される — 移せる先が「無い」から「在る」に
;; 変わる(本番では起きていない形)。
(val JOIN-DURING-1353
  (Timeline :name "13:53 の途絶の 30 秒目に 2 台目"
            :workers #((SimWorker :name "w1" :provides (frozenset ["solo-a" "cluster-net"]))
                       (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :starts-down True))
            :hosts #("w1") :standby #()
            :steps #((Cut :hosts #("w1") :until 115.0) (Join :at 30.0 :name "w2") (PauseCoordinator :at 34.0 :seconds 20.2))
            :silent 115.0 :mid-at MID-AFTER-REASSIGN-SECONDS :settle 30.0 :probe-needs (frozenset ["solo-a"])))


;; --- 表を進める -------------------------------------------------------------------------------------------------------

(defk starts-at [step]
  {:pre [(: step (| Cut Stall PauseCoordinator Join))] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "表の 1 行を出す秒(途絶の始まりからの秒)を決めるため — 網の切れと処理の止まりは始まりに、coordinator の止まりと 2 台目は at 秒目に。"
  (match step
    (Cut) 0.0
    (Stall) 0.0
    (PauseCoordinator :at at) at
    (Join :at at) at))


(defk act [step since]
  {:pre [(: step (| Cut Stall PauseCoordinator Join)) (: since float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "表の 1 行を、途絶の始まりから since 秒目の今に出すため(網の切れと処理の止まりは until 秒目に明けるよう、残りの秒だけ)。"
  (match step
    (Cut :hosts hosts :until until) (for [host hosts] (<- (CutWorker host (- until since))))
    (Stall :host host :until until) (<- (StallWorker host (- until since)))
    (PauseCoordinator :seconds seconds) (<- (StopCoordinator seconds))
    (Join :name name) (<- (StartWorker name)))
  None)


(defk play [timeline]
  {:pre [(: timeline Timeline)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "表を仮想の時計で進めるため: 先頭の担い手の最後の heartbeat(coordinator の見え方の silentMs)を途絶の始まりとし、表の行をその秒に出す。
   答え = 途絶の始まりの時刻(epoch ms)。"
  (<- view dict (ReadCoordinator (+ "/workers/" (get timeline.hosts 0))))
  (<- now int (now-epoch-ms))
  (val silent-ms (get view "silentMs"))
  (var clock (/ silent-ms 1000.0))
  (for [step timeline.steps]
    (<- at float (starts-at step))
    (when (> at clock)
      (<- (Delay (- at clock)))
      (:= clock at))
    (<- (act step clock)))
  (- now silent-ms))


(defk wait-until [zero seconds]
  {:pre [(: zero int) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "途絶の始まり zero(epoch ms)から seconds 秒目まで待つため。"
  (<- now int (now-epoch-ms))
  (val left (- (+ zero (int (* 1000 seconds))) now))
  (when (> left 0)
    (<- (Delay (/ left 1000.0))))
  None)


;; --- 読み -------------------------------------------------------------------------------------------------------------

(defrecord Moment
  "job 1 つの、ある時刻の読み: processes = その job の process の列(SimProcess・起こした順)・ready = coordinator の Service の ready。"
  (#^ tuple processes)
  (#^ SimReadiness ready))


(defrecord HostSeen
  "worker 1 台の coordinator の見え方(/workers/<名>): alive = 生きていると数えるか・silent-ms = 最後の heartbeat からの時間。"
  (#^ str name)
  (#^ bool alive)
  (#^ int silent-ms))


(defrecord JobSeen
  "job 1 つの筋書きの読み: job = 名・first = 途絶の前に動いていた process・mid = 途絶の最中の読み・after = 明けて settle 秒の読み・
   beats = 明けた後に first の世代から coordinator に届いた準備の報告の数。"
  (#^ str job)
  (#^ SimProcess first)
  (#^ Moment mid)
  (#^ Moment after)
  (#^ int beats))


(defrecord TimelineSeen
  "筋書き 1 つの読み: zero-ms = 途絶の始まり(epoch ms)・jobs = job ごとの読み(JobSeen — 宣言の順)・mid-hosts / hosts = 途絶の最中と明けた
   後の worker の見え方(HostSeen — 表の workers の順)・runs = coordinator の Pod の一生の列・probe = 明けた後に先頭の担い手へ出した task の答え。"
  (#^ int zero-ms)
  (#^ tuple jobs)
  (#^ tuple mid-hosts)
  (#^ tuple hosts)
  (#^ tuple runs)
  (#^ int probe))


(defk moments [jobs]
  {:pre [(: jobs tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job ごとの今の読み(Moment — jobs の順)を得るため。"
  (var read #())
  (for [job jobs]
    (<- processes tuple (ProcessesOf job))
    (<- ready SimReadiness (ReadinessOf job))
    (:= read #(#* read (Moment :processes processes :ready ready))))
  read)


(defk hosts-seen [workers]
  {:pre [(: workers tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker ごとの coordinator の見え方(HostSeen — workers の順)を得るため。"
  (var read #())
  (for [worker workers]
    (<- view dict (ReadCoordinator (+ "/workers/" worker.name)))
    (:= read #(#* read (HostSeen :name worker.name :alive (get view "alive") :silent-ms (get view "silentMs")))))
  read)


(defk beats-after [job instance since-ms]
  {:pre [(: job str) (: instance str) (: since-ms int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job の世代 instance から since-ms の後に coordinator に届いた準備の報告の数を数えるため。"
  (<- reports tuple (ReportsOf job))
  (len (lfor r reports :if (and (= r.kind "readiness") (= r.instance instance) (> r.at since-ms)) r)))


(defk outage [timeline jobs]
  {:pre [(: timeline Timeline) (: jobs tuple)] :post [(: % TimelineSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 8 秒待って(job が起き揃う)途絶の前の process を読み、standby の worker を起こして 5 秒待ち、表を進め、途絶の mid-at 秒目と
   明けて settle 秒目に読み、先頭の担い手だけが持つ能力の task を 1 つ出す。"
  (<- (Delay 8.0))
  (<- before tuple (moments jobs))
  (for [name timeline.standby]
    (<- (StartWorker name)))
  (when timeline.standby
    (<- (Delay 5.0)))
  (<- zero int (play timeline))
  (<- (wait-until zero timeline.mid-at))
  (<- mid tuple (moments jobs))
  (<- mid-hosts tuple (hosts-seen timeline.workers))
  (<- (wait-until zero (+ timeline.silent timeline.settle)))
  (<- after tuple (moments jobs))
  (<- hosts tuple (hosts-seen timeline.workers))
  (<- runs tuple (CoordinatorRuns))
  (<- probe int (remote-job (add-task sim-task-foundation 5) :needs timeline.probe-needs :name "after-outage"))
  (val end (+ zero (int (* 1000 timeline.silent))))
  (var seen #())
  (for [#(job b m a) (zip jobs before mid after)]
    (val first (get b.processes 0))
    (<- beats int (beats-after job first.instance end))
    (:= seen #(#* seen (JobSeen :job job :first first :mid m :after a :beats beats))))
  (TimelineSeen :zero-ms zero :jobs seen :mid-hosts mid-hosts :hosts hosts :runs runs :probe probe))


(defk spans-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの process の記録を条 C2 の判断に渡す区間の列にするため。"
  (tuple (gfor p processes (ProcessSpan :worker p.worker :started-ms p.started-ms :ended-ms p.ended-ms))))


(defk in-one-place [seen]
  {:pre [(: seen JobSeen)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job 1 つの筋書きの全体(明けた後の読みまでの process)で、条 C2 が重なりを名指さないことを確かめるため。"
  (<- spans tuple (spans-of seen.after.processes))
  (<- broken tuple (one-place-per-job spans))
  (assert (= broken #()) #(seen.job broken))
  None)


(defk host-of [seen name]
  {:pre [(: seen tuple) (: name str)] :post [(: % HostSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の見え方の列から name の物を引くため。"
  (next (gfor h seen :if (= h.name name) h)))


(defk kept-running [seen]
  {:pre [(: seen JobSeen)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "移せる先の無い job の約束を確かめるため: 途絶の最中も明けた後も、process は途絶の前の 1 つ(同じ世代・同じ pid)だけで動き続け(起こし直し 0 回)、
   途絶の最中の ready は止まりと読まない Unknown(印を渡してある旨)、明けた後は Ready で、同じ process の準備の報告が coordinator に届き続ける。"
  ;; 起こし直しの数(途絶の前の process の後に起きた process の数)が 0。
  (assert (= (- (len seen.after.processes) 1) 0) #("起こし直し" seen.job seen.after.processes))
  (val last (get seen.after.processes 0))
  (assert (= #(last.instance last.pid last.exit-code) #(seen.first.instance seen.first.pid None)) seen.after.processes)
  (assert (= seen.mid.processes #(seen.first)) seen)
  (assert (= seen.mid.ready.state "Unknown") seen.mid.ready)
  (assert (in "印" seen.mid.ready.reason) seen.mid.ready)
  (assert (= seen.after.ready.state "Ready") seen.after.ready)
  (assert (> seen.beats 0) seen)
  None)


(defk back-in-touch [seen names]
  {:pre [(: seen TimelineSeen) (: names tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "途絶させた担い手の heartbeat が明けた後に届き直していること(coordinator の見え方で生きていて、最後の heartbeat が生存の窓の内)と、
   明けた後に先頭の担い手へ出した task が置かれて終わったこと(add-task の答え 100 + 5)を確かめるため。"
  (for [name names]
    (<- host HostSeen (host-of seen.hosts name))
    (assert (and host.alive (< host.silent-ms T.lease-ms)) host))
  (assert (= seen.probe 105) seen.probe)
  None)


;; --- 筋書き ---------------------------------------------------------------------------------------------------------

(deftest test-a-stall-past-the-reassign-deadline-leaves-the-only-place-job-running-and-work-continues
  ;; 13:26 の形: 担い手の処理が止まり heartbeat が「移し替えの期限 + 2 秒」送られない(今日は 47 秒で、その時の期限 45 秒を 2 秒越えた)。
  ;; 置ける worker が 1 台の job は外されず、起こし直さず(以前は期限で外し、戻った担い手が止めて 67 秒かけて起こし直した)、明けた後は
  ;; heartbeat・Ready・報告・task の置きが続く。止まりの長さは期限から作る(値を写さない — 期限を動かしても主張が同じ所を見る)。
  (<- seen TimelineSeen (sim-cluster (pulses sim-foundation) (outage STALL-PAST-REASSIGN #("pulse")) :workers STALL-PAST-REASSIGN.workers))
  (val pulse (get seen.jobs 0))
  (<- (in-one-place pulse))
  (<- (kept-running pulse))
  (<- (back-in-touch seen STALL-PAST-REASSIGN.hosts))
  ;; 止まりの最中は coordinator の見え方で生きていない(沈黙が移し替えの期限を越えている)— 外さなかったのは期限の前だからではない。
  (<- mid-host HostSeen (host-of seen.mid-hosts "w1"))
  (assert (and (not mid-host.alive) (> mid-host.silent-ms T.reassign-after-ms)) mid-host)
  (assert (= (len seen.runs) 1) seen.runs))


(deftest test-todays-47-second-stall-is-within-the-reassign-deadline-and-changes-nothing
  ;; 今日の 13:26 の実際の 47 秒は、移し替えを 60 秒にした(#2806)後は期限の内 — coordinator の見え方の沈黙は期限に届かず、置ける worker が
  ;; 1 台の job は(印の有無に関わらず)外されず、起こし直さず、明けた後も仕事が続く。印に頼る見え方(途絶の最中の ready の理由)は見ない。
  (<- seen TimelineSeen (sim-cluster (pulses sim-foundation) (outage STALL-1326 #("pulse")) :workers STALL-1326.workers))
  (val pulse (get seen.jobs 0))
  (<- (in-one-place pulse))
  (assert (= (len pulse.after.processes) 1) #("起こし直し" pulse.after.processes))
  (assert (= pulse.mid.processes #(pulse.first)) pulse)
  (assert (= pulse.after.ready.state "Ready") pulse.after.ready)
  (<- (back-in-touch seen STALL-1326.hosts))
  (<- mid-host HostSeen (host-of seen.mid-hosts "w1"))
  (assert (< mid-host.silent-ms T.reassign-after-ms) mid-host)
  (assert (= (len seen.runs) 1) seen.runs))


(deftest test-a-115-second-cut-with-a-20-second-coordinator-stop-keeps-four-only-place-jobs-and-moves-the-movable-one
  ;; 13:53 型: 担い手 4 台の網が 115 秒切れ、その 34 秒目から coordinator が 20.2 秒止まって置き場から読み直す。置ける worker が 1 台ずつの
  ;; service 4 つは、印で止められず、読み直した coordinator も約束を保って外さず、起こし直しが 0 回(以前は fence 20 秒で自己停止し、
  ;; 明けた後に起こし直した)。明けた後は 4 台とも heartbeat が届き直し、4 つとも Ready・報告が続き、task も置かれる。
  ;; 同じ筋書きに混ぜた移せる先の在る service(roamer)は今までどおり: 担い手が fence(20 秒)で止め、coordinator が移し替えの期限の後に
  ;; 途絶していない w-spare へ移す — fence が先なので 2 か所で走らない。
  (val jobs #("pulse-a" "pulse-b" "pulse-c" "pulse-d" "roamer"))
  (<- seen TimelineSeen (sim-cluster (solo-pulses sim-foundation) (outage CUT-1353 jobs) :workers CUT-1353.workers))
  (for [job (cut seen.jobs 0 4)]
    (<- (in-one-place job))
    (<- (kept-running job)))
  (<- (back-in-touch seen CUT-HOSTS))
  ;; coordinator は途絶の最中に 1 度止まり、同じ置き場から作り直された。
  (assert (= (len seen.runs) 2) seen.runs)
  (val stopped (get seen.runs 0))
  (assert (= stopped.outcome "stopped") seen.runs)
  (assert (<= (+ seen.zero-ms 34000) stopped.ended-ms (+ seen.zero-ms 35000)) #(seen.zero-ms seen.runs))
  ;; 移せる先の在る service: 途絶した担い手の process は fence(20 秒)を越えて止まり(移し替えの期限より前)、移った先の process は
  ;; 移し替えの期限の後、途絶の明ける前に w-spare で起きる。重なりは無い。
  (val roamer (get seen.jobs 4))
  (assert (in roamer.first.worker CUT-HOSTS) roamer.first)
  (val old (get roamer.after.processes 0))
  (val moved (get roamer.after.processes -1))
  (assert (= old.exit-code -15) roamer.after.processes)
  (assert (<= (+ seen.zero-ms T.fence-ms) old.ended-ms (+ seen.zero-ms T.reassign-after-ms)) #(seen.zero-ms old))
  (assert (= #(moved.worker moved.exit-code) #("w-spare" None)) roamer.after.processes)
  (assert (<= (+ seen.zero-ms T.reassign-after-ms) moved.started-ms (+ seen.zero-ms 115000)) #(seen.zero-ms moved))
  (assert (= roamer.after.ready.state "Ready") roamer.after.ready)
  (<- (in-one-place roamer)))


(deftest test-a-capable-worker-joining-30-seconds-into-the-cut-does-not-take-the-promised-job
  ;; 途絶中の 2 台目: 担い手 w1 の網が 115 秒切れ、その 30 秒目に能力の合う w2 が登録され(移せる先が「無い」から「在る」に)、34 秒目から
  ;; coordinator が 20.2 秒止まって置き場から読み直す。印を渡した担い手からは、読み直した後も、移し替えの期限を過ぎても移さない — w2 で
  ;; process は起きず、2 か所で走らない。明けた後も w1 の process がそのまま動き続ける(担い手は条件を満たすので外さない)。
  (<- seen TimelineSeen (sim-cluster (pulses sim-foundation) (outage JOIN-DURING-1353 #("pulse")) :workers JOIN-DURING-1353.workers))
  (val pulse (get seen.jobs 0))
  ;; 移せる先は途絶の最中に在った(w2 は生きていると数えられ、w1 は沈黙が移し替えの期限を越えていた)。
  (<- joined HostSeen (host-of seen.mid-hosts "w2"))
  (<- holder HostSeen (host-of seen.mid-hosts "w1"))
  (assert joined.alive joined)
  (assert (and (not holder.alive) (> holder.silent-ms T.reassign-after-ms)) holder)
  (<- (in-one-place pulse))
  (<- (kept-running pulse))
  (assert (= (lfor p pulse.after.processes p.worker) ["w1"]) pulse.after.processes)
  (<- (back-in-touch seen #("w1" "w2")))
  (assert (= (len seen.runs) 2) seen.runs))
