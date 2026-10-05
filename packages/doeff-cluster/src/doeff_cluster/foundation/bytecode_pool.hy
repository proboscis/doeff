;;; 焼く物の列を 1 つの process の pool で焼く console の道具(worker の版の file — bytecode の準備の道具 worker/entry/code_prepare.hy が、
;;; 準備する root の venv の python で子 process として起こす)。焼きは 1 file ずつ独立で CPU だけを使うので、並列数の process に 1 つずつ
;;; 渡す(chunksize 1 — 束で渡すと遅い file を含む束を 1 core が抱える)。渡す順は呼び手が決める(大きい source から)。
;;;
;;;   python -m hy <worker のコード>/doeff_cluster/foundation/bytecode_pool.hy --jobs N [--path <dir> …] < 焼く物の行
;;;
;;; 標準入力 = 焼く物 1 つ 1 行の `<木の path>\t<相対 path>\t<module 名>`(この順に pool へ渡す)。pool へ渡す前に、木に既に在る .pyc
;;; (前の木から引き継いだ物)が今の source の hash と今の環境の macro の記録に合う物を除く(焼き直しても同じ物になる — #3675。前は全部の
;;; Hy の source を pool へ送り、pool の子が 1 つずつ照らしていた)。標準出力 = 1 つ 1 行で、焼けなかった物は
;;; `failed\t<木の path>\t<相対 path>\t<理由>`(理由の tab と改行は空白)、焼かずに残した物は `reused\t<木の path>\t<相対 path>`(読み手は
;;; worker/core/bake_plan.hy の bake-answer)。--path = 焼く process の import の路の先頭に足す dir(前が先 — 焼く source の macro が木の中の
;;; 別の module を require するため・残すかの照らしも、記録の macro の提供元をこの路で引き直す)。焼いた .pyc は source の隣の __pycache__ に
;;; 置く(python_bytecode の compile-one)。
;;;
;;; 版に依らず効く形: root の側から import するのは python_bytecode の compile-one と doeff-hy の bytecode-is-current だけ(cluster で動く
;;; job の doeff の版から変わっていない名前と引数の形 — どちらも 2026-10-02 の #2598 から在る)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import argparse)
(import multiprocessing)
(import posixpath)
(import sys)
(import concurrent.futures [ProcessPoolExecutor])
(import pathlib [Path])
(import doeff [run])
(import doeff_hy_bytecode_guard [bytecode-is-current])
(import doeff_core_effects.python_bytecode [compile-one pyc-path])


(defn #^ None prepare-path [#^ tuple paths]  ; defk にできない: process の pool の initializer(素の関数として呼ばれる)
  "焼く process の import の路の先頭に木の根を足し(前が先)、焼く途中の import が timestamp 方式の .pyc を書かないようにするため。"
  (setv sys.dont-write-bytecode True)
  (setv (cut sys.path 0 0) (list paths))
  None)


(defk current-pyc [tree rel]
  {:pre [(: tree str) (: rel str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木に既に在る source 1 つの .pyc が、今の source の hash と今の環境の macro の記録に合うか — 合えば焼き直さずに残すため(.pyc か source を
   読めない時は偽 = 焼く)。照らし方は import の読みの口と同じ doeff-hy の bytecode-is-current(記録の macro の提供元は module 名から今の
   import の路で引き直す — prepare-path の後に呼ぶ)。"
  (val source (posixpath.join tree rel))
  (try
    (val existing (.read-bytes (Path (pyc-path source))))
    (val data (.read-bytes (Path source)))
    (bytecode-is-current source existing data)
    (except [OSError] False)))


(defn #^ tuple bake [#^ tuple items #^ int jobs #^ tuple paths]  ; defk にできない: process の pool を回す
  "items(#(木の path 相対 path module 名) の列)のうち、在る .pyc が今の source と macro に合わない物をこの順に焼き、#(焼けなかった物の
   #(木の path 相対 path 理由) の列 焼かずに残した物の #(木の path 相対 path) の列) を返すため。jobs 個の process に 1 つずつ渡す。fork で
   起こす(spawn では子が Hy の module を import し直す前に関数を解けない)。"
  (prepare-path paths)
  (setv kept (tuple (gfor i items (run (current-pyc (get i 0) (get i 1)))))
        work (tuple (gfor #(i k) (zip items kept) :if (not k) i)))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (tuple (gfor #(tree rel name) work (compile-one tree rel name)))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-path :initargs #(paths))]
          (tuple (.map pool compile-one (gfor i work (get i 0)) (gfor i work (get i 1)) (gfor i work (get i 2)) :chunksize 1)))))
  #((tuple (gfor #(item result) (zip work results) :if (is-not result None) #((get item 0) result.path result.reason)))
    (tuple (gfor #(i k) (zip items kept) :if k #((get i 0) (get i 1))))))


(defn #^ None main []  ; defk にできない: console の入口(素の関数として呼ばれる)
  "標準入力の焼く物を焼き、焼けなかった物と焼かずに残した物を標準出力へ書くため(頭の註の形)。"
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "--jobs" :type int :required True :help "焼きの並列数")
  (.add-argument parser "--path" :action "append" :help "焼く process の import の路の先頭に足す dir(前が先)")
  (setv args (.parse-args parser))
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/foundation)が sys.path の先頭に入る。焼く木の module 名が
  ;; そこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (setv items (tuple (gfor line (.splitlines (.read sys.stdin)) :setv parts (.split line "\t" 2) :if (= (len parts) 3) (tuple parts))))
  (setv #(failed reused) (bake items args.jobs (tuple (or args.path []))))
  (for [#(tree rel reason) failed]
    (print (.format "failed\t{}\t{}\t{}" tree rel (.join " " (.split reason))) :flush True))
  (for [#(tree rel) reused]
    (print (.format "reused\t{}\t{}" tree rel) :flush True)))


(when (= __name__ "__main__")
  (main))
