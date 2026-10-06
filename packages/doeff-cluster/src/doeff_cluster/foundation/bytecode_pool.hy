;;; 焼く物の列を 1 つの process の pool で用意する console の道具(worker の版の file — bytecode の準備の道具 worker/entry/code_prepare.hy
;;; が、準備する root の venv の python で子 process として起こす)。1 file ずつ独立で CPU だけを使うので、並列数の process に 1 つずつ
;;; 渡す(chunksize 1 — 束で渡すと遅い file を含む束を 1 core が抱える)。渡す順は呼び手が決める(大きい source から)。
;;;
;;;   python -m hy <worker のコード>/doeff_cluster/foundation/bytecode_pool.hy --jobs N --store-module <code_store.py>
;;;       [--store <保存先の dir>] [--path <dir> …] < 焼く物の行
;;;
;;; 標準入力 = 焼く物 1 つ 1 行の `<木の path>\t<相対 path>\t<module 名>`(この順に pool へ渡す)。
;;; 1 つの用意(bake-one): source の中身で保存先(doeff-hy の code_store — 版・作業木をまたいで中身で引く入口の 1 つ)を引き、当たった code が
;;; 今の source と今の環境の macro に合えば(doeff-hy の bytecode-is-current — Hy の source は記録の macro の提供元を今の import の路で引き
;;; 直して照らす)その code から .pyc を書く(stored)。当たらない・合わない物は焼いて .pyc を書き、code を保存先へ足す(rebuilt)。
;;; 木に既に在る .pyc(同じ commit の木を hardlink で写した root)が今の source と macro に合う物は、pool へ渡さずに残す(reused)。
;;; 標準出力 = 1 つ 1 行で、焼けなかった物は `failed\t<木の path>\t<相対 path>\t<理由>`(理由の tab と改行は空白)、保存先の code から書いた
;;; 物は `stored\t<木の path>\t<相対 path>`、焼かずに残した物は `reused\t<木の path>\t<相対 path>`、保存先へ書けなかった理由は
;;; `unstored\t<理由>`(理由ごとに 1 行 — 読み手は worker/core/bake_plan.hy の bake-answer)。焼いた物は行を持たない(数は呼び手が引き算)。
;;; --store-module = worker の版の doeff-hy の code_store.py(root の版の doeff-hy は保存先の入口を持たない版でありうるので、worker の版の
;;; file を path で読む)・--store = 保存先の dir(無ければ保存先を使わずに全部を焼く — DOEFF_HY_CODE_STORE=off)。--path = 焼く process の
;;; import の路の先頭に足す dir(前が先 — 焼く source の macro が木の中の別の module を require するため・残すかの照らしも、記録の macro の
;;; 提供元をこの路で引き直す)。.pyc は source の隣の __pycache__ に置く(python_bytecode の pyc-path)。
;;;
;;; 版に依らず効く形: root の側から import するのは python_bytecode の compiled-pyc・checked-hash-pyc・pyc-path と doeff-hy の
;;; bytecode-is-current だけ(cluster で動く job の doeff の版から変わっていない名前と引数の形 — どれも 2026-10-02 の #2463・#2598 から在る)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import argparse)
(import importlib.machinery)
(import importlib.util)
(import multiprocessing)
(import os)
(import posixpath)
(import sys)
(import concurrent.futures [ProcessPoolExecutor])
(import functools [partial])
(import pathlib [Path])
(import types [ModuleType])
(import hy)
(import doeff [run])
(import doeff_hy_bytecode_guard [bytecode-is-current])
(import doeff_core_effects.file_effects [SourceNotCompiled])
(import doeff_core_effects.python_bytecode [compiled-pyc checked-hash-pyc pyc-path])

;; PEP 552 の .pyc の頭の長さ(magic・flags・source の hash)— 頭の後ろが marshal した code(保存先の code の entry の中身)。
(val PYC-HEAD-BYTES 16)
;; worker の版の code_store を sys.modules に置く名(root の版の doeff_hy_bytecode_guard.code_store と重ならない名)。
(val STORE-MODULE-NAME "doeff_cluster_worker_code_store")


(defn #^ None prepare-path [#^ tuple paths]  ; defk にできない: process の pool の initializer(素の関数として呼ばれる)
  "焼く process の import の路の先頭に木の根を足し(前が先)、焼く途中の import が timestamp 方式の .pyc を書かないようにするため。"
  (setv sys.dont-write-bytecode True)
  (setv (cut sys.path 0 0) (list paths))
  None)


(defn #^ ModuleType store-module-at [#^ str path]  ; defk にできない: 入口が pool を起こす前に 1 度だけ呼ぶ素の読み(fork の子が引き継ぐ)
  "worker の版の code_store.py を path で module として読むため(頭の註 --store-module)。"
  (setv spec (importlib.util.spec-from-file-location STORE-MODULE-NAME path))
  (when (or (is spec None) (is spec.loader None))
    (raise (ImportError (.format "{} を module として読めない" path))))
  (setv module (importlib.util.module-from-spec spec))
  (setv (get sys.modules STORE-MODULE-NAME) module)
  (.exec-module spec.loader module)
  module)


(defk current-pyc [tree rel]
  {:pre [(: tree str) (: rel str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木に既に在る source 1 つの .pyc が、今の source の hash と今の環境の macro の記録に合うか — 合えば焼き直さずに残すため(.pyc か source を
   読めない時は偽 = 用意する)。照らし方は import の読みの口と同じ doeff-hy の bytecode-is-current(記録の macro の提供元は module 名から
   今の import の路で引き直す — prepare-path の後に呼ぶ)。"
  (val source (posixpath.join tree rel))
  (try
    (val existing (.read-bytes (Path (pyc-path source))))
    (val data (.read-bytes (Path source)))
    (bytecode-is-current source existing data)
    (except [OSError] False)))


(defn #^ None write-pyc [#^ str source #^ bytes pyc]  ; defk にできない: process の pool の子が呼ぶ file の書き
  "source の隣の __pycache__ へ .pyc を置く(別の file へ書いて置き換える — hardlink の先の別の木の .pyc を書き換えない)。"
  (setv cache (pyc-path source))
  (os.makedirs (posixpath.dirname cache) :exist-ok True)
  (setv tmp (.format "{}.{}.tmp" cache (os.getpid)))
  (with [f (open tmp "wb")] (.write f pyc))
  (os.replace tmp cache)
  None)


(defn #^ tuple bake-one [#^ (| str None) store #^ str tree #^ str rel #^ str name]  ; defk にできない: process の pool の子が素の関数として呼ぶ
  "焼く物 1 つを用意し、#(結果 理由) を返すため。結果 = \"stored\"(保存先の code から .pyc を書いた)・\"rebuilt\"(焼いて .pyc を書き、
   code を保存先へ足した — 足せなかった時は理由)・\"failed\"(焼けない・読めない — 理由)。store = 保存先の dir(None = 引かず足さない)。
   保存先の code は今の source の hash と今の環境の macro に合う時だけ使う(合わなければ焼き直して同じ鍵の entry を書き直す)。"
  (setv source (posixpath.join tree rel))
  (try
    (with [f (open source "rb")] (setv data (.read f)))
    (except [error OSError]
      (return #("failed" (str error)))))   ; 読めない source の文は file の効果の断りと同じ形(OSError の文)
  (setv code-store (get sys.modules STORE-MODULE-NAME))
  (setv entry (if (is store None)
                  None
                  (.entry-path code-store store
                               (.code-key code-store source data name hy.__version__ (or sys.implementation.cache-tag "") sys.flags.optimize)
                               code-store.CODE-SUFFIX)))
  (when (is-not entry None)
    (setv code (.stored-code code-store entry))
    (when (is-not code None)
      (setv pyc (run (checked-hash-pyc code data)))
      (when (bytecode-is-current source pyc data)
        (write-pyc source pyc)
        (return #("stored" "")))))
  (setv compiled (run (compiled-pyc rel name source data)))
  (when (isinstance compiled SourceNotCompiled)
    (return #("failed" compiled.reason)))
  (write-pyc source compiled)
  (setv problem (if (is entry None) None (.write-entry code-store entry (cut compiled PYC-HEAD-BYTES None))))
  #("rebuilt" (or problem "")))


(defn #^ tuple bake [#^ tuple items #^ int jobs #^ tuple paths #^ (| str None) store]  ; defk にできない: process の pool を回す
  "items(#(木の path 相対 path module 名) の列)のうち、在る .pyc が今の source と macro に合わない物をこの順に用意し、#(焼けなかった物の
   #(木の path 相対 path 理由) の列 保存先から書いた物の #(木の path 相対 path) の列 焼かずに残した物の #(木の path 相対 path) の列
   保存先へ書けなかった理由の列(重ねない・名の順))を返すため。jobs 個の process に 1 つずつ渡す。fork で起こす(spawn では子が Hy の module を
   import し直す前に関数を解けない・worker の版の code_store も fork で子へ引き継ぐ)。"
  (prepare-path paths)
  (setv kept (tuple (gfor i items (run (current-pyc (get i 0) (get i 1)))))
        work (tuple (gfor #(i k) (zip items kept) :if (not k) i))
        one (partial bake-one store))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (tuple (gfor #(tree rel name) work (one tree rel name)))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-path :initargs #(paths))]
          (tuple (.map pool one (gfor i work (get i 0)) (gfor i work (get i 1)) (gfor i work (get i 2)) :chunksize 1)))))
  #((tuple (gfor #(item result) (zip work results) :if (= (get result 0) "failed") #((get item 0) (get item 1) (get result 1))))
    (tuple (gfor #(item result) (zip work results) :if (= (get result 0) "stored") #((get item 0) (get item 1))))
    (tuple (gfor #(i k) (zip items kept) :if k #((get i 0) (get i 1))))
    (tuple (sorted (sfor result results :if (and (= (get result 0) "rebuilt") (get result 1)) (get result 1))))))


(defn #^ None main []  ; defk にできない: console の入口(素の関数として呼ばれる)
  "標準入力の焼く物を用意し、焼けなかった物・保存先から書いた物・焼かずに残した物・保存先へ書けなかった理由を標準出力へ書くため
   (頭の註の形)。"
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "--jobs" :type int :required True :help "並列数")
  (.add-argument parser "--store-module" :required True :help "worker の版の doeff-hy の code_store.py")
  (.add-argument parser "--store" :default None :help "保存先の dir(無ければ保存先を使わない)")
  (.add-argument parser "--path" :action "append" :help "焼く process の import の路の先頭に足す dir(前が先)")
  (setv args (.parse-args parser))
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/foundation)が sys.path の先頭に入る。焼く木の module 名が
  ;; そこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (store-module-at args.store-module)
  (setv items (tuple (gfor line (.splitlines (.read sys.stdin)) :setv parts (.split line "\t" 2) :if (= (len parts) 3) (tuple parts))))
  (setv #(failed stored reused unstored) (bake items args.jobs (tuple (or args.path [])) args.store))
  (for [#(tree rel reason) failed]
    (print (.format "failed\t{}\t{}\t{}" tree rel (.join " " (.split reason))) :flush True))
  (for [#(tree rel) stored]
    (print (.format "stored\t{}\t{}" tree rel) :flush True))
  (for [#(tree rel) reused]
    (print (.format "reused\t{}\t{}" tree rel) :flush True))
  (for [reason unstored]
    (print (.format "unstored\t{}" (.join " " (.split reason))) :flush True)))


(when (= __name__ "__main__")
  (main))
