;; 掃除が、同じ Pod の中の worker の外の使い手の root を消さない形の検(worker/protocol/env_store の root-users と
;; worker/core/env_upkeep の used-root-keys)。
;;
;; 実例(2026-11-13): 掃除の固定の集合(policy.pinned-env-keys)は宣言の job・worker の起こした process・温める表・準備中だけを数え、
;; worker が起こしていない process(長い命令の監督・CLI・hook — 機体の配備の設定の dir の木の bin の link から root の venv を使う)を
;; 知らなかった。使い手の版が新しい root へ移り、古い root が project ごとの新しい 2 つから外れて roots の合計が上限を越えると、
;; 古い root を使う監督が生きたまま root が消え、監督は終わりの知らせの前の遅い import(ModuleNotFoundError)で落ちた。木の link が指す
;; root を消して、その木から起きる物が起動のたびに落ちた事もある。だから:
;;   - 走っている process の作業 dir・引数・maps のどれかが指す root は、掃除が選ばない(固定の数 pinned と in-use に入る)
;;   - settings.link-dirs の下の symlink が指す root も、process が無い間も選ばない
;;   - 使い手の無い古い root は今までどおり選ぶ(対照)
;; 反例 = 直す前の形(掃除が worker の固定と他の世代の固定だけを見る)は、下の「消さない」の断言が赤。
;; 土台: file system は本物(検の tmp の dir の中だけ)。/proc は 2 通り: 検の tmp の dir に置いた /proc の形(cmdline・maps・cwd の link)と、
;; 本物の /proc(検が起こした子 process の作業 dir)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import os)
(import subprocess)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [subprocess-handler])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster [ENV-MARKER])
(import doeff_cluster [SweepEnvs])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster [env-host]
        doeff_cluster._worker.protocol.env_store [EnvSettings SWEEP-LOG SWEEP-DONE-LOG root-users])
(import doeff_cluster._worker.core.env_upkeep [used-root-keys])

;; 同じ project の完成した root 3 つ(いちばん古い OLD は使い手が無ければ消してよい・新しい 2 つは今の版と戻し先の版なので残す — #3732)。
(val OLD-NAME "0123456789abcdef01234567")
(val MID-NAME "456789abcdef0123456789ab")
(val NEW-NAME "89abcdef0123456789abcdef")
(val OLD-KEY (+ "env-" OLD-NAME))
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}
             "interpreter" "/usr/bin/python3"})
;; roots の合計の上限を 0 に置いて、必ず掃除させる。
(val CAP 0)
;; 掃除の終わりを待つ SweepEnvs の回数の上限と、回の間の秒(本物の時計 — 合わせて 10 秒)。
(val SWEEP-ROUNDS 1000)
(val SWEEP-PAUSE-SECONDS 0.01)
;; 本物の /proc の検で起こす子 process の生きる秒(検が終わりに止める — 長めに置く)。
(val CHILD-SECONDS "60")


