;;; 焼く物の列を 1 つの process の pool で焼く console の道具(worker の版の file — bytecode の準備の道具 worker/entry/code_prepare.hy が、
;;; 準備する root の venv の python で子 process として起こす)。焼きは 1 file ずつ独立で CPU だけを使うので、並列数の process に 1 つずつ
;;; 渡す(chunksize 1 — 束で渡すと遅い file を含む束を 1 core が抱える)。渡す順は呼び手が決める(大きい source から)。
;;;
;;;   python -m hy <worker のコード>/doeff_cluster/foundation/bytecode_pool.hy --jobs N [--path <dir> …] < 焼く物の行
;;;
;;; 標準入力 = 焼く物 1 つ 1 行の `<木の path>\t<相対 path>\t<module 名>`(この順に pool へ渡す)。標準出力 = 焼けなかった物 1 つ 1 行の
;;; `<木の path>\t<相対 path>\t<理由>`(理由の tab と改行は空白)。--path = 焼く process の import の路の先頭に足す dir(前が先 — 焼く
;;; source の macro が木の中の別の module を require するため)。焼いた .pyc は source の隣の __pycache__ に置く(python_bytecode の compile-one)。
;;;
;;; 版に依らず効く形: root の側から import するのは python_bytecode の compile-one だけ(cluster で動く job の doeff の版から変わっていない
;;; 名前と引数の形)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import argparse)
(import multiprocessing)
(import sys)
(import concurrent.futures [ProcessPoolExecutor])
(import pathlib [Path])
(import doeff_core_effects.python_bytecode [compile-one])


(defn #^ None prepare-path [#^ tuple paths]  ; defk にできない: process の pool の initializer(素の関数として呼ばれる)
  "焼く process の import の路の先頭に木の根を足し(前が先)、焼く途中の import が timestamp 方式の .pyc を書かないようにするため。"
  (setv sys.dont-write-bytecode True)
  (setv (cut sys.path 0 0) (list paths))
  None)


(defn #^ tuple bake [#^ tuple items #^ int jobs #^ tuple paths]  ; defk にできない: process の pool を回す
  "items(#(木の path 相対 path module 名) の列)をこの順に焼き、焼けなかった物の #(木の path 相対 path 理由) を返すため。jobs 個の process に
   1 つずつ渡す。fork で起こす(spawn では子が Hy の module を import し直す前に関数を解けない)。"
  (prepare-path paths)
  (setv results
    (if (or (<= jobs 1) (<= (len items) 1))
        (tuple (gfor #(tree rel name) items (compile-one tree rel name)))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-path :initargs #(paths))]
          (tuple (.map pool compile-one (gfor i items (get i 0)) (gfor i items (get i 1)) (gfor i items (get i 2)) :chunksize 1)))))
  (tuple (gfor #(item result) (zip items results) :if (is-not result None) #((get item 0) result.path result.reason))))


(defn #^ None main []  ; defk にできない: console の入口(素の関数として呼ばれる)
  "標準入力の焼く物を焼き、焼けなかった物を標準出力へ書くため(頭の註の形)。"
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "--jobs" :type int :required True :help "焼きの並列数")
  (.add-argument parser "--path" :action "append" :help "焼く process の import の路の先頭に足す dir(前が先)")
  (setv args (.parse-args parser))
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/foundation)が sys.path の先頭に入る。焼く木の module 名が
  ;; そこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (setv items (tuple (gfor line (.splitlines (.read sys.stdin)) :setv parts (.split line "\t" 2) :if (= (len parts) 3) (tuple parts))))
  (for [#(tree rel reason) (bake items args.jobs (tuple (or args.path [])))]
    (print (.format "{}\t{}\t{}" tree rel (.join " " (.split reason))) :flush True)))


(when (= __name__ "__main__")
  (main))
