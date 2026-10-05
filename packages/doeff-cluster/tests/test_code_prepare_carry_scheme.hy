;; bytecode の準備の道具(worker/entry/code_prepare.hy)が前の木から .pyc を hardlink で引き継ぐ時に、.pyc の検めの方式と magic を見る検
;; (#3727)— 本物の file system(一時 dir)と本物の焼きの道具(foundation/bytecode_pool.hy を子 process で起こす)の上で。
;;   * 失敗ケース(1) 前の木に、道具が焼いた checked hash の .pyc と、入口の閉包の外だった module を Python が import の時に書いた
;;     timestamp の方式の .pyc が混ざる。次の版で閉包が広がってその module が焼く範囲に入ると、直す前は timestamp の .pyc を引き継いで
;;     carried に数え、焼く一覧から外す — 新しい木の .pyc は timestamp のままで(展開し直した木の source の mtime は違う)、import の時に
;;     compile される。直した後は焼き直されて checked hash になり、checked hash の物は hardlink のまま引き継がれる。
;;   * 失敗ケース(2) magic の違う(古い Python の)checked hash の .pyc も引き継がない — 直す前は引き継いで、新しい木の .pyc の magic が
;;     今の Python と違う(import の時に compile される)。
;;   * 走査は木の中の venv(隠し dir の .venv)の site-packages に降りない — そこの .pyc は引き継ぎの候補に入らない。
;;   * 純粋な判断(bake_plan の carried-pycs・pyc-head-of): 頭の record の列から、checked hash で magic が今の Python と同じ物だけを引き継ぐ。
;; 純粋な判断の名は package の module(bake)から呼ぶ時に引く — 直す前の版でもこの file を読めるようにし、失敗ケースの赤を振る舞いで見せる。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import importlib.util)
(import os)
(import py-compile)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.worker.core.code_plan [cache-rel])
(import doeff_cluster.worker.core.bake_plan :as bake)
(import doeff_cluster.worker.protocol.tree_files [tree-files])
(import doeff_cluster.worker.entry.code_prepare [bake-trees plan pool-tool-baker prepare-trees])


;; PEP 552 の頭の flags(checked hash)と、今と違う Python の magic(3.11 の 3495 — 今の Python の magic と決して同じでない)。
(val CHECKED-HASH-FLAGS 0b11)
(val OTHER-MAGIC (+ (.to-bytes 3495 2 "little") b"\r\n"))


