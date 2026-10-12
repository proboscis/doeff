;; 実行環境の木の置き方(翻訳 env-translation の MaterializeTree — #3677・#3671 の子 2)の検 — 本物の git・cp・tar と本物の file system の上で。
;;
;; 模擬の世界(test_env_prepare)は、翻訳が出す命令の並びと数を確かめる。この検は、本物の file system でしか確かめられない性質を確かめる:
;;   1 写した file は写し元と同じ実体(hardlink)・変わった path は写し元と別の実体で、写し元の中身は前のまま(その場で上書きしない)
;;   2 できた木は、その commit を mirror から全部展開した木と同じ(増えた・消えた・変わった・種類が変わった path・名に空白・改行・- で
;;     始まる path・実行の権限・symlink)。追跡しない物(.venv・__pycache__・焼いた印)は写さない
;;   3 写し元の root を消しても、写した木の file の中身は残る(古い置き場の片づけが、そこから引き継いだ置き場を壊さない)
;;   4 命令の引数の長さの内に収めるため、path を数百ずつ渡す(PATHS-PER-COMMAND を越える数の差も正しい木になる)
;;   5 写せない時(写し元の commit が mirror に無い・写し元の file が欠けて cp が失敗する)は、途中まで写した木を空にして全部を展開に戻し、
;;     理由を答えに載せる
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import shutil)
(import stat)
(import subprocess)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.worker.intent.env_prepare_model [MaterializeTree TreeSource TreePlaced TREE-COPY TREE-EXPAND])
(import doeff_cluster.worker.protocol.env_translation [env-translation PATHS-PER-COMMAND])


