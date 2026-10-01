;;; Python と Hy の source を、import が検める方式(PEP 552 の checked hash)の .pyc に焼く部品(#2463)— 汎用の効果 CompilePythonSources
;;; (file_effects.hy)の答え手(os_file.hy の本物・memory_file.hy の memory)が同じ判断を通る。
;;;   compiled-pyc         source の中身 1 つ → .pyc の中身(焼けなければ SourceNotCompiled)
;;;   checked-hash-pyc     焼いた code → .pyc の中身(PEP 552 の頭 + marshal)
;;;   pyc-path             source の path → 同じ dir の __pycache__ の .pyc の path
;;;   compile-python-sources  本物: 木の source を process の pool で並列に焼いて __pycache__ へ置く(fork — Hy の macro 展開が大半)
;;;   prepare-compile-path    焼く process の import の路に木の中の根を足す(焼く source の macro が木の中の別の module を require するため)
(require doeff-hy.macros [defk <- val])
(import importlib.machinery)
(import importlib.util)
(import marshal)
(import multiprocessing)
(import os)
(import posixpath)
(import sys)
(import types)
(import concurrent.futures [ProcessPoolExecutor])
(import doeff [run])
(import doeff_hy_bytecode_guard [source-to-code-as-import])
(import doeff_core_effects.file_effects [SourceNotCompiled])


;; PEP 552 の hash 方式の .pyc の頭の flags: bit 0 = hash 方式・bit 1 = import の時に source の hash を検める(checked)。
(val CHECKED-HASH-FLAGS 0b11)


(defk checked-hash-pyc [code data]
  {:pre [(: code types.CodeType) (: data bytes)] :post [(: % bytes)]}
  "焼いた code を、import が source の hash で検める .pyc の中身にするため(PEP 552 — 頭 = magic・flags・source の hash 8 byte、
   続けて marshal した code)。標準の私的な実装 importlib._bootstrap_external._code_to_hash_pyc と同じ並びを公開の API で組む。"
  (+ importlib.util.MAGIC-NUMBER
     (.to-bytes CHECKED-HASH-FLAGS 4 "little")
     (importlib.util.source-hash data)
     (marshal.dumps code)))


(defk compiled-pyc [rel name path data]
  {:pre [(: rel str) (: name str) (: path str) (: data bytes)] :post [(: % (| bytes SourceNotCompiled))]}
  "source の中身 1 つを、import が検める方式(PEP 552 の checked hash)の .pyc の中身にするため。焼けない時は SourceNotCompiled
   (import の時に同じ誤りが出るので、ここでは記録だけ)。rel = 木の中の相対 path・name = module 名・path = source の在処(Hy の source かの
   見分けと、誤りの文に出る名)。Hy の source は import と同じく module を置いた中で compile し、展開が依った macro の記録を付ける —
   記録の無い .pyc は import の時に doeff-hy の古さの検めが compile し直すので、焼いた分が無駄になる(agora-redesign #2598)。"
  (try
    (val loader (importlib.machinery.SourceFileLoader name path))
    (val code (source-to-code-as-import loader data path))
    (<- pyc bytes (checked-hash-pyc code data))
    pyc
    (except [error Exception]
      (SourceNotCompiled :path rel :reason (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 200))))))


(defn #^ str pyc-path [#^ str source]  ; defk にできない: 内包表記と process の pool の中で呼ぶ path の計算
  "source の path → 同じ dir の __pycache__ の .pyc の path(import が探す名 — <名>.<cache の印>.pyc)。"
  (setv #(head tail) (posixpath.split source))
  (posixpath.join head "__pycache__" (+ (get (posixpath.splitext tail) 0) "." sys.implementation.cache-tag ".pyc")))


(defn #^ (| SourceNotCompiled None) compile-one [#^ str tree #^ str rel #^ str name]  ; defk にできない: process の pool の子が呼ぶ
  "本物の木の source 1 つを焼いて __pycache__ へ置く(別の file へ書いて置き換える)。焼けない・読めない時は SourceNotCompiled。"
  (setv source (posixpath.join tree rel))
  (try
    (with [f (open source "rb")] (setv data (.read f)))
    (except [error OSError]
      (return (SourceNotCompiled :path rel :reason (str error)))))   ; 読めない source の文は file の効果の断りと同じ形(OSError の文)
  (setv compiled (run (compiled-pyc rel name source data)))
  (when (isinstance compiled SourceNotCompiled) (return compiled))
  (setv cache (pyc-path source))
  (os.makedirs (posixpath.dirname cache) :exist-ok True)
  (setv tmp (.format "{}.{}.tmp" cache (os.getpid)))
  (with [f (open tmp "wb")] (.write f compiled))
  (os.replace tmp cache)
  None)


(defn #^ None prepare-compile-path [#^ str tree #^ tuple roots]  ; defk にできない: process の pool の初期化(Program の外)
  "焼く process の import の路に木の中の根を足し(前が先)、焼く途中の import が timestamp 方式の .pyc を書かないようにする。"
  (setv sys.dont-write-bytecode True)
  (for [root (reversed roots)]
    (.insert sys.path 0 (posixpath.join tree root))))


(defn #^ (| SourceNotCompiled None) _compile-item [#^ tuple item]  ; defk にできない: process の pool の子が呼ぶ
  (compile-one #* item))


(defn #^ tuple compile-python-sources [#^ str tree #^ tuple items #^ int jobs #^ tuple roots]  ; defk にできない: process の pool を回す
  "木の source(#(相対 path module 名) の列)を焼いて __pycache__ へ置き、焼けなかった物の SourceNotCompiled の列を返す。焼きは 1 file
   ずつ独立で CPU だけを使うので jobs 個の process に分ける(fork — spawn では子が Hy の module を import し直す前に関数を解けない)。"
  (setv work (lfor #(rel name) items #(tree rel name)))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (lfor item work (_compile-item item))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-compile-path :initargs #(tree roots))]
          (list (.map pool _compile-item work :chunksize 4)))))
  (tuple (gfor r results :if (is-not r None) r)))
