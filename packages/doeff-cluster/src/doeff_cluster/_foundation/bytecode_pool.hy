;;; 焼く物の列を 1 つの process の pool で用意する console の道具(worker の版の file — bytecode の準備の道具 worker/entry/code_prepare.hy
;;; が、準備する root の venv の python で子 process として起こす)。1 file ずつ独立で CPU だけを使うので、並列数の process に 1 つずつ
;;; 渡す(chunksize 1 — 束で渡すと遅い file を含む束を 1 core が抱える)。渡す順は呼び手が決める(大きい source から)。
;;;
;;;   python -m hy <worker のコード>/doeff_cluster/_foundation/bytecode_pool.hy --jobs N --store-module <code_store.py>
;;;       [--store <保存先の dir>] [--progress <進みの印の file>] [--path <dir> …] < 焼く物の行
;;;
;;; 標準入力 = 焼く物 1 つ 1 行の `<木の path>\t<相対 path>\t<module 名>`(この順に pool へ渡す)。
;;; 1 つの用意(bake-one): source の中身で保存先(doeff-hy の code_store — 版・作業木をまたいで中身で引く入口の 1 つ)を引き、鍵に並ぶ macro の
;;; 環境ごとの code のうち今の source と今の環境の macro に合う物が在れば(doeff-hy の bytecode-is-current — Hy の source は記録の macro の
;;; 提供元を今の import の路で引き直して照らす)その code から .pyc を置く(stored)。無い・合わない物は焼いて .pyc を置き、今の環境の code を
;;; 鍵に足す(rebuilt — 他の環境の code は上書きしない)。.pyc は、保存先に置いた中身の同じ file への hardlink で置く(code_store の link-pyc —
;;; root ごとに同じ中身を新しく書かない。置けなければ今どおり書く)。
;;; 木に既に在る .pyc(同じ commit の木を hardlink で写した root)が今の source と macro に合う物は、pool へ渡さずに残す(reused)。
;;; 標準出力 = 1 つ 1 行で、焼けなかった物は `failed\t<木の path>\t<相対 path>\t<理由>`(理由の tab と改行は空白)、保存先の code から書いた
;;; 物は `stored\t<木の path>\t<相対 path>`、焼かずに残した物は `reused\t<木の path>\t<相対 path>`、保存先へ書けなかった理由と、保存先の
;;; .pyc を木へ hardlink で置けなかった理由は `unstored\t<理由>`(理由ごとに 1 行 — 読み手は worker/core/bake_plan.hy の bake-answer)。焼いた
;;; 物は行を持たない(数は呼び手が引き算)。
;;; --store-module = worker の版の doeff-hy の code_store.py(root の版の doeff-hy は保存先の入口を持たない版でありうるので、worker の版の
;;; file を path で読む)・--store = 保存先の dir(無ければ保存先を使わずに全部を焼く — DOEFF_HY_CODE_STORE=off)。--path = 焼く process の
;;; import の路の先頭に足す dir(前が先 — 焼く source の macro が木の中の別の module を require するため・残すかの照らしも、記録の macro の
;;; 提供元をこの路で引き直す)。.pyc は source の隣の __pycache__ に置く(python_bytecode の pyc-path)。
;;; --progress = 進みの印の file(呼び手の準備が停滞を見分ける印 — 1 つ用意し終えるたびに時刻を今にする・中身は変えない・無ければ触らない)。
;;; 印を焼きの前と後にしか進めないと、保存先に合う code の無い焼き直し(macro を変えた版の後の初めての準備 — 2026-10-27 の zeus-native で
;;; 2,400 本・並べる数 2)が 1 file ずつ進んでいても、焼きの全体が停滞の閾(600 秒)を越えて止められ、作り直しも同じ所で止められた。
;;;
;;; 版に依らず効く形: この道具は root の venv の python で走るが、root の版の doeff の package から名を import しない(doeff・
;;; doeff_core_effects・doeff-hy の macro も — 標準 library と hy だけ。defk・val を使わないのも、macro が root の版の doeff-hy の物になるため)。
;;; .pyc の path と PEP 552 の頭はここで組む。Hy の source の compile(展開が依った macro の記録を付ける)と、在る .pyc・保存先の code が
;;; 今の環境の macro に合うかの照らしだけは root の版の doeff-hy の古さの検め(guard)の物を使う(記録の形と照らし方は root の版の物 —
;;; import の時に照らすのも root の guard)。guard は module の名で引かず、root の guard が起動時(.pth)に SourceFileLoader に被せた口の
;;; 持ち主の module を sys.modules から引き、その bytecode_is_current と source_to_code_as_import を呼ぶ(guard-module)。口の持ち主の
;;; [removed]
;;; #2463・#2598 から変わらず、doeff-hy の tests/test_guard_hook_owner_contract.py が守る。
;;; 前は guard と python_bytecode を module の名で import したので、worker の版と root の版の doeff が組になり、名の置き場を内側へ移した
;;; doeff(2026-10-29)の root で、それより古い worker の準備が import の誤りで必ず落ちた(2026-11-01・ADR-DOE-BUILD-002 R7)。
(setv MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import argparse)
(import importlib.machinery)
(import importlib.util)
(import marshal)
(import multiprocessing)
(import os)
(import posixpath)
(import sys)
(import concurrent.futures [ProcessPoolExecutor as-completed])
(import functools [partial])
(import pathlib [Path])
(import types [CodeType ModuleType])
(import hy)