(defk git [repo #* args]
  {:pre [(: repo Path) (: args tuple)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の手元の repo で git を 1 回撃ち、標準出力を返すため。"
  (.strip (. (subprocess.run ["git" "-C" (str repo) "-c" "user.name=t" "-c" "user.email=t@t" #* args] :check True :capture-output True :text True)
             stdout)))


(defk write-entry [repo rel value]
  {:pre [(: repo Path) (: rel str) (: value (| str tuple None))] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "repo の中の path rel を value に替えるため(None = 消す・str = 中身・#(\"exec\" 中身) = 実行の権限つき・#(\"link\" 先) = symlink)。"
  (val path (/ repo rel))
  (when (or (.is-symlink path) (.exists path))
    (.unlink path))
  (when (is-not value None)
    (.mkdir path.parent :parents True :exist-ok True)
    (match value
      (tuple) (if (= (get value 0) "link")
                  (.symlink-to path (get value 1))
                  (do (.write-text path (get value 1)) (.chmod path 0o755)))
      _ (.write-text path value)))
  None)


(defk commit-files [repo files]
  {:pre [(: repo Path) (: files dict)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "repo(無ければ作る)に files(相対 path → write-entry の value)を反映して commit し、その sha を返すため。"
  (when (not (.exists (/ repo ".git")))
    (.mkdir repo :parents True)
    (<- (git repo "init" "-q")))
  (for [#(rel value) (.items files)]
    (<- (write-entry repo rel value)))
  (<- (git repo "add" "-A"))
  (<- (git repo "commit" "-q" "--allow-empty" "-m" "c"))
  (<- sha str (git repo "rev-parse" "HEAD"))
  sha)


(defk entry-shape [path]
  {:pre [(: path Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "木の path 1 つの姿を比べられる値にするため(symlink = 先・file = 中身と実行の権限・dir)。"
  (cond
    (.is-symlink path) #("link" (os.readlink path) False)
    (.is-dir path) #("dir")
    True #("file" (.read-text path :errors "surrogateescape") (bool (& (. (.stat path) st-mode) stat.S_IXUSR)))))


(defk snapshot [root]
  {:pre [(: root Path)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "木の姿(相対 path → entry-shape)。dir も入れる — 消えた path の空の dir が残っていないかも見るため。"
  (val paths (tuple (.rglob root "*")))
  (var seen {})
  (for [p paths]
    (<- shape tuple (entry-shape p))
    (:= seen (| seen {(str (.relative-to p root)) shape})))
  seen)


(defk place [mirror commit dest source tmp]
  {:pre [(: mirror Path) (: commit str) (: dest Path) (: source (| TreeSource None)) (: tmp Path)] :post [(: % TreePlaced)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の準備の process と同じ並び(本物の答え手 + 翻訳 env-translation)で、MaterializeTree を 1 回出して木を置くため。"
  (val settings {"runtime-env.state" (str (/ tmp "state")) "runtime-env.shared" (str (/ tmp "state")) "runtime-env.uv-cache" (str (/ tmp "uv-cache"))
                 "runtime-env.repo-keys" {} "runtime-env.code-prepare" "" "runtime-env.uv" "uv" "runtime-env.progress" ""
                 "runtime-env.notes" "/dev/null"})
  (<- placed TreePlaced
      (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                     (materialized mirror commit dest source)))
  placed)


(defk materialized [mirror commit dest source]
  {:pre [(: mirror Path) (: commit str) (: dest Path) (: source (| TreeSource None))] :post [(: % TreePlaced)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "木を置く effect を 1 回出す。"
  (<- placed TreePlaced (MaterializeTree (str mirror) commit (str dest) source))
  placed)


(defk bare-mirror [repo mirror]
  {:pre [(: repo Path) (: mirror Path)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "repo の全部の commit を持つ bare mirror を作るため(worker の mirror と同じ形)。"
  (subprocess.run ["git" "clone" "--bare" "-q" (str repo) (str mirror)] :check True :capture-output True)
  None)


(val V1 {"keep.py" "K = 1\n"
         "change.py" "old\n"
         "gone.py" "G = 1\n"
         "run.sh" #("exec" "#!/bin/sh\n")
         "sub dir/new\nline.txt" "weird name\n"
         "-dash.txt" "dash\n"
         "link" #("link" "keep.py")
         "docs/old/readme.md" "R\n"
         "glob[1].txt" "g\n"
         "glob1.txt" "g1\n"})
;; V1 から: change.py と glob[1].txt の中身を替える・gone.py と docs/old/readme.md を消す・add.py と docs/new/x.txt を足す・symlink link の先を
;; 替える(symlink の先が変わる = 変わった path)。glob[1].txt の名は glob の綴りで、変わらない glob1.txt に当たる(literal に読まないと glob1.txt まで
;; 置き直す)。
(val V2 {"change.py" "new\n"
         "gone.py" None
         "docs/old/readme.md" None
         "add.py" "A = 1\n"
         "docs/new/x.txt" "X\n"
         "link" #("link" "change.py")
         "glob[1].txt" "g changed\n"})


(defk planted [src]
  {:pre [(: src Path)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "準備済みの root の木に、追跡しない物(venv・焼いた .pyc・焼いた印)を置くため — 写し元にはこれが在り、写した木には要らない。"
  (for [rel [".venv/lib/mod.py" "__pycache__/keep.cpython-314.pyc" "sub dir/__pycache__/x.pyc" ".doeff-code-ready.json"]]
    (val path (/ src rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path "untracked\n"))
  None)


(deftest test-a-copied-tree-shares-unchanged-files-and-never-writes-through-them [tmp-path]
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo V1))
  (<- v2 str (commit-files repo V2))
  (val mirror (/ tmp-path "app.git"))
  (<- (bare-mirror repo mirror))
  ;; 前の root(1)の木を展開で作り、追跡しない物を置く。
  (val src (/ tmp-path "root-1" "app"))
  (<- first TreePlaced (place mirror v1 src None tmp-path))
  (assert (= #(first.how first.copied first.removed first.replaced) #(TREE-EXPAND 0 0 (len V1))) first)
  (<- (planted src))
  (<- before dict (snapshot src))
  (val inodes-before (dfor p (.rglob src "*") :if (and (.is-file p) (not (.is-symlink p))) (str (.relative-to p src)) (. (.stat p) st-ino)))
  ;; 次の版の木を写す。
  (val dest (/ tmp-path "root-2" "app"))
  (<- second TreePlaced (place mirror v2 dest (TreeSource :dir (str src) :commit v1) tmp-path))
  ;; V1 の追跡 file 10 のうち、変わった change.py・link・glob[1].txt と消えた gone.py・docs/old/readme.md の 5 つは持ち越さない。新しい中身を置くのは
  ;; change.py・link・glob[1].txt・増えた add.py・docs/new/x.txt の 5 つ。持ち越す 5 + 置き直す 5 = 新しい木の 10 file。
  (assert (= #(second.how second.copied second.removed second.replaced second.reason) #(TREE-COPY 5 5 5 "")) second)
  ;; できた木 = 新しい commit を mirror から全部展開した木(追跡しない物は無い・消えた path の dir も残らない)。
  (val fresh (/ tmp-path "root-3" "app"))
  (<- expanded TreePlaced (place mirror v2 fresh None tmp-path))
  (assert (= expanded.how TREE-EXPAND) expanded)
  (<- copied-shape dict (snapshot dest))
  (<- fresh-shape dict (snapshot fresh))
  (assert (= copied-shape fresh-shape) (.format "写した木と展開した木が違う: {}" (sorted (^ (set (.items copied-shape)) (set (.items fresh-shape))))))
  (assert (not-in ".venv" copied-shape))
  (assert (not-in ".doeff-code-ready.json" copied-shape))
  (assert (not-in "docs/old" copied-shape) "消えた path の dir も残らない")
  ;; 持ち越した file は写し元と同じ実体(hardlink)・変わった path は別の実体。
  (for [rel ["keep.py" "run.sh" "sub dir/new\nline.txt" "-dash.txt" "glob1.txt"]]
    (assert (= (. (.stat (/ dest rel)) st-ino) (get inodes-before rel)) #("hardlink でない" rel))
    (assert (>= (. (.stat (/ dest rel)) st-nlink) 2) #("hardlink でない" rel)))
  (assert (!= (. (.stat (/ dest "change.py")) st-ino) (get inodes-before "change.py")) "変わった file は別の実体")
  ;; 写し元の root の木は前のまま(変わった file も、追跡しない物も)。
  (<- after dict (snapshot src))
  (assert (= after before) "写し元の木が変わった")
  (assert (= (.read-text (/ src "change.py")) "old\n"))
  (assert (= (.read-text (/ dest "change.py")) "new\n"))
  ;; 写し元の root を消しても、写した木の file の中身は残る(hardlink)。
  (shutil.rmtree (/ tmp-path "root-1"))
  (<- remaining dict (snapshot dest))
  (assert (= remaining fresh-shape) "写し元を消したら写した木が欠けた")
  (assert (= (. (.stat (/ dest "keep.py")) st-nlink) 1) "写し元の名が消えた後は 1 つだけ"))


(defk bulk-files [n changed]
  {:pre [(: n int) (: changed int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "n 個の file(中身は番号)と、先頭 changed 個の中身を替えた物の組 #(旧 差)を作るため。"
  {"旧" (dfor i (range n) (.format "d{}/f{}.txt" (// i 50) i) (.format "v1 {}\n" i))
   "差" (dfor i (range changed) (.format "d{}/f{}.txt" (// i 50) i) (.format "v2 {}\n" i))})


(deftest test-a-change-set-larger-than-one-command-is-placed-in-chunks [tmp-path]
  ;; 持ち越す物も置き直す物も PATHS-PER-COMMAND を越える数 — cp と git archive は path を数百ずつ渡す(引数の長さの上限の内)。
  (val total (+ (* 2 PATHS-PER-COMMAND) 300))
  (val changed (+ PATHS-PER-COMMAND 200))
  (<- made dict (bulk-files total changed))
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo (get made "旧")))
  (<- v2 str (commit-files repo (| (get made "差") {"added.txt" "new\n"})))
  (val mirror (/ tmp-path "app.git"))
  (<- (bare-mirror repo mirror))
  (val src (/ tmp-path "root-1" "app"))
  (<- first TreePlaced (place mirror v1 src None tmp-path))
  (assert (= first.replaced total) first)
  (val dest (/ tmp-path "root-2" "app"))
  (<- second TreePlaced (place mirror v2 dest (TreeSource :dir (str src) :commit v1) tmp-path))
  (assert (= #(second.how second.copied second.removed second.replaced) #(TREE-COPY (- total changed) changed (+ changed 1))) second)
  (assert (> (- total changed) PATHS-PER-COMMAND) "持ち越す物が 1 回の命令に収まる数では chunk を試せない")
  (assert (> (+ changed 1) PATHS-PER-COMMAND) "置き直す物が 1 回の命令に収まる数では chunk を試せない")
  (val fresh (/ tmp-path "root-3" "app"))
  (<- expanded TreePlaced (place mirror v2 fresh None tmp-path))
  (<- copied-shape dict (snapshot dest))
  (<- fresh-shape dict (snapshot fresh))
  (assert (= copied-shape fresh-shape) "写した木と展開した木が違う"))


(deftest test-a-source-commit-missing-from-the-mirror-expands-the-whole-tree [tmp-path]
  ;; 先頭の commit だけを持つ mirror(浅い clone)には、写し元の commit が無い — 写さず全部を展開し、理由を答えに載せる。
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo V1))
  (<- v2 str (commit-files repo V2))
  (val mirror (/ tmp-path "app.git"))
  (subprocess.run ["git" "clone" "--bare" "-q" "--depth" "1" (+ "file://" (str repo)) (str mirror)] :check True :capture-output True)
  (val src (/ tmp-path "root-1" "app"))
  (<- (bare-mirror repo (/ tmp-path "full.git")))
  (<- first TreePlaced (place (/ tmp-path "full.git") v1 src None tmp-path))
  (val dest (/ tmp-path "root-2" "app"))
  (<- placed TreePlaced (place mirror v2 dest (TreeSource :dir (str src) :commit v1) tmp-path))
  (assert (= placed.how TREE-EXPAND) placed)
  (assert (and (in "写し元の commit" placed.reason) (in "mirror に無い" placed.reason)) placed.reason)
  (val fresh (/ tmp-path "root-3" "app"))
  (<- expanded TreePlaced (place mirror v2 fresh None tmp-path))
  (<- placed-shape dict (snapshot dest))
  (<- fresh-shape dict (snapshot fresh))
  (assert (= placed-shape fresh-shape) "展開に戻した木が違う"))


(deftest test-a-copy-source-missing-a-file-expands-and-leaves-no-partial-copy [tmp-path]
  ;; 写し元の追跡 file が欠けると cp が失敗する — 途中まで写した木を空にして全部を展開に戻す。写し元は触らない。
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo V1))
  (<- v2 str (commit-files repo V2))
  (val mirror (/ tmp-path "app.git"))
  (<- (bare-mirror repo mirror))
  (val src (/ tmp-path "root-1" "app"))
  (<- first TreePlaced (place mirror v1 src None tmp-path))
  (.unlink (/ src "-dash.txt"))
  (<- before dict (snapshot src))
  (val dest (/ tmp-path "root-2" "app"))
  (<- placed TreePlaced (place mirror v2 dest (TreeSource :dir (str src) :commit v1) tmp-path))
  (assert (= placed.how TREE-EXPAND) placed)
  (assert (in "hardlink で写せない" placed.reason) placed.reason)
  (val fresh (/ tmp-path "root-3" "app"))
  (<- expanded TreePlaced (place mirror v2 fresh None tmp-path))
  (<- placed-shape dict (snapshot dest))
  (<- fresh-shape dict (snapshot fresh))
  (assert (= placed-shape fresh-shape) "途中まで写した file が残った")
  (<- after dict (snapshot src))
  (assert (= after before) "写し元の木が変わった"))
