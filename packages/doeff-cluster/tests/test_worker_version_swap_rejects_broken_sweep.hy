;;; 同じ container の中の世代の入れ替え(deploy/boot.sh の keeper)で、拍の掃除(SweepEnvs)で
;;; 落ちる新しい版 B を入れた時に、旧い世代 A の job が止まらず、
;;; keeper が版 B を退けて log に訳を出し、同じ版を起こし直さず、別の健康な版 C へは入れ替わる事の検。
;;;
;;; 手元の 1 台の cluster(test_worker_version_swap と同じ組 — 本物の coordinator と deploy/boot.sh の keeper)で
;;; 3 つの版 A(起動)・B(掃除で TypeError を投げる)・C(直した健康な版)を用意する。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import os)
(import sys)
(import pathlib [Path])
(import pytest)
(import doeff_core_effects [WriteText ReadText FileFailed])
(import doeff_time [Delay])
(import doeff_cluster [DetachedSubmitted LocalMachine local-machine-cluster])
(import doeff_cluster._sim.machine [BOOT-SCRIPT])
(import tests.fixtures.machine_app [SWAP-PREFIX])
(import tests.test_local_machine [AppRepo app-repo git leftover])
(import tests.test_worker_version_swap [WORKER WAIT-SECONDS BEFORE-SECONDS Versions swap-machine-of
                                        with-task-sender submitted-work board-commit-within
                                        worker-commit-within finished-phase-within log-has-within])

(val pytestmark (pytest.mark.timeout 180))