(defrecord SweepLine
  "掃除の頭の行 1 つの欄(固定の数・worker の外の使い手の root の数・候補の数・選んだ数)。"
  (#^ int pinned)
  (#^ int in-use)
  (#^ int candidates)
  (#^ int chosen))


(defrecord NotedSweeps
  "ここまでに出た掃除の行: lines = 掃除の選びの行(出た順)・done = 掃除の終わりの行の数。"
  (#^ (get tuple #(SweepLine ...)) lines)
  (#^ int done))


(defeffect NotedSweepLines
  "ここまでに出た掃除の行を読む — 検だけの問い(sweep-lines-noted が答える)。"
  {:fields [] :answer NotedSweeps :tags {:context "doeff-cluster-test" :role "intent"}})


(defk count-field [fields name]
  {:pre [(: fields dict) (: name str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の行の欄 name の数を返すため(数でなければ -1 — 断言が名指す)。"
  (val value (.get fields name))
  (if (isinstance value int) value -1))


(defhandler sweep-lines-noted
  "外の世界の log の代役: 掃除の選びの行(SWEEP-LOG)の欄を出た順に覚え、終わりの行(SWEEP-DONE-LOG)を数え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (session var done 0)
  (SlogEffect []
    (when (= effect.msg SWEEP-DONE-LOG)
      (:= done (+ done 1)))
    (when (= effect.msg SWEEP-LOG)
      (val fields effect.kwargs)
      (<- pinned int (count-field fields "pinned"))
      (<- in-use int (count-field fields "in_use"))
      (<- candidates int (count-field fields "candidates"))
      (<- chosen int (count-field fields "chosen"))
      (:= lines (+ lines #((SweepLine :pinned pinned :in-use in-use :candidates candidates :chosen chosen)))))
    (resume None))
  (NotedSweepLines []
    (resume (NotedSweeps :lines lines :done done))))


(defk settings-of [tmp [proc-dir None] [link-dirs #()]]
  {:pre [(: tmp Path) (: proc-dir (| Path None)) (: link-dirs tuple)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker 1 つの設定を返すため: 状態の dir は tmp/state・root の置き場は tmp/shared/roots・/proc の置き場は proc-dir(None = 本物の
   /proc)・link-dirs = 下の symlink の先の root を消さない dir。準備の process と uv は何もしない `true`・roots の上限は 0。"
  (val own (/ tmp "state"))
  (.mkdir own :parents True :exist-ok True)
  (EnvSettings :state (str own) :shared (str (/ tmp "shared")) :uv-cache (str (/ tmp "shared" "uv-cache")) :hy-command "true"
               :platform "test" :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes CAP
               :proc-dir (if (is proc-dir None) "/proc" (str proc-dir)) :link-dirs (tuple (gfor d link-dirs (str d)))))


(defk shared-root [tmp name hours-ago]
  {:pre [(: tmp Path) (: name str) (: hours-ago int)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root の置き場に完成した root name を置くため(hours-ago = 印と dir の時刻を何時間前にするか)。中に venv の bin の形の dir と、印でない
   file kept を置く(消されたかを読む)。答え = root の path。"
  (val root (/ tmp "shared" "roots" name))
  (.mkdir (/ root "cli" ".venv" "bin") :parents True)
  (.write-text (/ root "kept") "x")
  (.write-text (/ root ENV-MARKER) (json.dumps MARKER))
  (val at (- (. (.stat (/ root ENV-MARKER)) st-mtime) (* 3600 hours-ago)))
  (os.utime (/ root ENV-MARKER) #(at at))
  (os.utime root #(at at))
  root)


(defk three-roots [tmp]
  {:pre [(: tmp Path)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "同じ project の root 3 つを置くため(OLD がいちばん古い — 使い手が無ければ掃除の候補)。答え = OLD の root の path。"
  (<- old Path (shared-root tmp OLD-NAME 2))
  (<- (shared-root tmp MID-NAME 1))
  (<- (shared-root tmp NEW-NAME 0))
  old)


(defk fake-process [proc pid [argv #()] [maps ""] [cwd None]]
  {:pre [(: proc Path) (: pid int) (: argv tuple) (: maps str) (: cwd (| Path None))] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の /proc の置き場に process 1 つの形を置くため(cmdline = NUL で区切った引数・maps = 読み込んだ file の行・cwd = 作業 dir への link)。"
  (val place (/ proc (str pid)))
  (.mkdir place :parents True)
  (.write-text (/ place "cmdline") (+ (.join "\x00" argv) (if argv "\x00" "")))
  (.write-text (/ place "maps") maps)
  (when (is-not cwd None)
    (os.symlink cwd (/ place "cwd")))
  None)


(defk on-envs [settings program]
  {:pre [(: settings EnvSettings) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を env-host と本物の答え手の下で回すため(掃除の行は env-host の外側の sweep-lines-noted が受ける)。"
  (<- answer tuple (scheduled (with-handlers [(state) (sync-time-handler) sweep-lines-noted os-file-handler subprocess-handler (env-host settings)]
                                       program)))
  answer)


(defk sweep-once [pinned]
  {:pre [(: pinned frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で掃除を 1 回終わりまで進め(終わりの行が出るまで SweepEnvs を撃つ)、出た掃除の選びの行を返すため。"
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs pinned))
    (<- seen NotedSweeps (NotedSweepLines))
    (when (> seen.done 0)
      (break))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  (<- noted NotedSweeps (NotedSweepLines))
  (assert (= noted.done 1) noted)
  noted.lines)


(defk users-of [settings]
  {:pre [(: settings EnvSettings)] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "設定 settings の worker の外の使い手の root のキーを、本物の file system の答え手で読むため。"
  (<- keys frozenset (with-handlers [os-file-handler] (root-users settings)))
  keys)


(deftest test-used-root-keys-reads-the-root-name-under-either-spelling-of-the-place
  ;; 置き場の直下の名が root の名の形の物だけを数える: 引数(書いたままの綴り)・作業 dir と link の先(実の path)のどちらの綴りでも。
  ;; 脇へ退けた名・置き場の外・置き場そのもの・名の形でない dir は数えない。
  (val places #("/w/state/roots" "/real/state/roots"))
  (val paths #((+ "/w/state/roots/" OLD-NAME "/cli/.venv/bin/python")
               (+ "/real/state/roots/" MID-NAME "/lib/libx.so")
               (+ "/real/state/roots/" NEW-NAME)
               (+ "/w/state/roots/." OLD-NAME ".swept.1")
               "/w/state/roots"
               "/w/state/roots/not-a-root/x"
               (+ "/w/state/rootsx/" OLD-NAME)
               "/usr/bin/python3"))
  (assert (= (! (used-root-keys places paths)) (frozenset #(OLD-KEY (+ "env-" MID-NAME) (+ "env-" NEW-NAME))))))


(deftest test-the-users-are-read-from-argv-maps-cwd-and-the-links [tmp-path]
  ;; 固定の集合に足す使い手の root: 引数の venv の python・maps の読み込んだ file・作業 dir の link・link-dirs の下の link の先。
  ;; 使い手の無い root と、先の無い link は入らない。
  (val roots (/ tmp-path "shared" "roots"))
  (<- (shared-root tmp-path OLD-NAME 0))
  (<- (shared-root tmp-path MID-NAME 0))
  (<- (shared-root tmp-path NEW-NAME 0))
  (val proc (/ tmp-path "proc"))
  (<- (fake-process proc 11 :argv #((str (/ roots OLD-NAME "cli" ".venv" "bin" "python")) "-m" "supervisor")))
  (<- (fake-process proc 12 :maps (+ "7f00-7f01 r-xp 00000000 08:01 42   " (str (/ roots MID-NAME "cli" ".venv" "lib.so")) " (deleted)\n"
                                     "7f02-7f03 r--p 00000000 00:00 0    [heap]\n")))
  (<- (fake-process proc 13 :argv #("sleep" "60") :cwd (/ roots NEW-NAME "cli")))
  (val trees (/ tmp-path "trees"))
  (.mkdir (/ trees "t1") :parents True)
  (os.symlink (/ roots OLD-NAME "cli" ".venv" "bin") (/ trees "t1" "bin"))
  (os.symlink (/ roots "fedcba9876543210fedcba98" "bin") (/ trees "t1" "gone"))
  (<- proc-settings EnvSettings (settings-of tmp-path :proc-dir proc))
  (<- by-proc frozenset (users-of proc-settings))
  (assert (= by-proc (frozenset #(OLD-KEY (+ "env-" MID-NAME) (+ "env-" NEW-NAME)))) by-proc)
  (<- link-settings EnvSettings (settings-of tmp-path :proc-dir (/ tmp-path "no-proc") :link-dirs #(trees)))
  (<- by-link frozenset (users-of link-settings))
  (assert (= by-link (frozenset #(OLD-KEY))) by-link))


(deftest test-the-sweep-chooses-the-old-root-when-nothing-uses-it [tmp-path]
  ;; 対照: 使い手の無い古い root は今までどおり消す(固定 0・候補 1・選び 1)。
  (<- old Path (three-roots tmp-path))
  (val proc (/ tmp-path "proc"))
  (<- (fake-process proc 21 :argv #("/usr/bin/python3" "-c" "pass")))
  (<- sweep-settings EnvSettings (settings-of tmp-path :proc-dir proc))
  (<- lines tuple (on-envs sweep-settings (sweep-once (frozenset))))
  (assert (= (lfor l lines #(l.pinned l.in-use l.candidates l.chosen)) [#(0 0 1 1)]) lines)
  (assert (not (.exists old)) "使い手の無い古い root は消す"))


(deftest test-the-sweep-keeps-the-old-root-a-live-process-uses [tmp-path]
  ;; worker が起こしていない process が古い root の venv の python で動いている間は、その root を選ばない。直す前は固定 0・候補 1・選び 1
  ;; で古い root が消えて赤。
  (<- old Path (three-roots tmp-path))
  (val proc (/ tmp-path "proc"))
  (<- (fake-process proc 31 :argv #((str (/ old "cli" ".venv" "bin" "python")) "-m" "supervisor_main")))
  (<- sweep-settings EnvSettings (settings-of tmp-path :proc-dir proc))
  (<- lines tuple (on-envs sweep-settings (sweep-once (frozenset))))
  (assert (= (lfor l lines #(l.pinned l.in-use l.candidates l.chosen)) [#(1 1 0 0)]) lines)
  (assert (.exists (/ old "kept")) "生きた process の使う root は消さない"))


(deftest test-the-sweep-keeps-the-old-root-a-placed-link-points-to [tmp-path]
  ;; process が無い間も、link-dirs の下の木の link が指す root は選ばない(その木から次に起きる物が落ちない)。
  (<- old Path (three-roots tmp-path))
  (val trees (/ tmp-path "trees"))
  (.mkdir (/ trees "t1") :parents True)
  (os.symlink (/ old "cli" ".venv" "bin") (/ trees "t1" "bin"))
  (<- sweep-settings EnvSettings (settings-of tmp-path :proc-dir (/ tmp-path "no-proc") :link-dirs #(trees)))
  (<- lines tuple (on-envs sweep-settings (sweep-once (frozenset))))
  (assert (= (lfor l lines #(l.pinned l.in-use l.candidates l.chosen)) [#(1 1 0 0)]) lines)
  (assert (.exists (/ old "kept")) "置いた木の link が指す root は消さない"))


(deftest test-the-sweep-keeps-the-old-root-a-real-process-works-in [tmp-path]
  ;; 本物の /proc: 検が起こした子 process の作業 dir が古い root の中に在る間は、その root を選ばない。子を止めた後の掃除は消す。
  (<- old Path (three-roots tmp-path))
  (<- real-settings EnvSettings (settings-of tmp-path))
  (val child (subprocess.Popen ["sleep" CHILD-SECONDS] :cwd (str (/ old "cli"))))
  (try
    (<- lines tuple (on-envs real-settings (sweep-once (frozenset))))
    (assert (= (lfor l lines #(l.in-use l.candidates l.chosen)) [#(1 0 0)]) lines)
    (assert (.exists (/ old "kept")) "本物の走っている process の作業 dir の root は消さない")
    (finally
      (.kill child)
      (.wait child)))
  (<- after tuple (on-envs real-settings (sweep-once (frozenset))))
  (assert (= (lfor l after #(l.in-use l.candidates l.chosen)) [#(0 1 1)]) after)
  (assert (not (.exists old)) "使い手が終わった root は消す"))
