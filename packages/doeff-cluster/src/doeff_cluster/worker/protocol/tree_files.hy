;;; コードの木の効果(code_prepare の焼きの Program prepare-tree が出す ScanTree・LinkPycs・ImportClosure・CompileSources・WriteMarker・Note)
;;; の言い換え tree-files(code_prepare.hy の files-tree を移し、本物の local-tree を退役させた・#2468)。木の効果を汎用の file system の効果
;;; (WalkTree・StatPath・MakeDirectory・LinkFile・ReadText・WriteText・CompilePythonSources)と slog へ出し直す。I/O を持たない — 本番の焼きの
;;; 道具(code_prepare.hy の main)は外側に os-file-handler、模擬の世界は memory-file-handler を置く。
;;;
;;; 走査・閉包・印の中身の判断は worker/core/code_prepare の関数(tree-listing・closure-of・marker-text)、焼きは CompilePythonSources
;;; (本物は process の pool・memory は逐次)。答え手による違い: memory の LinkFile は中身の写し・memory は Hy の source を焼けない
;;; (doeff-hy は disk に在る file かで Hy の source を見分ける — 契約テスト test_tree_contract.hy の頭の註)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import os)
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath ReadText WriteText MakeDirectory WalkTree LinkFile CompilePythonSources
                                         file-done])
(import doeff_cluster.worker.core.code_plan [MARKER])
(import doeff_cluster.worker.intent.code_model [ScanTree LinkPycs CompileSources ImportClosure WriteMarker Note])
(import doeff_cluster.worker.core.code_prepare [tree-listing marker-text closure-of])


(defk tree-file-rels [tree]
  {:pre [(: tree str)] :post [(: % list)]}
  "木の下の file の相対 path を並べるため(無い木は空 — どれを数えるかの判断は tree-listing)。"
  (<- walked (WalkTree tree))
  (if (isinstance walked tuple)
      (lfor entry walked :if (= entry.kind PathKind.FILE) entry.name)
      []))


(defk link-pycs [old new pycs]
  {:pre [(: old str) (: new str) (: pycs tuple)] :post [(: % int)]}
  "前の木の .pyc に新しい木の同じ相対 path の名を付け(ハードリンク)、付けた数を返すため(先が在れば付けない)。import が焼き直す時は
   別 file へ書いて置き換えるので、共有しても壊れない。"
  (var count 0)
  (for [rel pycs]
    (val target (os.path.join new rel))
    (<- (file-done (MakeDirectory (os.path.dirname target))))
    (<- found (file-done (StatPath target)))
    (when (= found.kind PathKind.MISSING)
      (<- (file-done (LinkFile (os.path.join old rel) target)))
      (:= count (+ count 1))))
  count)


(defk source-texts [tree sources]
  {:pre [(: tree str) (: sources (| list tuple))] :post [(: % dict)]}
  "木の source の相対 path → text を読むため(閉包の辿りに渡す)。"
  (val texts {})
  (for [rel sources]
    (<- text (file-done (ReadText (os.path.join tree rel))))
    (.update texts {rel text}))
  texts)


(defhandler tree-files
  ;; 木の効果を file system の効果へ出し直す(頭の註)。
  (ScanTree [tree]
    (<- rels list (tree-file-rels tree))
    (<- listed tuple (tree-listing rels))
    (resume listed))
  (LinkPycs [old new pycs]
    (<- linked int (link-pycs old new pycs))
    (resume linked))
  (ImportClosure [tree sources entries roots]
    (<- texts dict (source-texts tree sources))
    (<- closure frozenset (closure-of sources entries roots (fn [rel] (get texts rel))))
    (resume closure))
  (CompileSources [tree items jobs roots]
    (<- failures tuple (CompilePythonSources tree items jobs roots))
    (resume (lfor f failures #(f.path f.reason))))
  (WriteMarker [tree content]
    (<- text str (marker-text content))
    (<- (file-done (WriteText (os.path.join tree MARKER) text :replace True)))
    (resume None))
  (Note [line]
    (<- (slog line))
    (resume None)))
