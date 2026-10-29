;;; 版の file を替えると、同じ container(手元では同じ keeper の process)の中で worker の process が新しい版へ入れ替わる事の検
;;; (card ki-6e83e03d146d — deploy/boot.sh の頭の註の「版の file と keeper」)。
;;;
;;; 手元の 1 台の cluster(sim/machine.hy の local-machine-cluster — 本物の coordinator と、配備と同じ deploy/boot.sh で起こす worker)の
;;; worker を、配備の雛形と同じく版の file(WORKER_VERSION_FILE)と起動の版(WORKER_DOEFF_COMMIT)で起こす — boot.sh は keeper として回る。
;;; 版は検の doeff の mirror の 2 つの commit A・B で、どちらの root も準備済み(完成の印・venv はこの検の venv)にしておく — 版の入れ替えの
;;; 道(file の読み・役 prepare・世代の起こし・名乗り・前の世代の drain と止め)は本物のまま通り、Rust と uv の準備だけを省く。worker の
;;; PATH は image と同じ並び(土台の道具だけ — 検を走らせる機体の道具の包みを、名乗る道具の版の読みに巻き込まない)。
;;;
;;; 筋書き: 検の process が切り離した task before(検の app の module の generation-work — 走らせた世代の版を盤に書く・BEFORE-SECONDS 秒)を
;;; 出し、版 A の世代で走り出したら版の file を B に書き換え、版 B の名乗りの後に task after を出す。
;;; 求める姿:
;;;   - coordinator の Worker が版 B を名乗る(GET /workers/<名> の doeffCommit)。その時 before はまだ走っている(入れ替えが走っている仕事と
;;;     重なった)。keeper の process は 1 つのまま(起きた行が 1 つ — Pod を作り直さない)で、世代を 2 つ起こした。
;;;   - 版 B の名乗りの後に出した task after は、版 B の世代が走らせる(新しい仕事は新しい版に置かれる)。
;;;   - 版を替える前から走っていた before は、版 A の世代のまま終わりまで走り(盤に done・GET /detached の phase finished)、その後に
;;;     前の世代が退く(keeper の log の「が退いた」)。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import os)
(import sys)
(import pathlib [Path])
(import doeff [with-handlers Program EffectBase])
(import doeff_core_effects [WriteText ReadText FileFailed])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_time [Delay])
(import doeff_cluster.foundation.process_versions [this-process-versions])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [DetachedSubmitted])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.shared.protocol.detached [detached-cluster DetachedSender])
(import doeff_cluster.shared.entry.cluster_foundation [coordinator-route-options])
(import doeff_cluster.sim.local [SimWorker ReadCoordinator])
(import doeff_cluster.sim.machine [LocalMachine BOOT-SCRIPT local-machine-cluster coordinator-url])
(import tests.fixtures.machine_app [generation-work machine-foundation SWAP-PREFIX])
(import tests.test_local_machine [AppRepo app-repo free-port git leftover])