;; PEP 552 の .pyc の頭の長さ(magic・flags・source の hash)— 頭の後ろが marshal した code(保存先の code の entry の中身)。
(setv PYC-HEAD-BYTES 16)
;; PEP 552 の hash 方式の .pyc の頭の flags: bit 0 = hash 方式・bit 1 = import の時に source の hash を検める(checked)。
(setv CHECKED-HASH-FLAGS 0b11)
;; worker の版の code_store を sys.modules に置く名(root の版の doeff_hy_bytecode_guard._code_store と重ならない名)。
(setv STORE-MODULE-NAME "doeff_cluster_worker_code_store")
;; root の guard が包む SourceFileLoader の口(持ち主の module を引く手がかり — 前が先)と、持ち主の module から呼ぶ名(頭の註)。
(setv GUARD-HOOKS #("get_code" "source_to_code"))
(setv GUARD-NAMES #("bytecode_is_current" "source_to_code_as_import"))


(defn #^ ModuleType guard-module []  ; defk にできない: root の版の doeff の macro を使わない道具の素の関数(頭の註)
  "root の guard の module — root の guard が起動時に SourceFileLoader に被せた口の持ち主を、module の名で import せずに引くため(頭の註)。
   口が包まれていない・持ち主が 2 つの名を出さない root は、焼いた .pyc に記録を付けられない(import が全部を compile し直す)ので落とす。"
  (for [hook GUARD-HOOKS]
    (setv owner (.get sys.modules (getattr (getattr importlib.machinery.SourceFileLoader hook) "__module__" None)))
    (when (and (is-not owner None) (all (gfor name GUARD-NAMES (callable (getattr owner name None)))))
      (return owner)))
  (raise (RuntimeError (.format "SourceFileLoader の口を包んだ guard の module が {} を出さない(doeff-hy の .pth が効いていない root)"
                                (.join "・" GUARD-NAMES)))))


(defn #^ str pyc-path [#^ str source]  ; defk にできない: 内包表記と process の pool の中で呼ぶ path の計算
  "source の path → 同じ dir の __pycache__ の .pyc の path(import が探す名 — <名>.<cache の印>.pyc)。"
  (setv #(head tail) (posixpath.split source))
  (posixpath.join head "__pycache__" (+ (get (posixpath.splitext tail) 0) "." sys.implementation.cache-tag ".pyc")))


(defn #^ bytes checked-hash-pyc [#^ CodeType code #^ bytes data]  ; defk にできない: process の pool の子が呼ぶ素の組み立て
  "焼いた code を、import が source の hash で検める .pyc の中身にするため(PEP 552 — 頭 = magic・flags・source の hash 8 byte、
   続けて marshal した code)。"
  (+ importlib.util.MAGIC-NUMBER
     (.to-bytes CHECKED-HASH-FLAGS 4 "little")
     (importlib.util.source-hash data)
     (marshal.dumps code)))


(defn #^ tuple compiled-pyc [#^ str name #^ str path #^ bytes data]  ; defk にできない: process の pool の子が呼ぶ焼き
  "source の中身 1 つを、import が検める方式の .pyc の中身にするため — #(.pyc の中身 None)、焼けない時は #(None 理由)(import の時に同じ
   誤りが出るので、ここでは記録だけ)。Hy の source は root の guard の source_to_code_as_import で、import と同じく module を置いた中で
   compile し、展開が依った macro の記録を付ける(記録の無い .pyc は import の時に guard が compile し直す)。"
  (try
    (setv code (.source-to-code-as-import (guard-module) (importlib.machinery.SourceFileLoader name path) data path))
    #((checked-hash-pyc code data) None)
    (except [error Exception]
      #(None (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 200))))))


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


(defn #^ bool current-pyc [#^ str tree #^ str rel]  ; defk にできない: root の版の doeff の macro を使わない道具の素の関数(頭の註)
  "木に既に在る source 1 つの .pyc が、今の source の hash と今の環境の macro の記録に合うか — 合えば焼き直さずに残すため(.pyc か source を
   読めない時は偽 = 用意する)。照らし方は import の読みの口と同じ root の guard の bytecode_is_current(記録の macro の提供元は module 名から
   今の import の路で引き直す — prepare-path の後に呼ぶ)。"
  (setv source (posixpath.join tree rel))
  (try
    (setv existing (.read-bytes (Path (pyc-path source)))
          data (.read-bytes (Path source)))
    (.bytecode-is-current (guard-module) source existing data)
    (except [OSError] False)))


(defn #^ None write-pyc [#^ str source #^ bytes pyc]  ; defk にできない: process の pool の子が呼ぶ file の書き
  "source の隣の __pycache__ へ .pyc を置く(別の file へ書いて置き換える — hardlink の先の別の木の .pyc を書き換えない)。"
  (setv cache (pyc-path source))
  (os.makedirs (posixpath.dirname cache) :exist-ok True)
  (setv tmp (.format "{}.{}.tmp" cache (os.getpid)))
  (with [f (open tmp "wb")] (.write f pyc))
  (os.replace tmp cache)
  None)


(defn #^ str place-pyc [#^ (| str None) store #^ str source #^ bytes pyc]  ; defk にできない: process の pool の子が呼ぶ file の書き
  "source の .pyc を置くため: 保存先が在れば、保存先の中身の同じ file への hardlink で置き(code_store の link-pyc — root ごとに同じ中身を
   新しく書かない)、置けなかった・保存先を使わない時は write-pyc で書く。答え = hardlink で置けなかった理由(置けた・保存先を使わない時は空)。"
  (setv problem (if (is store None) None (.link-pyc (get sys.modules STORE-MODULE-NAME) store pyc (pyc-path source))))
  (when (or (is store None) (is-not problem None))
    (write-pyc source pyc))
  (or problem ""))


(defn #^ None mark-progress [#^ (| str None) progress]  ; defk にできない: 焼きの結果を待つ loop の中で呼ぶ素の file の触り
  "進みの印の file の時刻を今にするため(頭の註 --progress)。None = 印の無い起動 — 何もしない。印の file が消えていても焼きは止めない
   (印は見張りの材料)。"
  (when (is-not progress None)
    (try
      (os.utime progress)
      (except [OSError]
        None)))
  None)


(defn #^ tuple bake-one [#^ (| str None) store #^ str tree #^ str rel #^ str name]  ; defk にできない: process の pool の子が素の関数として呼ぶ
  "焼く物 1 つを用意し、#(結果 理由) を返すため。結果 = \"stored\"(保存先の code から .pyc を置いた — 置き方の理由は place-pyc)・
   \"rebuilt\"(焼いて .pyc を置き、code を保存先へ足した — 足せなかった・hardlink で置けなかった時は理由)・\"failed\"(焼けない・
   読めない — 理由)。store = 保存先の dir(None = 引かず足さない)。保存先の code から置く .pyc の bytes は code_store の entry-pyc が
   組む(頭の後ろに entry の中身をそのまま — 同じ entry から置く .pyc が root をまたいで同じ bytes になり、保存先の 1 つの file を分け合う。
   組み方は code_store だけが知る)。
   保存先の鍵には macro の環境ごとの entry が並ぶ(doeff の版の違う書き手の物も — code_store の current-code)。今の source の hash と
   今の環境の macro に合う entry だけを使い、無ければ焼き直して今の環境の entry を鍵に足す(他の環境の entry は上書きしない —
   鍵 1 つに entry 1 つの形では、版の違う書き手と同じ鍵を交互に上書きし、同じ版の準備でも 1 回に 100〜600 本を
   焼き直した)。"
  (setv source (posixpath.join tree rel))
  (try
    (with [f (open source "rb")] (setv data (.read f)))
    (except [error OSError]
      (return #("failed" (str error)))))   ; 読めない source の文は file の効果の断りと同じ形(OSError の文)
  (setv code-store (get sys.modules STORE-MODULE-NAME))
  (setv key (if (is store None)
                None
                (.code-key code-store source data name hy.__version__ (or sys.implementation.cache-tag "") sys.flags.optimize)))
  (when (is-not key None)
    (setv guard (guard-module)
          found (.current-entry code-store store key (fn [c] (.bytecode-is-current guard source (checked-hash-pyc c data) data))))
    (when (is-not found None)
      (setv head (cut (checked-hash-pyc found.code data) 0 PYC-HEAD-BYTES))
      (return #("stored" (place-pyc store source (.entry-pyc code-store head found))))))
  (setv #(compiled reason) (compiled-pyc name source data))
  (when (is compiled None)
    (return #("failed" reason)))
  (setv placed (place-pyc store source compiled))
  ;; entry の中身は焼いた .pyc の頭の後ろの bytes そのもの — 次の root が entry から置く .pyc が今置いた .pyc と同じ bytes になり、保存先の
  ;; 1 つの file を分け合う(marshal し直すと bytes が変わり得る)。
  (setv body (cut compiled PYC-HEAD-BYTES None))
  (setv problem (if (is key None) None (.add-code code-store store key (marshal.loads body) body)))
  #("rebuilt" (or problem placed)))


(defn #^ tuple bake [#^ tuple items #^ int jobs #^ tuple paths #^ (| str None) store #^ (| str None) progress]  ; defk にできない: process の pool を回す
  "items(#(木の path 相対 path module 名) の列)のうち、在る .pyc が今の source と macro に合わない物をこの順に用意し、#(焼けなかった物の
   #(木の path 相対 path 理由) の列 保存先から書いた物の #(木の path 相対 path) の列 焼かずに残した物の #(木の path 相対 path) の列
   保存先へ書けなかった理由の列(重ねない・名の順))を返すため。jobs 個の process に 1 つずつ渡す。fork で起こす(spawn では子が Hy の module を
   import し直す前に関数を解けない・worker の版の code_store も fork で子へ引き継ぐ)。1 つ用意し終えるたびに(終わった順に)進みの印
   progress を進める(頭の註 --progress — 渡す順の先の遅い 1 file を待つ間も、ほかの process の終わりで印が進む)。"
  (prepare-path paths)
  (setv kept (tuple (gfor i items (current-pyc (get i 0) (get i 1))))
        work (tuple (gfor #(i k) (zip items kept) :if (not k) i))
        one (partial bake-one store))
  (mark-progress progress)
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (tuple (gfor #(tree rel name) work
                     :setv result (one tree rel name)
                     :do (mark-progress progress)
                     result))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-path :initargs #(paths))]
          ;; 渡す順は submit の順(pool は受けた順に空いた process へ 1 つずつ渡す — map の chunksize 1 と同じ)。
          (setv futures (lfor #(tree rel name) work (.submit pool one tree rel name)))
          (for [_ (as-completed futures)]
            (mark-progress progress))
          (tuple (gfor f futures (.result f))))))
  #((tuple (gfor #(item result) (zip work results) :if (= (get result 0) "failed") #((get item 0) (get item 1) (get result 1))))
    (tuple (gfor #(item result) (zip work results) :if (= (get result 0) "stored") #((get item 0) (get item 1))))
    (tuple (gfor #(i k) (zip items kept) :if k #((get i 0) (get i 1))))
    (tuple (sorted (sfor result results :if (and (in (get result 0) #("stored" "rebuilt")) (get result 1)) (get result 1))))))


(defn #^ None main []  ; defk にできない: console の入口(素の関数として呼ばれる)
  "標準入力の焼く物を用意し、焼けなかった物・保存先から書いた物・焼かずに残した物・保存先へ書けなかった理由を標準出力へ書くため
   (頭の註の形)。"
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "--jobs" :type int :required True :help "並列数")
  (.add-argument parser "--store-module" :required True :help "worker の版の doeff-hy の code_store.py")
  (.add-argument parser "--store" :default None :help "保存先の dir(無ければ保存先を使わない)")
  (.add-argument parser "--progress" :default None :help "進みの印の file(1 つ用意し終えるたびに時刻を進める・無ければ触らない)")
  (.add-argument parser "--path" :action "append" :help "焼く process の import の路の先頭に足す dir(前が先)")
  (setv args (.parse-args parser))
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/_foundation)が sys.path の先頭に入る。焼く木の module 名が
  ;; そこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (store-module-at args.store-module)
  (guard-module)  ; guard の口の無い root は焼く前に落とす(焼いても記録を付けられない)
  (setv items (tuple (gfor line (.splitlines (.read sys.stdin)) :setv parts (.split line "\t" 2) :if (= (len parts) 3) (tuple parts))))
  (setv #(failed stored reused unstored) (bake items args.jobs (tuple (or args.path [])) args.store args.progress))
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
