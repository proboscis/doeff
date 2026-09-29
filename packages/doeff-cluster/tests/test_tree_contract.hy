;;; コードの木の答え手の契約テスト — 本物(local-tree: os・hardlink・焼き)と fake(files-tree: file system の effect の上 + memory の置き場)が
;;; 同じ deftest を通る。解釈器の組み立ては tree_contract_handlers.hy。
;;;
;;;   * 走査: __pycache__ の外の .py / .hy を source・__pycache__ の直下の .pyc を .pyc と数える(隠し file と隠し dir の下は数えない)・
;;;     無い木は空
;;;   * 焼き(prepare-tree を通して): 焼ける source ごとに import が検める方式(checked hash)の .pyc を置き、焼けない source は理由つきで
;;;     印に載せ、完成の印(読む時の検め marker-problem が通る物)を木の根に置く
;;;   * 引き継ぎ: 変わっていない source の .pyc は前の木から同じ中身で引き継ぎ、変わった source だけを焼き直す
;;;   * 閉包: entries の import(Hy の import / require・Python の import・相対の名)を辿った source だけ
;;; 本物だけの性質(契約の外): 引き継ぎが hardlink であること(fake は写す — 中身は同じ)・焼きの並列(jobs)・焼く間の import の路・
;;; Note の行き先(stderr)。焼けない物の理由の文は source の在処(一時 dir と memory の置き場)を含むので、例外の型の名だけを比べる。
;;; 焼きの性質は .py の source で比べる: fake は Hy の source を焼けない(doeff-hy の _could_be_hy_src が Hy の source かを os.path.isfile で
;;; 見るので、disk に無い memory の置き場の source は Python として読まれ SyntaxError になる)— 契約が見つけた食い違いで、直すのは
;;; doeff-hy の見分けの側(この契約の外)。
(require doeff-hy.macros [defk deftest <- val])
(import json)
(import os)
(import importlib.util)
(import doeff_core_effects.file_effects [ReadText ReadBytes WriteText MakeDirectory file-done])
(import doeff_cluster.code_prepare [ScanTree ImportClosure MARKER prepare-tree marker-problem cache-rel])
(import tests.file_contract_handlers [FilesRoot])

