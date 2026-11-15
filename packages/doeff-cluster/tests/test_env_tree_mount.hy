;; root の repo の木を設定の命令で読み専用の mount として置き、root を消す・退ける前に外す形の検(env_translation の placed-tree・
;; unmounted-under と env_store の set-aside)。
;;
;; 実例: 新しい版の root を作るたびに、変わらない repo の木まで数万の file を hardlink で張り、掃除がその file を 1 つずつ消した(root 1 つの
;; inode の表の block の 77% が木)。木を置く命令(外の daemon が commit の木を 1 つだけ持って見せる)が設定された worker では:
;;   1 1 つの repo の commit だけが変わった新しい root の準備で、木の dir へ張る hardlink と木の dir から消す file の数の合計が、変わった
;;     repo の木の file の数を越えない(直す前 = 設定を読まない形は、変わらない repo の木を全部 hardlink で張って赤)
;;   2 命令が断った(daemon の保存先にまだ無い commit)時は、今までの hardlink の写しで準備が通る
;;   3 掃除が root を消す時は、外す命令が root を脇の名へ退ける前(rmtree の前)に元の path で走り、木の file は消しを通らない。外せない
;;     root は退けずに残す
;;   4 途中で止まった root を退けて準備し直す時も、退ける前に中の mount を外す
;; 土台: 1・2・4 は速い模擬(env_world の代役の命令 tree-mount・tree-unmount)、3 は本物の file system と本物の子 process(sh の script)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import json)
(import os)
(import re)
(import shlex)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects [LinkFiles RemoveTree ReadMemoryFiles MemoryFiles WriteText])
(import doeff_core_effects [state scheduled])
(import doeff_core_effects [os-file-handler subprocess-handler])
(import doeff_core_effects [SlogEffect])
(import doeff_time [SimClock sim-time-handler sync-time-handler Delay])
(import doeff_cluster [ENV-MARKER SweepEnvs env-host env-key RuntimeEnv])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster._worker.protocol.env_store [EnvSettings SWEEP-DONE-LOG TREE-UNMOUNTED-LOG])
(import doeff_cluster._worker.protocol.env_translation [template-argv prepared-once])
(import doeff_cluster._worker.core.env_prepare [prepare-env mounts-under])
(import doeff_cluster._worker.intent.env_prepare_model [PrepareRequest KnownRoot EnvReady])
(import doeff_cluster._shared.intent.runtime_env_model [EnvFailure])
(import doeff_cluster._sim.env_world [EnvWorld EnvWorldLog WorldCommit env-world read-world-log world-files
                                      TREE-MOUNT-TEMPLATE TREE-UNMOUNT-TEMPLATE])
(import tests.env_fixtures [LOCK sha-of app-commit lib-commit env-of base-world])

(val PLATFORM "linux-x86_64")


;; --- 純粋な読み -----------------------------------------------------------------------------

