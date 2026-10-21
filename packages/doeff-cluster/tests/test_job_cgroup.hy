;; 子の job を置く cgroup(card ki-ed9dca403f58): zeus の機体の worker(zeus-native)は heartbeat を送る本体も子の job も同じ cgroup
;; (CPUWeight=idle・CPUQuota=200%)に居て、機体が混んだ刻に本体が拍の間の眠り(AwaitNextTick)から 10 秒起きられず、coordinator から
;; 消えた。機体の配備が本体と兄弟の cgroup を分け、worker は起こす子を兄弟の cgroup へ入れる(判断 = worker/core/job_cgroup・答え手 =
;; worker/protocol/job_cgroup・入口の読み = worker/entry/main の job-cgroup-placed)。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects.file_effects [PathKind PathStat ReadText StatPath])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome RunProcess StartProcess])
(import doeff_cluster.worker.core.job_cgroup [job-cgroup-procs placed-argv])
(import doeff_cluster.worker.protocol.job_cgroup [job-cgroup-placer])
(import doeff_cluster.worker.entry.main [job-cgroup-placed])

;; 機体の worker の本体が居る cgroup(unit の DelegateSubgroup=main — dotfiles の doeff-agent-worker.service)。
(val UNIT "/user.slice/user-1000.slice/user@1000.service/app.slice/doeff-agent-worker.service")
(val OWN (+ "0::" UNIT "/main\n"))
(val JOBS-PROCS (+ "/sys/fs/cgroup" UNIT "/jobs/cgroup.procs"))


(deftest test-the-job-cgroup-is-the-sibling-of-the-workers-own-cgroup
  ;; 本体の cgroup(…/main)の親の下の兄弟(…/jobs)へ子を置く。
  (<- procs str (job-cgroup-procs OWN "jobs"))
  (assert (= procs JOBS-PROCS) procs))


(deftest test-a-worker-without-a-sibling-to-place-its-jobs-refuses-to-start
  ;; 失敗ケース: 兄弟を置けない起動で子を本体の cgroup に黙って起こすと、本体がまた子と同じ CPU の扱いを受ける。起動の時に名指しで断る。
  (with [(pytest.raises ValueError :match "0::")]
    (! (job-cgroup-procs "1:name=systemd:/x\n" "jobs")))
  (with [(pytest.raises ValueError :match "根")]
    (! (job-cgroup-procs "0::/\n" "jobs")))
  (with [(pytest.raises ValueError :match "名前空間の外")]
    (! (job-cgroup-procs "0::/../../user.slice/x/main\n" "jobs")))
  (for [bad #("" "a/b" ".." ".")]
    (with [(pytest.raises ValueError :match "1 段の名")]
      (! (job-cgroup-procs OWN bad)))))


(defhandler reached-outside
  ;; 外の答え手の代わり: 外へ届いた子の効果をそのまま答えにして、置いたスコープを終える(子は起こさない)。
  (StartProcess [argv]
    :finish-reason "外へ届いた StartProcess を検が読む"
    (finish effect))
  (RunProcess [argv]
    :finish-reason "外へ届いた RunProcess を検が読む"
    (finish effect)))


(deftest test-the-placer-wraps-every-child-and-keeps-the-rest-of-the-effect
  ;; StartProcess(job の shim・待ちの子・検め)も RunProcess(git・uv の準備)も、argv だけを包み、cwd・env・出力の先はそのまま外へ回す。
  (val env #((EnvEntry :name "A" :value "1")))
  (val started (! (with-handlers [reached-outside (job-cgroup-placer JOBS-PROCS)]
                    (StartProcess :argv #("uv" "run" "x") :cwd "/w" :env env :env-mode EnvMode.REPLACE :stdout-path "/l" :stderr-path "/l"
                                  :process-group True))))
  (val ran (! (with-handlers [reached-outside (job-cgroup-placer JOBS-PROCS)]
                (RunProcess :argv #("git" "fetch") :cwd "/r" :env env :env-mode EnvMode.EXTEND))))
  (assert (= started.argv (! (placed-argv JOBS-PROCS #("uv" "run" "x")))) started.argv)
  (assert (= (cut started.argv 0 2) #("/bin/sh" "-c")) started.argv)
  (assert (= #(started.cwd started.env started.env-mode started.stdout-path started.process-group) #("/w" env EnvMode.REPLACE "/l" True)))
  (assert (= ran.argv (! (placed-argv JOBS-PROCS #("git" "fetch")))) ran.argv)
  (assert (= #(ran.cwd ran.env ran.env-mode) #("/r" env EnvMode.EXTEND))))


(deftest test-the-placed-child-writes-its-own-pid-before-it-runs [#^ Path tmp-path]
  ;; 本物の sh と子: 包んだ子は自分の pid を cgroup.procs(ここでは tmp の file)へ書いてから元の命令を exec する(pid は 1 つ)。
  (val procs (str (/ tmp-path "cgroup.procs")))
  (.write-text (Path procs) "")
  (<- outcome ProcessOutcome (with-handlers [subprocess-handler (job-cgroup-placer procs)]
                               (RunProcess :argv #("sh" "-c" "echo $$"))))
  (assert (= outcome.exit-code 0) outcome)
  (assert (= (.strip (.read-text (Path procs))) (.strip outcome.stdout)) "書いた pid と exec した命令の pid が同じ"))


(deftest test-a-child-that-cannot-enter-the-job-cgroup-does-not-run [#^ Path tmp-path]
  ;; 失敗ケース: 兄弟の cgroup へ入れない子を本体の cgroup で走らせると、本体と子が同じ CPU の扱いに戻る。入れなければ命令を走らせない。
  (val marker (/ tmp-path "ran"))
  (<- outcome ProcessOutcome (with-handlers [subprocess-handler (job-cgroup-placer (str (/ tmp-path "missing" "cgroup.procs")))]
                               (RunProcess :argv #("touch" (str marker)))))
  (assert (!= outcome.exit-code 0) outcome)
  (assert (not (.exists marker)) "入れなかった子の命令は走らない"))


(defhandler machine-files [#^ str own #^ (| PathKind None) procs-kind]
  ;; 引数に残す理由: 検ごとに機体の形(本体の cgroup と兄弟の有無)を替えて同じ答え手を並べる。
  ;; 機体の file の代わり: /proc/self/cgroup の中身と、兄弟の cgroup.procs の様子(None = 無い)。
  (ReadText [path]
    (resume own))
  (StatPath [path]
    (resume (PathStat :kind (or procs-kind PathKind.MISSING) :real-path path :size 0 :modified 0.0))))


(deftest test-the-worker-reads-its-job-cgroup-once-at-boot
  ;; 入口: 名が無ければ子を包まない(Pod の worker は今のまま)。名が在り兄弟の cgroup が在れば、その cgroup.procs を答える。
  ;; 失敗ケース: 機体の配備が兄弟を作り忘れた起動を、子を起こしてから(全部の job の失敗で)知る。起動の時に名指しで断る。
  (assert (is (! (with-handlers [(machine-files OWN PathKind.FILE)] (job-cgroup-placed None))) None))
  (assert (= (! (with-handlers [(machine-files OWN PathKind.FILE)] (job-cgroup-placed "jobs"))) JOBS-PROCS))
  (with [(pytest.raises ValueError :match "兄弟の cgroup が無い")]
    (! (with-handlers [(machine-files OWN None)] (job-cgroup-placed "jobs")))))