(val WORKER "w-swap")
;; image の PATH(deploy/base/Dockerfile の ENV PATH と同じ並び)。
(val IMAGE-PATH "/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin")
;; task before の長さ(秒)— 版の file の書き換えから新しい世代の名乗りまで(手元で数秒)より長くする。
(val BEFORE-SECONDS 12.0)
;; 1 つの待ちの上限(秒)と、読み直しの間隔(秒)。
(val WAIT-SECONDS 40.0)
(val PROBE-SECONDS 0.5)
;; task の終わっていない phase(GET /detached の phase — 読みの前の空を含む)。
(val OPEN-PHASES #("" "queued" "preparing" "assigned"))


(defrecord Versions
  "検の doeff の 2 つの版: old = 起動の版 A・new = 入れ替える版 B(どちらも 40 字の sha)。"
  (#^ str old)
  (#^ str new))


(defk prepared-versions [home]
  {:pre [(: home Path)] :post [(: % Versions)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の作業の dir home に、2 つの commit を持つ doeff の mirror(boot/doeff.git)と、その 2 つの準備済みの root(boot/roots/<sha> —
   起動の script はこの checkout の deploy/boot.sh の写し・venv はこの検の venv・完成の印)を置くため。"
  (val src (/ home "doeff-src"))
  (.mkdir src :parents True)
  (<- (git home #("init" "-q" (str src))))
  (var shas #())
  (for [n #("a" "b")]
    (.write-text (/ src "VERSION") n :encoding "utf-8")
    (<- (git src #("add" "VERSION")))
    (<- (git src #("commit" "-q" "-m" n)))
    (<- sha str (git src #("rev-parse" "HEAD")))
    (:= shas (+ shas #(sha))))
  (<- (git home #("clone" "-q" "--bare" (str src) (str (/ home "boot" "doeff.git")))))
  (for [sha shas]
    (val root (/ home "boot" "roots" sha))
    (val deploy (/ root "packages" "doeff-cluster" "deploy"))
    (.mkdir deploy :parents True)
    (.write-text (/ deploy "boot.sh") (.read-text (Path BOOT-SCRIPT) :encoding "utf-8") :encoding "utf-8")
    (os.symlink sys.prefix (/ root ".venv"))
    (.touch (/ root ".doeff-boot-ready")))
  (Versions :old (get shas 0) :new (get shas 1)))


(defk swap-machine-of [tmp-path repo versions]
  {:pre [(: tmp-path Path) (: repo AppRepo) (: versions Versions)] :post [(: % LocalMachine)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker 1 つの手元の 1 台を、配備の雛形と同じく版の file と起動の版で組むため(版の file は版 A を持つ)。worker は自分の版を子へ渡す
   (WORKER_PASS_ENV — 検の task が走らせた世代の版を読む)。"
  (<- port int (free-port))
  (val home (/ tmp-path "workers" WORKER))
  (val version-file (/ home "version"))
  (.write-text version-file versions.old :encoding "utf-8")
  (LocalMachine :work-dir (str tmp-path) :port port :workers #((SimWorker :name WORKER :provides (frozenset ["local"]) :task-reserve 0))
                :boot-seconds 120.0 :stop-grace 15.0 :code-repo repo.url :revision repo.sha
                :worker-env #((EnvEntry :name "PATH" :value IMAGE-PATH)
                              (EnvEntry :name "WORKER_DOEFF_COMMIT" :value versions.old)
                              (EnvEntry :name "WORKER_DOEFF_URL" :value (str (/ home "boot" "doeff.git")))
                              (EnvEntry :name "WORKER_VERSION_FILE" :value (str version-file))
                              (EnvEntry :name "DOEFF_KEEPER_DIR" :value (str (/ home "keeper")))
                              (EnvEntry :name "KEEPER_TICK_SECONDS" :value "1")
                              (EnvEntry :name "POD_UID" :value "pod-swap")
                              (EnvEntry :name "DRAIN_DEADLINE" :value "60")
                              (EnvEntry :name "WORKER_PASS_ENV" :value "WORKER_DOEFF_COMMIT"))))


(defk with-task-sender [machine body]
  {:pre [(: machine LocalMachine) (: body (| Program EffectBase))] :post [(: % "body の答え")]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの外側に、検の process から coordinator へ切り離した task を出す口を並べるため(本番の土台と同じ detached-cluster — 版は検の app の
   repo の commit・版の識別はこの process の物で、worker と同じ venv なので揃う)。"
  (<- url str (coordinator-url machine))
  (<- now int (now-epoch-ms))
  (<- route CoordinatorRoute (route-of url now))
  (<- options RouteOptions (coordinator-route-options))
  (<- versions dict (this-process-versions))
  (val sender (DetachedSender :revision machine.revision :versions versions :runtime-env None :deadline-seconds IDEMPOTENT-DEADLINE-SECONDS))
  (<- answer (with-handlers [(detached-cluster (RouteCell route) options sender)] body))
  answer)


(defk submitted-work [key seconds]
  {:pre [(: key str) (: seconds float)] :post [(: % DetachedSubmitted)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の task(走らせた世代の版を盤の swap/<key>/ に書き、seconds 秒で終わる)を key で 1 本出すため。"
  (<- submitted (submit-detached-task (generation-work machine-foundation key seconds) :key key :needs (frozenset ["local"])
                                      :name "generation-work"))
  (assert (isinstance submitted DetachedSubmitted) submitted)
  submitted)


(defk board-commit [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "盤の行 key の版(検の task が書いた {commit})を読むため(行が無ければ None)。"
  (<- rows dict (ReadCoordinator (+ "/board?prefix=" SWAP-PREFIX)))
  (val row (.get rows key))
  (if (isinstance row dict) (.get row "commit") None))


(defk board-commit-within [key]
  {:pre [(: key str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "盤の行 key が書かれるまで PROBE-SECONDS ごとに読み直し(上限 WAIT-SECONDS)、書かれた版を返すため。"
  (var commit None)
  (var waited 0.0)
  (while (and (is commit None) (< waited WAIT-SECONDS))
    (<- seen (| str None) (board-commit key))
    (:= commit seen)
    (when (is commit None)
      (<- (Delay PROBE-SECONDS))
      (:= waited (+ waited PROBE-SECONDS))))
  (assert (is-not commit None) (+ "盤の行が書かれない: " key))
  commit)


(defk worker-commit-within [commit]
  {:pre [(: commit str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の Worker が版 commit を名乗るまで読み直し(上限 WAIT-SECONDS)、その世代(boot)を返すため。"
  (var view {})
  (var waited 0.0)
  (while (and (!= (.get view "doeffCommit") commit) (< waited WAIT-SECONDS))
    (<- read dict (ReadCoordinator (+ "/workers/" WORKER)))
    (:= view read)
    (when (!= (.get view "doeffCommit") commit)
      (<- (Delay PROBE-SECONDS))
      (:= waited (+ waited PROBE-SECONDS))))
  (assert (= (.get view "doeffCommit") commit) #("Worker が版を名乗らない" commit view))
  (get view "boot"))


(defk finished-phase-within [key]
  {:pre [(: key str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "GET /detached/<key> の phase が終わりの phase(OPEN-PHASES の外)になるまで読み直し(上限 WAIT-SECONDS)、その phase を返すため。"
  (var phase "")
  (var waited 0.0)
  (while (and (in phase OPEN-PHASES) (< waited WAIT-SECONDS))
    (<- progress dict (ReadCoordinator (+ "/detached/" key)))
    (:= phase (.get progress "phase" ""))
    (when (in phase OPEN-PHASES)
      (<- (Delay PROBE-SECONDS))
      (:= waited (+ waited PROBE-SECONDS))))
  phase)


(defk log-has-within [log needle]
  {:pre [(: log str) (: needle str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の log に needle が出るまで読み直し(上限 WAIT-SECONDS)、出たかを返すため。"
  (var found False)
  (var waited 0.0)
  (while (and (not found) (< waited WAIT-SECONDS))
    (<- text (ReadText log))
    (:= found (and (isinstance text str) (in needle text)))
    (when (not found)
      (<- (Delay PROBE-SECONDS))
      (:= waited (+ waited PROBE-SECONDS))))
  found)


(defrecord SwapSeen
  "筋書きが読んだ姿: old-boot / new-boot = 版 A・B の世代(coordinator の Worker の boot)・before-begun / before-done = task before の
   始まりと終わりの版・before-done-at-swap = 版 B の名乗りを読んだ時に before の終わりが盤に在ったか・after-begun = task after の始まりの
   版・before-phase = 終わった後の before の GET /detached の phase・elder-left = keeper の log に前の世代の退きが出たか。"
  (#^ str old-boot)
  (#^ str new-boot)
  (#^ str before-begun)
  (#^ str before-done)
  (#^ bool before-done-at-swap)
  (#^ str after-begun)
  (#^ str before-phase)
  (#^ bool elder-left))


(defk swapped-during-the-work [versions version-file log]
  {:pre [(: versions Versions) (: version-file str) (: log str)] :post [(: % SwapSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 版 A の世代が名乗った後に task before を出し、走り出したら版の file を B に書き換え、版 B の名乗りを待って task after を出し、
   after の始まり・before の終わり・前の世代の退きを順に待って読むため。"
  (<- old-boot str (worker-commit-within versions.old))
  (<- _before DetachedSubmitted (submitted-work "before" BEFORE-SECONDS))
  (<- before-begun str (board-commit-within (+ SWAP-PREFIX "before/begun")))
  (<- wrote (WriteText version-file versions.new))
  (assert (not (isinstance wrote FileFailed)) wrote)
  (<- new-boot str (worker-commit-within versions.new))
  (<- done-at-swap (| str None) (board-commit (+ SWAP-PREFIX "before/done")))
  (<- _after DetachedSubmitted (submitted-work "after" 1.0))
  (<- after-begun str (board-commit-within (+ SWAP-PREFIX "after/begun")))
  (<- before-done str (board-commit-within (+ SWAP-PREFIX "before/done")))
  (<- before-phase str (finished-phase-within "before"))
  (<- left bool (log-has-within log "が退いた"))
  (SwapSeen :old-boot old-boot :new-boot new-boot :before-begun before-begun :before-done before-done
            :before-done-at-swap (is-not done-at-swap None) :after-begun after-begun :before-phase before-phase :elder-left left))


;; coordinator と worker を本物の子 process で起こすので subprocess-bytecode(根の conftest — tests/test_local_machine.hy の註)を取る。
(deftest test-changing-the-version-file-swaps-the-worker-process-in-place-and-lets-running-work-finish [tmp-path subprocess-bytecode]
  (<- repo AppRepo (app-repo tmp-path))
  (val home (/ tmp-path "workers" WORKER))
  (.mkdir home :parents True)
  (<- versions Versions (prepared-versions home))
  (<- machine LocalMachine (swap-machine-of tmp-path repo versions))
  (val log (str (/ home (+ WORKER ".log"))))
  (<- seen SwapSeen (local-machine-cluster (with-task-sender machine (swapped-during-the-work versions (str (/ home "version")) log))
                                           :machine machine))
  ;; 版 B の世代が名乗り、版 A の世代とは別の process(世代)。その時 before はまだ走っていた(入れ替えが走っている仕事と重なった)。
  (assert (!= seen.old-boot seen.new-boot) seen)
  (assert (not seen.before-done-at-swap) seen)
  ;; 版 B の名乗りの後に出した task は版 B の世代が走らせる。
  (assert (= seen.after-begun versions.new) seen)
  ;; 替える前から走っていた task は版 A の世代のまま終わりまで走った(切られていない)。
  (assert (= #(seen.before-begun seen.before-done) #(versions.old versions.old)) seen)
  (assert (= seen.before-phase "finished") seen)
  ;; 前の世代は仕事を終えた後に退き、keeper(Pod に当たる process)は 1 つのまま起きた行が 1 つ・起こした世代は 2 つ。
  (assert seen.elder-left seen)
  (val text (.read-text (Path log) :encoding "utf-8"))
  (assert (= (.count text "keeper として起きた") 1) text)
  (assert (= (.count text "を起こした(pid") 2) text)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))