(defrecord ThreeVersions
  "検の doeff の 3 つの版: old = 起動の健康な版 A・broken = 掃除で落ちる版 B・fixed = 直した健康な版 C。"
  (#^ str old)
  (#^ str broken)
  (#^ str fixed))


(defk prepared-three-versions [home]
  {:pre [(: home Path)] :post [(: % ThreeVersions)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の作業の dir home に、3 つの commit(A・B・C)を持つ doeff の mirror と準備済みの root を置くため。
   版 B の root の起動の script だけは、本物の worker の入口を起こす直前に env_store.start_measuring を
   TypeError で落とす版に差し替えて起動する(拍の掃除 SweepEnvs の中で worker が落ちる形)。"
  (val src (/ home "doeff-src"))
  (.mkdir src :parents True)
  (<- (git home #("init" "-q" (str src))))
  (var shas #())
  (for [n #("a" "b" "c")]
    (.write-text (/ src "VERSION") n :encoding "utf-8")
    (<- (git src #("add" "VERSION")))
    (<- (git src #("commit" "-q" "-m" n)))
    (<- sha str (git src #("rev-parse" "HEAD")))
    (:= shas (+ shas #(sha))))
  (<- (git home #("clone" "-q" "--bare" (str src) (str (/ home "boot" "doeff.git")))))
  (val boot-text (.read-text (Path BOOT-SCRIPT) :encoding "utf-8"))
  (val broken-entry
       (+ "import sys, hy\n"
          "import doeff_cluster._worker.protocol.env_store as _es\n"
          "import doeff_cluster._worker.entry.main as _wm\n"
          "def _broken_sweep(*a, **kw):\n"
          "    raise TypeError('injected sweep crash')\n"
          "_es.start_measuring = _broken_sweep\n"
          "sys.argv = ['doeff_cluster._worker.entry.main', *sys.argv[1:]]\n"
          "_wm.main()\n"))
  (val broken-boot
       (.replace boot-text
                 "exec hy -m doeff_cluster._worker.entry.main "
                 "PYTHONDONTWRITEBYTECODE=1 exec python -B \"${0%/*}/broken_main.py\" "))
  (assert (!= broken-boot boot-text) "boot.sh の worker の起動行が見つからない")
  (for [#(i sha) (enumerate shas)]
    (val root (/ home "boot" "roots" sha))
    (val deploy (/ root "packages" "doeff-cluster" "deploy"))
    (.mkdir deploy :parents True)
    (if (= i 1)
        (do (.write-text (/ deploy "broken_main.py") broken-entry :encoding "utf-8")
            (.write-text (/ deploy "boot.sh") broken-boot :encoding "utf-8"))
        (.write-text (/ deploy "boot.sh") boot-text :encoding "utf-8"))
    (os.symlink sys.prefix (/ root ".venv"))
    (.touch (/ root ".doeff-boot-ready")))
  (ThreeVersions :old (get shas 0) :broken (get shas 1) :fixed (get shas 2)))


(defrecord RejectedE2ESeen
  "筋書きが読んだ姿: rejected-logged = 版 B を退けた行が log に出たか・rejected-file = 退けの直後の
   $keeper_dir/rejected の中身・elder-retired-during-b = 版 B の間に旧い世代が退いたか・
   starts-during-b = 版 B の間に keeper が起こした世代の数・before-begun / before-done / before-phase =
   版 A の task before の始まり・終わり・結末・after-begun = 版 C へ替わった後の task after の版・
   elder-retired-after-c = 版 C の committed の後に旧い世代が退いたか。"
  (#^ bool rejected-logged)
  (#^ str rejected-file)
  (#^ bool elder-retired-during-b)
  (#^ int starts-during-b)
  (#^ str before-begun)
  (#^ str before-done)
  (#^ str before-phase)
  (#^ str after-begun)
  (#^ bool elder-retired-after-c))


(defk swap-through-broken-to-fixed [versions version-file rejected-path log scale]
  {:pre [(: versions ThreeVersions) (: version-file str) (: rejected-path str) (: log str) (: scale float)]
   :post [(: % RejectedE2ESeen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 版 A で task before を走らせ、版の file を掃除で落ちる版 B へ替え、keeper が版 B を退けて
   旧い世代 A のまま before が終わりまで走り切る事を確かめてから、版の file を健康な版 C へ替えて入れ替える。"
  (val wait (* WAIT-SECONDS scale))
  (<- _old-boot str (worker-commit-within versions.old wait))
  (<- _before DetachedSubmitted (submitted-work "before" (* BEFORE-SECONDS scale)))
  (<- before-begun str (board-commit-within (+ SWAP-PREFIX "before/begun") wait))
  ;; 版の file を掃除で落ちる版 B へ替える。
  (<- wrote-b (WriteText version-file versions.broken))
  (assert (not (isinstance wrote-b FileFailed)) wrote-b)
  (<- rejected-logged bool (log-has-within log "この版を退け" wait))
  ;; 退けた後に少し待っても、同じ版 B を起こし直さず、旧い世代を退かせない事を確かめる。
  (<- (Delay 3.0))
  (<- log-during-b (ReadText log))
  (val text-during-b (if (isinstance log-during-b str) log-during-b ""))
  (val elder-retired-during-b (in "が退いた" text-during-b))
  (val starts-during-b (.count text-during-b "を起こした(pid"))
  (<- rej-text (ReadText rejected-path))
  (val rejected-file (if (isinstance rej-text str) (.strip rej-text) ""))
  ;; 版 A の task before は止まらずに版 A のまま終わりまで走り切る。
  (<- before-done str (board-commit-within (+ SWAP-PREFIX "before/done") wait))
  (<- before-phase str (finished-phase-within "before" wait))
  ;; 版の file を健康な版 C へ替えると、今度は版 C へ入れ替わり旧い世代が退く。
  (<- wrote-c (WriteText version-file versions.fixed))
  (assert (not (isinstance wrote-c FileFailed)) wrote-c)
  (<- _fixed-boot str (worker-commit-within versions.fixed wait))
  (<- elder-retired-after-c bool (log-has-within log "が退いた" wait))
  (<- _after DetachedSubmitted (submitted-work "after" 1.0))
  (<- after-begun str (board-commit-within (+ SWAP-PREFIX "after/begun") wait))
  (RejectedE2ESeen :rejected-logged rejected-logged
                   :rejected-file rejected-file
                   :elder-retired-during-b elder-retired-during-b
                   :starts-during-b starts-during-b
                   :before-begun before-begun
                   :before-done before-done
                   :before-phase before-phase
                   :after-begun after-begun
                   :elder-retired-after-c elder-retired-after-c))


(deftest test-version-swap-keeps-elder-work-and-rejects-version-crashing-in-sweep [tmp-path subprocess-bytecode
                                                                                  #^ float deadline-scale-factor]
  (<- repo AppRepo (app-repo tmp-path))
  (val home (/ tmp-path "workers" WORKER))
  (.mkdir home :parents True)
  (<- versions ThreeVersions (prepared-three-versions home))
  (<- machine LocalMachine (swap-machine-of tmp-path repo (Versions :old versions.old :new versions.broken) deadline-scale-factor))
  (val log (str (/ home (+ WORKER ".log"))))
  (val rejected-path (str (/ home "keeper" "rejected")))
  (<- seen RejectedE2ESeen
      (local-machine-cluster
        (with-task-sender machine
          (swap-through-broken-to-fixed versions (str (/ home "version")) rejected-path log deadline-scale-factor))
        :machine machine))
  ;; 版 B は掃除で落ちたため退けられ、log に訳が出て、$keeper_dir/rejected に版 B が書かれた。
  (assert seen.rejected-logged seen)
  (assert (= seen.rejected-file versions.broken) seen)
  ;; 版 B の間、旧い世代 A は drain されず、同じ版 B を起こし直していない(起こしたのは A と B の 1 回ずつの 2 回)。
  (assert (not seen.elder-retired-during-b) seen)
  (assert (= seen.starts-during-b 2) seen)
  ;; 版 A の task before は止まらずに版 A のまま finished まで走り切った。
  (assert (= #(seen.before-begun seen.before-done) #(versions.old versions.old)) seen)
  (assert (= seen.before-phase "finished") seen)
  ;; 別の健康な版 C へ替えると入れ替わりが完了し、旧い世代が退いて新しい task は版 C で走る。
  (assert seen.elder-retired-after-c seen)
  (assert (= seen.after-begun versions.fixed) seen)
  (val text (.read-text (Path log) :encoding "utf-8"))
  (assert (in (cut versions.broken 0 12) text) text)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))
