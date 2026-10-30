;; 同じ機体に同じ名の worker の 2 つの世代が並ぶ時(Deployment の RollingUpdate — AL-D2c・card ki-457bdbcc0236)、2 つの worker は同じ /work を
;; 使う。boot.sh は世代ごとの状態の dir($WORK_DIR/gen/<Pod の uid>/state)を --state-dir に、分け合う dir($WORK_DIR/state)を --shared-dir に
;; 渡す。ここでは、その 2 つの dir から worker が作る置き場が衝突しない事を縛る:
;;   * 版の木・job の cwd・Program・task の結果・温める子・入口の検め・拍の状態は、世代ごとに別の場所で、それぞれ自分の状態の dir の下。
;;   * job の log の dir は分け合い、名に入れる世代の印(boot の頭)は世代ごとに違う — 試みの番号は worker の process ごとに 1 から数えるので、
;;     印が無いと 2 つの世代が同じ file に書き足す(AL-D2j。宿が印を名に入れる事は test_logs_of_two_generations)。
;;   * 実行環境の root は分け合う(AL-D2g — 新しい世代は前の世代の準備した root を準備し直さない・錠と掃除の固定は
;;     test_env_roots_shared_by_generations。Rust の wheel の保存先を分け合う事は test_boot_wheel・準備の process へ --shared を渡す事は
;;     test_env_rules)。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster._worker.core.places [worker-places])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster._worker.protocol.env_store [EnvSettings env-root])

(val SHARED "/work/state")
(val OLD-STATE "/work/gen/0b6f-old/state")
(val NEW-STATE "/work/gen/5c1d-new/state")
(val GENERATION-FIELDS #("code" "jobs" "programs" "tasks" "warm" "probe" "status"))
;; 2 つの世代の名(main の boot-name と同じ形 — 16 進 32 字)。
(val OLD-BOOT "0b6f5e2a9c1d4f3e8a7b6c5d4e3f2a1b")
(val NEW-BOOT "5c1d9e8f7a6b5c4d3e2f1a0b9c8d7e6f")


(deftest test-two-generations-keep-their-own-places-and-share-only-the-logs
  (<- old (worker-places OLD-STATE SHARED OLD-BOOT))
  (<- new (worker-places NEW-STATE SHARED NEW-BOOT))
  (val old-paths (lfor field GENERATION-FIELDS (getattr old field)))
  (val new-paths (lfor field GENERATION-FIELDS (getattr new field)))
  (assert (all (gfor path old-paths (.startswith path (+ OLD-STATE "/")))) old-paths)
  (assert (all (gfor path new-paths (.startswith path (+ NEW-STATE "/")))) new-paths)
  (assert (not (& (set old-paths) (set new-paths))))
  (assert (= old.logs new.logs (+ SHARED "/logs")))
  ;; 分け合う log の dir の中の名は、世代ごとに違う印で分ける(印は boot の頭)。
  (assert (!= old.generation new.generation) #(old.generation new.generation))
  (assert (and (.startswith OLD-BOOT old.generation) (.startswith NEW-BOOT new.generation)) #(old.generation new.generation)))


(deftest test-a-boot-that-cannot-be-a-file-name-mark-is-refused
  ;; 世代の印は file の名にそのまま入るので、16 進の字でない boot(/ を含むなど)や短い boot は置き場を作る前に断る。
  (for [boot #("0b6f/../../etc" "0b6f")]
    (var refused False)
    (try
      (<- (worker-places OLD-STATE SHARED boot))
      (except [AssertionError]
        (:= refused True)))
    (assert refused boot)))


(deftest test-an-unsplit-worker-keeps-every-place-in-its-one-state-dir
  ;; --shared-dir を省いた worker(boot.sh の既定・WORKER_GENERATION_DIRS なし)は、今までどおり全部を 1 つの状態の dir に置く。
  (<- places (worker-places SHARED SHARED OLD-BOOT))
  (assert (= places.jobs (+ SHARED "/jobs")))
  (assert (= places.warm (+ SHARED "/warm")))
  (assert (= places.status (+ SHARED "/status.json")))
  (assert (= places.logs (+ SHARED "/logs"))))


(deftest test-two-generations-share-the-roots
  (val old (EnvSettings :state OLD-STATE :shared SHARED :uv-cache (+ SHARED "/uv-cache") :hy-command "hy" :platform "test"
                        :code-prepare PREPARE-TOOL :roots-cap-bytes (** 2 62)))
  (val new (EnvSettings :state NEW-STATE :shared SHARED :uv-cache (+ SHARED "/uv-cache") :hy-command "hy" :platform "test"
                        :code-prepare PREPARE-TOOL :roots-cap-bytes (** 2 62)))
  (<- old-root (env-root old "env-abc"))
  (<- new-root (env-root new "env-abc"))
  (assert (= old-root new-root (+ SHARED "/roots/abc")) #(old-root new-root))
  ;; --shared-dir を省いた worker は、今までどおり状態の dir の下に置く。
  (val unsplit (EnvSettings :state SHARED :uv-cache (+ SHARED "/uv-cache") :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL
                            :roots-cap-bytes (** 2 62)))
  (<- unsplit-root (env-root unsplit "env-abc"))
  (assert (= unsplit-root (+ SHARED "/roots/abc")) unsplit-root))
