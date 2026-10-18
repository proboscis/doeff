;; 実行環境の木の置き方(翻訳 env-translation の MaterializeTree — #3677・#3671 の子 2)の検 — 本物の git・tar・os.link と本物の file system
;; の上で。
;;
;; 模擬の世界(test_env_prepare)は、翻訳が出す命令の並びと数を確かめる。この検は、本物の file system でしか確かめられない性質を確かめる:
;;   1 写した file は写し元と同じ実体(hardlink)・変わった path は写し元と別の実体で、写し元の中身は前のまま(その場で上書きしない)
;;   2 できた木は、その commit を mirror から全部展開した木と同じ(増えた・消えた・変わった・種類が変わった path・名に空白・改行・- で
;;     始まる path・実行の権限・変わらない symlink も symlink のまま)。追跡しない物(.venv・__pycache__・焼いた印)は写さない
;;   3 写し元の root を消しても、写した木の file の中身は残る(古い置き場の片づけが、そこから引き継いだ置き場を壊さない)
;;   4 git archive の引数の長さの内に収めるため、path を数百ずつ渡す(PATHS-PER-COMMAND を越える数の差も正しい木になる)
;;   5 写せない時(写し元の commit が mirror に無い・写し元の file が欠けて hardlink を作れない)は、途中まで写した木を空にして全部を展開に
;;     戻し、理由を答えに載せる
;;   6 写しは外の cp を起こさない — PATH の cp が GNU の書き方を断る機体(macOS の BSD の cp)でも写す(card ki-d51b7baa38bc)
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
(import doeff_cluster.worker.protocol.env_translation [env-translation PATHS-PER-COMMAND run-watched CommandResult])


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
         "docs/stay-link" #("link" "../keep.py")
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
  ;; V1 の追跡 file 11 のうち、変わった change.py・link・glob[1].txt と消えた gone.py・docs/old/readme.md の 5 つは持ち越さない。変わらない
  ;; symlink docs/stay-link は hardlink にせず(os.link は先の file に名を付ける)mirror から置き直す。新しい中身を置くのは change.py・link・
  ;; glob[1].txt・増えた add.py・docs/new/x.txt と docs/stay-link の 6 つ。持ち越す 5 + 置き直す 6 = 新しい木の 11 file。
  (assert (= #(second.how second.copied second.removed second.replaced second.reason) #(TREE-COPY 5 5 6 "")) second)
  (assert (.is-symlink (/ dest "docs/stay-link")) "変わらない symlink は symlink のまま")
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


(deftest test-a-tree-is-copied-where-cp-refuses-gnu-options [tmp-path monkeypatch]
  ;; macOS の worker の PATH の cp は BSD の cp で、GNU の書き方(cp -al --parents -t)を使い方の誤り(64)で断る — 10-17 19:53 JST の Mac の
  ;; root は 2 つの repo の木とも展開に戻った(card ki-d51b7baa38bc)。同じ断り方の cp を PATH の頭に置いても、写しは写しのまま。
  (val bin (/ tmp-path "bsd-bin"))
  (.mkdir bin)
  (val fake (/ bin "cp"))
  (.write-text fake "#!/bin/sh\necho 'cp: illegal option -- -' >&2\nexit 64\n")
  (.chmod fake 0o755)
  (.setenv monkeypatch "PATH" (.format "{}:{}" bin (os.environ.get "PATH" "")))
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo V1))
  (<- v2 str (commit-files repo V2))
  (val mirror (/ tmp-path "app.git"))
  (<- (bare-mirror repo mirror))
  (val src (/ tmp-path "root-1" "app"))
  (<- first TreePlaced (place mirror v1 src None tmp-path))
  (val dest (/ tmp-path "root-2" "app"))
  (<- second TreePlaced (place mirror v2 dest (TreeSource :dir (str src) :commit v1) tmp-path))
  (assert (= #(second.how second.reason) #(TREE-COPY "")) second)
  (assert (= (. (.stat (/ dest "keep.py")) st-ino) (. (.stat (/ src "keep.py")) st-ino)) "持ち越した file は hardlink"))


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
  (assert (in "-dash.txt" placed.reason) #("理由が写せなかった file を名指す" placed.reason))
  (val fresh (/ tmp-path "root-3" "app"))
  (<- expanded TreePlaced (place mirror v2 fresh None tmp-path))
  (<- placed-shape dict (snapshot dest))
  (<- fresh-shape dict (snapshot fresh))
  (assert (= placed-shape fresh-shape) "途中まで写した file が残った")
  (<- after dict (snapshot src))
  (assert (= after before) "写し元の木が変わった"))


;; 進みの印は子の書いた量に結ぶ(段の区切りだけで進めない): 書き続ける子は印を触り、量が増えない子は触らない。
(defk watched [argv out err watched-paths progress]
  {:pre [(: argv tuple) (: out Path) (: err Path) (: watched-paths tuple) (: progress Path)] :post [(: % CommandResult)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の子を run-watched で 1 つ走らせ、結果を返すため(読み直しの間隔は検のため短い)。"
  (<- result CommandResult
      (with-handlers [(sync-time-handler) subprocess-handler os-file-handler]
                     (run-watched argv None (str out) (str err) (tuple (gfor p watched-paths (str p))) (str progress) 0.05)))
  result)


(deftest test-a-child-that-keeps-writing-touches-the-progress-mark [tmp-path]
  (val progress (/ tmp-path "mark.progress"))
  (val out (/ tmp-path "child.out"))
  (<- result CommandResult
      (watched #("sh" "-c" "for i in 1 2 3 4; do echo line; sleep 0.1; done") out (/ tmp-path "child.err") #(out) progress))
  (assert (= result.code 0) result)
  (assert (= result.stdout "line\nline\nline\nline\n") result)
  (assert (= (.read-text progress) "tree\n"))
  (assert (not (.exists out)) "出力の file は片づける")
  None)


(deftest test-a-child-that-writes-nothing-never-touches-the-progress-mark [tmp-path]
  (val progress (/ tmp-path "mark.progress"))
  (val out (/ tmp-path "child.out"))
  (<- result CommandResult
      (watched #("sh" "-c" "sleep 0.5; echo done >&2; exit 3") out (/ tmp-path "child.err") #(out) progress))
  (assert (= #(result.code result.stderr) #(3 "done\n")) result)
  (assert (not (.exists progress)) "量が増えない子は、時間が経っても印を触らない")
  None)


(deftest test-expanding-a-tree-touches-the-progress-mark-from-what-the-children-wrote [tmp-path]
  (val repo (/ tmp-path "app-repo"))
  (<- v1 str (commit-files repo V1))
  (val mirror (/ tmp-path "app.git"))
  (<- (bare-mirror repo mirror))
  (val progress (/ tmp-path "mark.progress"))
  (val settings {"runtime-env.state" (str (/ tmp-path "state")) "runtime-env.shared" (str (/ tmp-path "state"))
                 "runtime-env.uv-cache" (str (/ tmp-path "uv-cache")) "runtime-env.repo-keys" {} "runtime-env.code-prepare" ""
                 "runtime-env.uv" "uv" "runtime-env.progress" (str progress) "runtime-env.notes" "/dev/null"})
  (<- placed TreePlaced
      (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                     (materialized mirror v1 (/ tmp-path "root" "app") None)))
  (assert (= placed.how TREE-EXPAND) placed)
  ;; 子が速く終わるので、読み直しの間(5 秒)に印を触る機会は無い — 触れるのは子が走っている間に量が増えた時だけ。ここでは触らずに終わる。
  (assert (not (.exists progress)) "子が読み直しの前に終わる木は、印を触らない")
  None)