(defk plant [root files]
  {:pre [(: root Path) (: files tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "一時 dir の木に file を置くため(#(相対 path 中身) の列 — 親の dir も作る)。"
  (for [#(rel text) files]
    (val path (/ root rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text))
  None)


(defk prepare-shaped [shaped entries]
  {:pre [(: shaped tuple) (: entries tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "道具の入口と同じ順で、揃えた木の組を焼く木にしてから準備し、木ごとの結果の列を返すため(並列 1)。"
  (<- trees tuple (bake-trees shaped))
  (<- outcomes tuple (prepare-trees trees "env" 1 entries))
  outcomes)


(defk prepare-real [tree old changed entries]
  {:pre [(: tree Path) (: old (| Path None)) (: changed (| Path None)) (: entries tuple)] :post [(: % plan.TreeOutcome)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "木 1 つを、前の木 old(None = 引き継がない)と変わった path の一覧の file changed から、本物の file system と本物の焼きの道具で準備し、
   その木の結果を返すため。"
  (<- shaped (| tuple str) (plan.tree-arguments #((str tree)) #(".") (if (is old None) #() #((str old)))
                                                (if (is changed None) #() #((str changed)))))
  (assert (isinstance shaped tuple) shaped)
  (<- outcomes tuple
      (with-handlers [(sim-time-handler :clock (SimClock)) slog-discard-handler os-file-handler subprocess-handler tree-files
                      pool-tool-baker]
        (prepare-shaped shaped entries)))
  (get outcomes 0))


(defk pyc-head [path]
  {:pre [(: path Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "置かれた .pyc の頭を #(magic flags) で読むため(PEP 552 — magic 4 byte・flags 4 byte)。"
  (val data (.read-bytes path))
  #((cut data 0 4) (int.from-bytes (cut data 4 8) "little")))


(defk write-pyc [tree rel mode]
  {:pre [(: tree Path) (: rel str) (: mode py-compile.PycInvalidationMode)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "木の source 1 つの .pyc を、import が探す名で、指定の検めの方式で書くため(timestamp = Python が import の時に書く形)。"
  (py-compile.compile (str (/ tree rel)) :cfile (str (/ tree (cache-rel rel))) :doraise True :invalidation-mode mode)
  None)


(defk replace-magic [path magic]
  {:pre [(: path Path) (: magic bytes)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "置かれた .pyc の magic だけを書き換えるため(別の file へ書いて置き換える — 古い Python が焼いた .pyc の形)。"
  (val data (.read-bytes path))
  (val written (.with-name path (+ path.name ".tmp")))
  (.write-bytes written (+ magic (cut data 4 None)))
  (os.replace written path)
  None)


(deftest test-a-timestamp-pyc-in-the-old-tree-is-rebaked-not-carried [#^ Path tmp-path]
  (val old (/ tmp-path "old"))
  (val new (/ tmp-path "new"))
  (val shared #(#("app/__init__.py" "") #("app/used.py" "X = 1\n") #("app/late.py" "Y = 2\n")))
  (<- (plant old (+ shared #(#("app/main.py" "import app.used\n")))))
  (<- first plan.TreeOutcome (prepare-real old None None #("app.main")))
  (assert (is first.problem None) first)
  ;; 閉包の外の app.late は焼かれず、子が import した時に Python が timestamp の方式の .pyc を書く。
  (assert (not (.exists (/ old (cache-rel "app/late.py")))) "閉包の外の module まで焼いた")
  (<- (write-pyc old "app/late.py" py-compile.PycInvalidationMode.TIMESTAMP))
  (<- old-late tuple (pyc-head (/ old (cache-rel "app/late.py"))))
  (<- old-used tuple (pyc-head (/ old (cache-rel "app/used.py"))))
  (assert (= #((get old-late 1) (get old-used 1)) #(0 CHECKED-HASH-FLAGS)) #(old-late old-used))
  ;; 次の版は入口が app.late も import する(閉包が広がる)— 変わった file は入口だけ。
  (<- (plant new (+ shared #(#("app/main.py" "import app.used\nimport app.late\n")))))
  (val listed (/ tmp-path "new.changed"))
  (.write-text listed "app/main.py\n")
  (<- outcome plan.TreeOutcome (prepare-real new old listed #("app.main")))
  (<- new-late tuple (pyc-head (/ new (cache-rel "app/late.py"))))
  (assert (= new-late #(importlib.util.MAGIC-NUMBER CHECKED-HASH-FLAGS))
          #("前の木の timestamp の方式の .pyc を引き継いだ(新しい木の .pyc が timestamp のまま — import の時に compile される)" new-late outcome))
  (assert (not (os.path.samefile (/ old (cache-rel "app/late.py")) (/ new (cache-rel "app/late.py")))) "timestamp の .pyc を hardlink した")
  (assert (os.path.samefile (/ old (cache-rel "app/used.py")) (/ new (cache-rel "app/used.py")))
          "checked hash の .pyc を hardlink で引き継いでいない")
  ;; 引き継いだ = __init__ と used・焼いた = 変わった入口と、引き継がなかった late(数えの欄は足さない — rebuilt に入る)。
  (assert (= #(outcome.carried outcome.rebuilt outcome.reused outcome.failed outcome.problem) #(2 2 0 0 None)) outcome))


(deftest test-a-checked-hash-pyc-of-another-python-is-not-carried [#^ Path tmp-path]
  (val old (/ tmp-path "old"))
  (val new (/ tmp-path "new"))
  (val files #(#("app/__init__.py" "") #("app/main.py" "import app.used\n") #("app/used.py" "X = 1\n")))
  (<- (plant old files))
  (<- first plan.TreeOutcome (prepare-real old None None #()))
  (assert (is first.problem None) first)
  (val old-used (/ old (cache-rel "app/used.py")))
  (<- (replace-magic old-used OTHER-MAGIC))
  (<- (plant new files))
  (<- outcome plan.TreeOutcome (prepare-real new old None #()))
  (val new-used (/ new (cache-rel "app/used.py")))
  (<- head tuple (pyc-head new-used))
  (assert (= head #(importlib.util.MAGIC-NUMBER CHECKED-HASH-FLAGS))
          #("magic の違う(古い Python の)checked hash の .pyc を引き継いだ" head outcome))
  (assert (not (os.path.samefile old-used new-used)) "magic の違う .pyc を hardlink した")
  (assert (os.path.samefile (/ old (cache-rel "app/main.py")) (/ new (cache-rel "app/main.py")))
          "今の Python の checked hash の .pyc を hardlink で引き継いでいない")
  (assert (= #(outcome.carried outcome.rebuilt outcome.reused outcome.failed outcome.problem) #(2 1 0 0 None)) outcome))


(deftest test-the-venv-inside-a-tree-is-not-a-carry-candidate [#^ Path tmp-path]
  (val old (/ tmp-path "old"))
  (val new (/ tmp-path "new"))
  (val venv-source ".venv/lib/python3.14/site-packages/x/__init__.py")
  (val files #(#("app/__init__.py" "") #(venv-source "Z = 3\n")))
  (<- (plant old files))
  (<- (prepare-real old None None #()))
  (<- (write-pyc old venv-source py-compile.PycInvalidationMode.CHECKED-HASH))
  (<- (plant new files))
  (<- outcome plan.TreeOutcome (prepare-real new old None #()))
  (assert (= #((.exists (/ new (cache-rel venv-source))) outcome.carried) #(False 1))
          #("木の中の venv の site-packages の .pyc を引き継ぎの候補にした" outcome)))


(deftest test-only-checked-hash-pycs-of-this-python-are-carried
  (val magic importlib.util.MAGIC-NUMBER)
  (val heads #((bake.PycHead :path (cache-rel "a/checked.py") :scheme bake.PycScheme.CHECKED-HASH :magic magic)
               (bake.PycHead :path (cache-rel "a/stamped.py") :scheme bake.PycScheme.TIMESTAMP :magic magic)
               (bake.PycHead :path (cache-rel "a/unchecked.py") :scheme bake.PycScheme.UNCHECKED-HASH :magic magic)
               (bake.PycHead :path (cache-rel "a/older.py") :scheme bake.PycScheme.CHECKED-HASH :magic OTHER-MAGIC)
               (bake.PycHead :path (cache-rel "a/broken.py") :scheme bake.PycScheme.UNREADABLE :magic b"")
               (bake.PycHead :path (cache-rel "a/edited.py") :scheme bake.PycScheme.CHECKED-HASH :magic magic)))
  (val sources (frozenset #("a/checked.py" "a/stamped.py" "a/unchecked.py" "a/older.py" "a/broken.py" "a/edited.py")))
  ;; source の判断(変わった・新しい木に無い・新しい木に .pyc が在る)は今までどおり — 変わった edited は方式が合っても引き継がない。
  (<- carried list (bake.carried-pycs heads magic sources sources (frozenset) (frozenset #("a/edited.py"))))
  (assert (= carried [(cache-rel "a/checked.py")]) carried))


(deftest test-the-pyc-head-names-the-scheme-by-pep-552-flags
  (val magic importlib.util.MAGIC-NUMBER)
  (val hash (bytes 8))
  (val cases #(#((+ magic (.to-bytes 0 4 "little") hash) bake.PycScheme.TIMESTAMP magic)
               #((+ magic (.to-bytes 0b01 4 "little") hash) bake.PycScheme.UNCHECKED-HASH magic)
               #((+ magic (.to-bytes 0b11 4 "little") hash) bake.PycScheme.CHECKED-HASH magic)
               #((+ OTHER-MAGIC (.to-bytes 0b11 4 "little") hash) bake.PycScheme.CHECKED-HASH OTHER-MAGIC)
               #((+ magic (.to-bytes 0b10 4 "little") hash) bake.PycScheme.UNREADABLE magic)
               #((+ magic (.to-bytes 0b11 4 "little")) bake.PycScheme.UNREADABLE b"")
               #(None bake.PycScheme.UNREADABLE b"")))
  (for [#(head scheme want-magic) cases]
    (<- seen bake.PycHead (bake.pyc-head-of "a/__pycache__/m.pyc" head))
    (assert (= seen (bake.PycHead :path "a/__pycache__/m.pyc" :scheme scheme :magic want-magic)) #(head seen))))