(val PACKAGE #(#("pkg/__init__.py" "") #("pkg/m.py" "X = 1\n") #("pkg/n.py" "Y = 2\n")))


(defk plant [tree files]
  {:pre [(: tree str) (: files tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "木に file を置くため(#(相対 path 中身) の列 — 親の dir も作る)。"
  (for [#(rel text) files]
    (val path (os.path.join tree rel))
    (<- (file-done (MakeDirectory (os.path.dirname path))))
    (<- (file-done (WriteText path text))))
  None)


(deftest test-a-scan-lists-sources-and-cached-pycs-and-skips-hidden-files
  {:interpreters ["local-tree" "files-tree"]}
  (<- root str (FilesRoot))
  (val tree (+ root "/t"))
  (val pyc (cache-rel "a/m.py"))
  (<- (plant tree #(#("a/m.py" "") #("a/n.hy" "") #("top.py" "") #(pyc "x") #("a/__pycache__/note.txt" "") #("a/x.pyc" "")
                    #(".hidden/h.py" "") #("a/.dot.py" "") #("a/data.txt" "") #((+ "a/.cache/" (cache-rel "z.py")) "")
                    #("a/__pycache__/inner.py" ""))))
  (<- scanned tuple (ScanTree tree))
  (assert (= scanned #(["a/m.py" "a/n.hy" "top.py"] [pyc])) scanned)
  (<- missing tuple (ScanTree (+ root "/none")))
  (assert (= missing #([] [])) missing))


(deftest test-prepare-compiles-the-tree-and-writes-a-marker-that-passes-the-check
  {:interpreters ["local-tree" "files-tree"]}
  (<- root str (FilesRoot))
  (val tree (+ root "/rev1"))
  (<- (plant tree (+ PACKAGE #(#("pkg/bad.py" "def (:\n")))))
  (<- summary dict (prepare-tree tree "rev1" None (frozenset) 1 #(".")))
  (assert (= #((get summary "carried") (get summary "compiled") (get summary "failed") (get summary "problem")) #(0 3 1 None)) summary)
  (<- scanned tuple (ScanTree tree))
  (assert (= (get scanned 1) (sorted (gfor #(rel _) PACKAGE (cache-rel rel)))) scanned)
  (<- text str (ReadText (+ tree "/" MARKER)))
  (val marker (json.loads text))
  (assert (= #((get marker "revision") (get marker "bytecode") (get marker "compilable") (get marker "pycs")) #("rev1" True 4 3)) marker)
  (assert (= (lfor f (get marker "failed") #((get f "path") (get (.split (get f "reason") ":") 0))) [#("pkg/bad.py" "SyntaxError")]) marker)
  (assert (is (marker-problem text "rev1" True 3) None) text)
  ;; .pyc は import が source の hash で検める方式(PEP 552 の flags = 3)で、頭に source の hash を持つ。
  (<- pyc bytes (ReadBytes (+ tree "/" (cache-rel "pkg/m.py"))))
  (assert (= (cut pyc 4 16) (+ b"\x03\x00\x00\x00" (importlib.util.source-hash b"X = 1\n"))) pyc))


(deftest test-a-new-tree-carries-unchanged-pycs-and-compiles-only-the-changed-source
  {:interpreters ["local-tree" "files-tree"]}
  (<- root str (FilesRoot))
  (val old (+ root "/rev1"))
  (val new (+ root "/rev2"))
  (<- (plant old PACKAGE))
  (<- first dict (prepare-tree old "rev1" None (frozenset) 1 #(".")))
  (<- (plant new #(#("pkg/__init__.py" "") #("pkg/m.py" "X = 1\n") #("pkg/n.py" "Y = 3\n"))))
  (<- second dict (prepare-tree new "rev2" old (frozenset #("pkg/n.py")) 1 #(".")))
  (assert (= #((get first "compiled") (get second "carried") (get second "compiled") (get second "problem")) #(3 2 1 None)) #(first second))
  (<- old-pyc bytes (ReadBytes (+ old "/" (cache-rel "pkg/m.py"))))
  (<- new-pyc bytes (ReadBytes (+ new "/" (cache-rel "pkg/m.py"))))
  (assert (= new-pyc old-pyc) new-pyc)
  (<- changed bytes (ReadBytes (+ new "/" (cache-rel "pkg/n.py"))))
  (assert (= (cut changed 8 16) (importlib.util.source-hash b"Y = 3\n")) changed))


(deftest test-the-compile-scope-is-the-import-closure-of-the-entries
  {:interpreters ["local-tree" "files-tree"]}
  (<- root str (FilesRoot))
  (val tree (+ root "/t"))
  (val files #(#("pkg/__init__.py" "")
               #("pkg/entry.hy" "(import pkg.used [f])\n(require pkg.macros [m])\n(import json os)\n")
               #("pkg/used.hy" "(import .deep [g])\n(defn f [] 1)\n")
               #("pkg/deep.py" "import pkg.leaf\n")
               #("pkg/leaf.py" "X = 1\n")
               #("pkg/macros.hy" "(defmacro m [] 1)\n")
               #("pkg/unused.hy" "(import pkg.leaf)\n")))
  (<- (plant tree files))
  (<- closure frozenset (ImportClosure tree (tuple (sorted (gfor #(rel _) files rel))) #("pkg.entry") #(".")))
  (assert (= closure (frozenset #("pkg/__init__.py" "pkg/entry.hy" "pkg/used.hy" "pkg/deep.py" "pkg/leaf.py" "pkg/macros.hy")))
          closure))
