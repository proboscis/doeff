;;; 版の file を替えて同じ container の中で worker の process を入れ替えても(deploy/boot.sh の keeper)、前の世代の上で退く handoff の
;;; service が、Pod の preStop の drain の上限(DRAIN_DEADLINE)より長い仕事を終わりまで回す事の検。
;;;
;;; 出自: 2026-11-15 の本番。走っている命令を持つ受け口(handoff・retiredSeconds 25200)の worker の版を上げると、keeper が前の世代の
;;; drain を preStop と同じ上限 DRAIN_DEADLINE(その機体の Pod の止めの猶予に合わせた 90 秒)で諦めて SIGTERM を送り、前の世代の worker が
;;; 退いた process を止めて、回していた命令が切れた。keeper の入れ替えは kubelet の猶予に縛られないので、上限は keeper_retire の
;;; KEEPER_RETIRE_DEADLINE(既定 86400 秒)に分けた。
;;;
;;; 手元の 1 台の cluster(test_worker_version_swap と同じ組 — 本物の coordinator と、配備と同じ deploy/boot.sh の keeper)で、DRAIN_DEADLINE を
;;; 2 秒にする。筋書き: 系 handoff-works(handoff・retiredSeconds 600・20 秒の仕事を持つ service — tests/fixtures/machine_app.hy)を宣言し、
;;; 版 A の世代の service が仕事を始めたら版の file を B に書き換える。
;;; 求める姿: 版 B の世代の service が起き、版 A の世代の service は退く知らせを受けても仕事を終わりまで回し(盤に done)、自分で終わり
;;; (盤に left)、その後に前の世代が退く(keeper の log の「が退いた」)。
;;; 直す前: keeper は drain の 2 秒の後に前の世代へ SIGTERM を送り、版 A の service は仕事の途中で止められた(盤に done が無い)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import pathlib [Path])
(import pytest)
(import doeff_core_effects [WriteText FileFailed])
(import doeff_cluster [Redeclare])
(import doeff_cluster [LocalMachine local-machine-cluster])
(import tests.fixtures.machine_app [handoff-works machine-foundation SWAP-PREFIX])
(import tests.test_local_machine [AppRepo app-repo leftover])
(import tests.test_worker_version_swap [WORKER WAIT-SECONDS Versions prepared-versions swap-machine-of board-commit board-commit-within
                                        worker-commit-within log-has-within])

;; 筋書きは仕事 20 秒と 2 つの世代の起動を待つので、既定の 60 秒より長く取る(倍率は根の conftest が掛ける)。
(val pytestmark (pytest.mark.timeout 150))

;; Pod の preStop の drain の上限(秒)— service の仕事(20 秒)より十分短い。
(val DRAIN-DEADLINE "2")
;; 版 A の service の仕事の終わりを待つ上限(秒)— 仕事 20 秒に、待ちの上限を足す。
(val WORK-SECONDS 20.0)


(defrecord HoldSeen
  "筋書きが読んだ姿: old-begun / new-begun = 版 A・B の世代の service が仕事を始めた行の版・old-done = 版 A の service の仕事の終わりの
   行の版(None = 書かれない — 仕事が切れた)・old-left = 版 A の service が退く知らせを受けて自分で終わったか・elder-left = keeper の log に
   前の世代の退きが出たか。"
  (#^ str old-begun)
  (#^ str new-begun)
  (#^ (| str None) old-done)
  (#^ bool old-left)
  (#^ bool elder-left))


(defk swapped-during-the-hold [versions version-file log scale]
  {:pre [(: versions Versions) (: version-file str) (: log str) (: scale float)] :post [(: % HoldSeen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 版 A の世代の名乗りの後に系を宣言し、service が仕事を始めたら版の file を B に書き換え、版 B の service の始まり・版 A の
   service の仕事の終わりと退き・前の世代の退きを順に待って読むため。"
  (val wait (* WAIT-SECONDS scale))
  (<- _old-boot str (worker-commit-within versions.old wait))
  (<- _declared tuple (Redeclare (handoff-works machine-foundation)))
  (val old-row (+ SWAP-PREFIX "hold/" versions.old "/"))
  (val new-row (+ SWAP-PREFIX "hold/" versions.new "/"))
  (<- old-begun str (board-commit-within (+ old-row "begun") wait))
  (<- wrote (WriteText version-file versions.new))
  (assert (not (isinstance wrote FileFailed)) wrote)
  (<- _new-boot str (worker-commit-within versions.new wait))
  (<- new-begun str (board-commit-within (+ new-row "begun") wait))
  ;; 前の世代の退きは、仕事の長さに待ちの上限を足して待つ。版 A の仕事の行はその後に読む(切れていれば書かれず None を読む)。
  (<- elder-left bool (log-has-within log "が退いた" (+ (* WORK-SECONDS scale) wait)))
  (<- old-done (| str None) (board-commit (+ old-row "done")))
  (<- old-left (| str None) (board-commit (+ old-row "left")))
  (HoldSeen :old-begun old-begun :new-begun new-begun :old-done old-done :old-left (is-not old-left None) :elder-left elder-left))


;; coordinator と worker を本物の子 process で起こすので subprocess-bytecode(根の conftest — tests/test_local_machine.hy の註)を取る。
(deftest test-swapping-the-worker-process-lets-a-retired-handoff-service-finish-work-longer-than-the-prestop-drain [tmp-path
                                                                                                                    subprocess-bytecode
                                                                                                                    #^ float deadline-scale-factor]
  (<- repo AppRepo (app-repo tmp-path))
  (val home (/ tmp-path "workers" WORKER))
  (.mkdir home :parents True)
  (<- versions Versions (prepared-versions home))
  (<- machine LocalMachine (swap-machine-of tmp-path repo versions deadline-scale-factor DRAIN-DEADLINE))
  (val log (str (/ home (+ WORKER ".log"))))
  (<- seen HoldSeen (local-machine-cluster (swapped-during-the-hold versions (str (/ home "version")) log deadline-scale-factor)
                                           :machine machine))
  ;; 版 A の世代の service が仕事を始め、版 B の世代の service が並んで起きた。
  (assert (= #(seen.old-begun seen.new-begun) #(versions.old versions.new)) seen)
  ;; 版 A の service は preStop の drain の上限より長い仕事を終わりまで回し、退く知らせを受けて自分で終わった(切られていない)。
  (assert (= seen.old-done versions.old) seen)
  (assert seen.old-left seen)
  ;; その後に前の世代が退いた。keeper は drain の間に前の世代を見失っていない。
  (assert seen.elder-left seen)
  (val text (.read-text (Path log) :encoding "utf-8"))
  (assert (not-in "drain の待ちを止めた" text) text)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))
