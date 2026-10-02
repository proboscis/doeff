;;; 展開したコードの木の bytecode の準備の Program(prepare-tree)と、木の言い換え(worker/protocol/tree_files の tree-files — #2468)が通る判断
;;; (走査の列・完成の印の text・import の閉包)。code_prepare.hy から分けた(#2027)。起動される道具の script(main)は
;;; doeff_cluster/code_prepare.hy に残る(worker 自身のコードから file の path で起動する)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(import json)
(import collections.abc [Callable])
(import pathlib [PurePosixPath])
(import doeff_time [GetMonotonic])
(import doeff_cluster.worker.core.code_plan [SOURCE-SUFFIXES carry-pairs compile-plan imported-names marker-content module-name tree-problem])
(import doeff_cluster.worker.intent.code_model [ScanTree LinkPycs CompileSources ImportClosure WriteMarker Note])


;; 木を焼き、検めが通れば完成の印を置く。結果の "problem" が None でなければ印は置いていない。
(defk prepare-tree [tree revision old changed jobs roots [entries #()]]
  {:pre [(: tree str) (: revision str) (: old (| str None)) (: changed frozenset) (: jobs int) (: roots tuple) (: entries tuple)]
   :post [(: % dict)]}
  ;; entries が在れば、焼く物と検めの対象をその import の閉包に絞る(閉包の外は import の時に作られる)。
  (<- started float (GetMonotonic))
  (<- scanned tuple (ScanTree tree))
  (val all-sources (get scanned 0))
  (var pycs (get scanned 1))
  (var scope None)
  (when entries
    (<- closure frozenset (ImportClosure tree (tuple all-sources) entries roots))
    (:= scope closure))
  (setv sources (if (is scope None) all-sources (lfor s all-sources :if (in s scope) s)))
  (var carried 0)
  (when (is-not old None)
    (<- old-scan tuple (ScanTree old))
    (setv #(old-sources old-pycs) old-scan)
    (<- pairs list (carry-pairs old-pycs (frozenset old-sources) (frozenset sources) (frozenset pycs) changed))
    (<- linked int (LinkPycs old tree (tuple pairs)))
    (:= carried linked)
    (:= pycs (+ pycs pairs)))
  (<- carried-at float (GetMonotonic))
  (<- plan list (compile-plan sources (frozenset pycs) roots))
  (<- failures list (CompileSources tree (tuple plan) jobs roots))
  (<- ended float (GetMonotonic))
  (setv summary {"carried" carried "compiled" (- (len plan) (len failures)) "failed" (len failures)
                 "carry_s" (round (- carried-at started) 2) "compile_s" (round (- ended carried-at) 2)})
  (<- (Note (.join " " (lfor #(k v) (.items summary) (.format "{}={}" k v)))))
  (for [#(rel reason) (cut failures 0 20)]
    (<- (Note f"  焼けない: {rel}: {reason}")))
  ;; 検め: 焼いた結果を木から読み直す(焼きの答えを信じず、置かれた物を数える)。
  (<- after tuple (ScanTree tree))
  (setv #(scanned-after after-pycs) after
        after-sources (if (is scope None) scanned-after (lfor s scanned-after :if (in s scope) s))
        failed (frozenset (gfor #(rel _) failures rel)))
  (<- problem (| str None) (tree-problem after-sources (frozenset after-pycs) failed roots))
  (if (is problem None)
      (do (<- marker dict (marker-content revision True after-sources (frozenset after-pycs) failures roots))
          (<- (WriteMarker tree marker)))
      (<- (Note (+ "検めが通らないので完成の印を置きません: " problem))))
  (| summary {"problem" problem}))


(defk tree-listing [rels]
  {:pre [(: rels (| list tuple))] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "木の中の file の相対 path(posix)の列を、走査の答え #(source の列 .pyc の列)(名の順)にするため。隠し file と隠し dir の下は
   数えない・.pyc は __pycache__ の直下の物だけ・source は __pycache__ の外の .py / .hy。"
  (val sources [])
  (val pycs [])
  (for [rel rels]
    (val path (PurePosixPath rel))
    (val parent path.parent.name)
    (cond
      (any (gfor part path.parts (.startswith part "."))) None
      (and (= parent "__pycache__") (.endswith path.name ".pyc")) (.append pycs rel)
      (and (!= parent "__pycache__") (.endswith path.name SOURCE-SUFFIXES)) (.append sources rel)))
  #((sorted sources) (sorted pycs)))


(defk marker-text [content]
  {:pre [(: content dict)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "完成の印の中身を file の text にするため。"
  (json.dumps content :ensure-ascii False :indent 1))


(defk closure-of [sources entries roots read]
  {:pre [(: sources (| list tuple)) (: entries tuple) (: roots tuple) (: read Callable)] :post [(: % frozenset)]
   :tags {:context "worker" :role "judgment"}}
  "entries(module 名)から import を静的に辿った閉包に入る source の相対 path を求めるため(焼く範囲を task が読む module に絞る)。
   package の module を読むと、その上の package の __init__ も読む。木の外の module(標準・第三者)は辿らない。
   read = 相対 path → source の text(本物は木の file を読み、fake は置き場の中身を渡す)。"
  (val by-module (dfor s sources :setv m (module-name s roots) :if (is-not m None) m s))
  (val seen (set))
  (val queue (list entries))
  (while queue
    (val name (.pop queue))
    (val parts (.split name "."))
    ;; 上の package も読む(import a.b.c は a と a.b の __init__ を走らせる)。
    (for [n (range 1 (+ (len parts) 1))]
      (val m (.join "." (cut parts 0 n)))
      (when (and (in m by-module) (not-in m seen))
        (.add seen m)
        (val rel (get by-module m))
        (val package (if (.endswith rel #("__init__.py" "__init__.hy")) m (.join "." (cut (.split m ".") 0 -1))))
        (for [#(dots target names) (imported-names rel (read rel))]
          (val base (if (> dots 0)
                        (.join "." (+ (cut (.split package ".") 0 (max 0 (- (len (.split package ".")) (- dots 1)))) (if target [target] [])))
                        target))
          (when base
            (.append queue base)
            ;; from base import x の x が module なら、それも読む。
            (for [x names] (.append queue (+ base "." x))))))))
  (frozenset (gfor m seen (get by-module m))))