(deftest test-the-mounts-under-a-root-are-read-deepest-first-and-the-root-itself-is-not-one
  (val table (+ "1 0 0:30 / / rw - overlay overlay rw\n"
                "2 1 259:2 / /state/roots/k rw - ext4 /dev/x rw\n"
                "3 2 0:90 / /state/roots/k/app ro - fuse.tree tree ro\n"
                "4 3 0:91 / /state/roots/k/app/sub ro - fuse.tree tree ro\n"
                "5 2 0:92 / /state/roots/kk/app ro - fuse.tree tree ro\n"
                "6 2 0:93 / /state/roots/k/with\\040space ro - fuse.tree tree ro\n"
                "broken line\n"))
  (<- found tuple (mounts-under table "/state/roots/k"))
  (assert (= found #("/state/roots/k/with space" "/state/roots/k/app/sub" "/state/roots/k/app")) found))


(deftest test-the-command-template-fills-the-commit-and-the-dir-as-one-word-each
  (<- argv tuple (template-argv "/bin/nemo mount --read-only --commit {commit} '{dir}'" "abc" "/r/k/my app"))
  (assert (= argv #("/bin/nemo" "mount" "--read-only" "--commit" "abc" "/r/k/my app")) argv))


;; --- 準備(速い模擬) ----------------------------------------------------------------------

;; root の置き場の下の repo の木の dir(app・lib)の path(venv・.pyc の置き場・root の直下の file・脇の dir は入らない)。
(val TREE-PATH (re.compile r"^/state/roots/[^/.][^/]*/(app|lib)(/|$)"))


(defclass TreeWrites []
  "tree-writes の記録: linked = 木の dir へ張った hardlink の数・removed = 木の dir の下から翻訳の消し(RemoveTree)が消した file の数。"
  (defn #^ None __init__ [self]
    (setv #^ int self.linked 0)
    (setv #^ int self.removed 0)
    None))


(defhandler tree-writes [#^ TreeWrites marks]
  ;; 引数に残す理由: 検ごとに別の記録を持つ。翻訳(env-translation)が出す hardlink の張りと消しのうち、root の置き場の下の木の dir に
  ;; 当たる物を数え、外側の memory の置き場へそのまま渡す(venv の置き場・脇の dir と root の直下の file は数えない)。
  (LinkFiles [pairs]
    (setv marks.linked (+ marks.linked (len (lfor #(_ target) pairs :if (.match TREE-PATH target) target))))
    (<- answer effect)
    (resume answer))
  (RemoveTree [path]
    (<- store MemoryFiles (ReadMemoryFiles))
    (setv marks.removed (+ marks.removed (len (lfor f store.files :if (and (or (= f.path path) (.startswith f.path (+ path "/")))
                                                                           (.match TREE-PATH f.path))
                                                    f))))
    (<- answer effect)
    (resume answer)))


(defk prepare [env known]
  {:pre [(: env RuntimeEnv) (: known tuple)] :post [(: % (| EnvReady EnvFailure))]}
  "worker と同じ置き場(state/roots/<キー>)に root を準備する。"
  (<- key str (env-key env PLATFORM))
  (<- result (| EnvReady EnvFailure)
      (prepare-env (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                                   :compile-jobs None :known known :min-free-bytes 1024)))
  result)


(defk known-of [ready]
  {:pre [(: ready EnvReady)] :post [(: % tuple)]}
  "完成した root 1 つ → 次の準備の known(platform は印から読む)。"
  (<- files tuple (world-files ready.root))
  (val marker (json.loads (next (gfor f files :if (= f.path (.format "{}/{}" ready.root ENV-MARKER)) f.text))))
  #((KnownRoot :env ready.env :root ready.root :platform (get marker "platform") :made-ms 0 :hy-version (get marker "hyVersion"))))


(defk tree-hows [ready]
  {:pre [(: ready EnvReady)] :post [(: % dict)]}
  "完成した root の印の木の処理ステージの repo ごとの置き方(repo の名 → how)。"
  (<- files tuple (world-files ready.root))
  (val marker (json.loads (next (gfor f files :if (= f.path (.format "{}/{}" ready.root ENV-MARKER)) f.text))))
  (val tree (next (gfor s (get marker "stages") :if (= (get s "name") "tree") s)))
  (dfor p (get tree "parts") (get p "name") (get p "how")))


(defk run-counted [world marks scenario]
  {:pre [(: world EnvWorld) (: marks TreeWrites) (: scenario Program)] :post [(: % bool)]}
  "筋書きを仮想の時計と env-world の下で走らせる(翻訳のすぐ外に tree-writes を挟む)。"
  (<- handlers list (env-world world))
  (val counted (+ (cut handlers 0 -1) [(tree-writes marks) (get handlers -1)]))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) (with-handlers counted scenario))))
  ok)


(defk mounted-world [[refused (frozenset)]]
  {:pre [(: refused frozenset)] :post [(: % EnvWorld)]}
  "木を置く命令と外す命令の設定された世界(refused = 代役の daemon が断る commit)。"
  (<- world EnvWorld (base-world))
  (replace world :tree-mount TREE-MOUNT-TEMPLATE :tree-unmount TREE-UNMOUNT-TEMPLATE :mount-refused refused))


(defk one-repo-change-scenario [marks]
  {:pre [(: marks TreeWrites)] :post [(: % bool)]}
  "app の commit だけが変わった 2 つ目の root の準備で、木の dir へ張る hardlink と消す file の合計が、変わった app の木の file の数を
   越えない事・両方の木が mount で置かれて新しい commit の中身を見せる事を確かめる。"
  (<- first (| EnvReady EnvFailure) (prepare (env-of "app-1" "lib-1" LOCK) #()))
  (assert (isinstance first EnvReady) first)
  (<- known tuple (known-of first))
  (val before #(marks.linked marks.removed))
  (<- second (| EnvReady EnvFailure) (prepare (env-of "app-2" "lib-1" LOCK) known))
  (assert (isinstance second EnvReady) second)
  (<- app-2 WorldCommit (app-commit "app-2" LOCK "V = 2\n"))
  (val written (+ (- marks.linked (get before 0)) (- marks.removed (get before 1))))
  (assert (<= written (len app-2.files)) #(written marks.linked marks.removed))
  (<- hows dict (tree-hows second))
  (assert (= hows {"app" "mount" "lib" "mount"}) hows)
  (<- placed tuple (world-files (.format "{}/app/app/" second.root)))
  (assert (= (lfor f placed :if (.endswith f.path ".py") f.text) ["V = 2\n"]) placed)
  (<- log EnvWorldLog (read-world-log))
  (assert (= log.mounts 4) log)
  (assert (= log.expanded 0) "mount で置いた木は mirror から 1 file も展開しない")
  True)


(deftest test-a-mounted-root-links-and-removes-no-more-files-than-the-changed-tree
  (val marks (TreeWrites))
  (<- world EnvWorld (mounted-world))
  (<- ok bool (run-counted world marks (one-repo-change-scenario marks)))
  (assert ok))


(defk refused-scenario []
  {:pre [] :post [(: % bool)]}
  "代役の daemon が app-2 の commit を断る時、2 つ目の root の準備が通り、app の木は前の root の木からの hardlink の写しで置かれ、断りが
   準備の記録の行に残る事を確かめる(断られなかった lib は mount のまま)。"
  (<- first (| EnvReady EnvFailure) (prepare (env-of "app-1" "lib-1" LOCK) #()))
  (assert (isinstance first EnvReady) first)
  (<- known tuple (known-of first))
  (<- second (| EnvReady EnvFailure) (prepare (env-of "app-2" "lib-1" LOCK) known))
  (assert (isinstance second EnvReady) second)
  (<- hows dict (tree-hows second))
  (assert (= (get hows "lib") "mount") hows)
  (assert (in (get hows "app") #("copy" "expand")) hows)
  (<- placed tuple (world-files (.format "{}/app/app/" second.root)))
  (assert (= (lfor f placed :if (.endswith f.path ".py") f.text) ["V = 2\n"]) placed)
  (<- log EnvWorldLog (read-world-log))
  (assert (any (gfor n log.notes (and (in "mount で置けず" n) (in "not in the store" n)))) log.notes)
  True)


(deftest test-a-refused-tree-mount-falls-back-to-the-hardlink-copy
  (val marks (TreeWrites))
  (<- refused-sha str (sha-of "app-2"))
  (<- world EnvWorld (mounted-world (frozenset #(refused-sha))))
  (<- ok bool (run-counted world marks (refused-scenario)))
  (assert ok)
  (assert (> marks.linked 0) "断られた木は hardlink で写す"))


(defk broken-root-scenario []
  {:pre [] :post [(: % bool)]}
  "完成の印の無い(途中で止まった)root を準備し直す時、退ける前に中の mount を外し、退けた脇の dir に木の file が残らない事を確かめる。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- key str (env-key env PLATFORM))
  (val request (PrepareRequest :env env :key key :platform PLATFORM :root (.format "/state/roots/{}" key)
                               :compile-jobs None :known #() :min-free-bytes 1024))
  (<- first (| EnvReady EnvFailure) (prepared-once request))
  (assert (isinstance first EnvReady) first)
  ;; 完成の印を読めない中身に替える(準備の途中で止まった root と同じに読まれる)。
  (<- (WriteText (.format "{}/{}" first.root ENV-MARKER) "broken" :replace True))
  (<- again (| EnvReady EnvFailure) (prepared-once request))
  (assert (isinstance again EnvReady) again)
  (<- log EnvWorldLog (read-world-log))
  (assert (= #(log.mounts log.unmounts) #(4 2)) log)
  (<- aside tuple (world-files (.format "/state/roots/.{}.broken." key)))
  (assert (not (any (gfor f aside (or (.endswith f.path "/app/uv.lock") (.endswith f.path "/lib/lib/__init__.py"))))) aside)
  True)


(deftest test-a-broken-root-is-unmounted-before-it-is-set-aside
  (val marks (TreeWrites))
  (<- world EnvWorld (mounted-world))
  (<- ok bool (run-counted world marks (broken-root-scenario)))
  (assert ok))


;; --- 掃除(本物の file system と子 process) -------------------------------------------------------

(val OLD-NAME "0123456789abcdef01234567")
(val MID-NAME "456789abcdef0123456789ab")
(val NEW-NAME "89abcdef0123456789abcdef")
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}
             "interpreter" "/usr/bin/python3"})
;; 掃除の終わりを待つ SweepEnvs の回数の上限と、回の間の秒(本物の時計 — 合わせて 10 秒)。
(val SWEEP-ROUNDS 1000)
(val SWEEP-PAUSE-SECONDS 0.01)
;; 外す命令の代役: 呼ばれた dir と、その時の dir の下の file の数を log へ書き、木の file を daemon の cache の代役の dir へ移して(消しを
;; 通らずに残る)mount 点を消す。code が 0 でない時は何もせずにその code で終わる(外せない mount の代役)。
(val UNMOUNT-SCRIPT (+ "log=$1; keep=$2; code=$3; dir=$4\n"
                       "printf '%s %s\\n' \"$dir\" \"$(find \"$dir\" -type f | wc -l | tr -d ' ')\" >> \"$log\"\n"
                       "[ \"$code\" = 0 ] || exit \"$code\"\n"
                       "mkdir -p \"$keep\" && cp -R \"$dir\"/. \"$keep\"/ && rm -rf \"$dir\"\n"))


(defeffect NotedSweepDone
  "ここまでに出た #(掃除の終わりの行の数 mount を外せずに root を残した行の数) を読む — 検だけの問い(sweep-done-noted が答える)。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler sweep-done-noted
  "外の世界の log の代役: 掃除の終わりの行(SWEEP-DONE-LOG)と、mount を外せずに root を残した行(TREE-UNMOUNTED-LOG)を数える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var done 0)
  (session var kept 0)
  (SlogEffect []
    (when (= effect.msg SWEEP-DONE-LOG)
      (:= done (+ done 1)))
    (when (= effect.msg TREE-UNMOUNTED-LOG)
      (:= kept (+ kept 1)))
    (resume None))
  (NotedSweepDone []
    (resume #(done kept))))


(defk shared-root [tmp name hours-ago]
  {:pre [(: tmp Path) (: name str) (: hours-ago int)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root の置き場に完成した root name を置き、中に mount で置いた木の代役の dir r(file 2 つ)を置くため。答え = root の path。"
  (val root (/ tmp "shared" "roots" name))
  (.mkdir (/ root "r" "pkg") :parents True)
  (.write-text (/ root "r" "a.py") "A = 1\n")
  (.write-text (/ root "r" "pkg" "b.py") "B = 1\n")
  (.write-text (/ root ENV-MARKER) (json.dumps MARKER))
  (val at (- (. (.stat (/ root ENV-MARKER)) st-mtime) (* 3600 hours-ago)))
  (os.utime (/ root ENV-MARKER) #(at at))
  (os.utime root #(at at))
  root)


(defk mounted-settings [tmp code]
  {:pre [(: tmp Path) (: code int)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root 3 つ(古い OLD は掃除の候補)と、3 つの木 r を mount 点に持つ mount の表を置き、外す命令を代役の script(終わり code)にした worker の
   設定を返すため(roots の上限 0 — 必ず掃除する)。"
  (val roots (/ tmp "shared" "roots"))
  (val table (/ tmp "mountinfo"))
  (var lines #())
  (for [#(name hours) #(#(OLD-NAME 2) #(MID-NAME 1) #(NEW-NAME 0))]
    (<- root Path (shared-root tmp name hours))
    (:= lines (+ lines #((.format "90 1 0:90 / {} ro - fuse.tree tree ro\n" (/ (.resolve root) "r"))))))
  (.write-text table (.join "" lines))
  (val script (/ tmp "unmount.sh"))
  (.write-text script UNMOUNT-SCRIPT)
  (val own (/ tmp "state"))
  (.mkdir own :parents True :exist-ok True)
  (EnvSettings :state (str own) :shared (str (/ tmp "shared")) :uv-cache (str (/ tmp "shared" "uv-cache")) :hy-command "true"
               :platform "test" :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes 0 :proc-dir (str (/ tmp "no-proc"))
               :mount-table (str table)
               :tree-unmount (+ (.join " " (gfor w ["sh" (str script) (str (/ tmp "unmount.log")) (str (/ tmp "kept-tree")) (str code)]
                                                 (shlex.quote w)))
                                " {dir}")))


(defk sweep-until-done []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "掃除を 1 回終わりまで進め(終わりの行が出るまで SweepEnvs を撃つ)、#(終わりの行の数 root を残した行の数) を返すため。"
  (var seen #(0 0))
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs (frozenset)))
    (<- now tuple (NotedSweepDone))
    (:= seen now)
    (when (> (get seen 0) 0)
      (break))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  seen)


(defk swept [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "掃除を env-host と本物の答え手の下で 1 回終わりまで進めるため。"
  (<- answer tuple (scheduled (with-handlers [(state) (sync-time-handler) sweep-done-noted os-file-handler subprocess-handler (env-host settings)]
                                             (sweep-until-done))))
  answer)


(deftest test-the-sweep-unmounts-the-tree-before-it-sets-the-root-aside [tmp-path]
  (<- settings EnvSettings (mounted-settings tmp-path 0))
  (val old (/ tmp-path "shared" "roots" OLD-NAME))
  (<- seen tuple (swept settings))
  (assert (= seen #(1 0)) seen)
  (assert (not (.exists old)) "外した後の古い root は消す")
  ;; 外す命令は 1 回だけ、退ける前の元の path で、木の file 2 つが在る間に走った(消しが先に走れば file は 0 か path が脇の名)。
  (val logged (.splitlines (.read-text (/ tmp-path "unmount.log"))))
  (assert (= logged [(.format "{} 2" (/ (.resolve old) "r"))]) logged)
  ;; 木の file は消しを通らずに daemon の cache の代役に残る。
  (assert (= (sorted (gfor p (.rglob (/ tmp-path "kept-tree") "*") :if (.is-file p) (str (.relative-to p (/ tmp-path "kept-tree")))))
             ["a.py" "pkg/b.py"]))
  (assert (.exists (/ tmp-path "shared" "roots" MID-NAME "r" "a.py")) "選ばれない root の木は外さない"))


(deftest test-a-root-whose-tree-cannot-be-unmounted-is-kept [tmp-path]
  (<- settings EnvSettings (mounted-settings tmp-path 3))
  (val old (/ tmp-path "shared" "roots" OLD-NAME))
  (<- seen tuple (swept settings))
  (assert (= seen #(1 1)) seen)
  (assert (.exists (/ old "r" "a.py")) "外せない mount の root は退けずに残す")
  (assert (not (any (gfor p (.iterdir (/ tmp-path "shared" "roots")) (in ".swept." p.name)))) "脇の名へも退けない"))
