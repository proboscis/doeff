;;; Component that bakes Python and Hy sources into .pyc files in the form that import verifies (PEP 552 checked hash) (#2463) -- the handlers of the generic effect CompilePythonSources
;;; (file_effects.hy) (the real one in os_file.hy and the memory one in memory_file.hy) go through the same judgement.
;;;   compiled-pyc         the content of one source -> the content of a .pyc (SourceNotCompiled if it cannot be baked)
;;;   checked-hash-pyc     baked code -> the content of a .pyc (PEP 552 header + marshal)
;;;   kept-pyc             whether to keep a .pyc already in the tree (inherited from the previous tree) without re-baking (only when it matches the current source and current macros)
;;;   pyc-path             source path -> the path of the .pyc in __pycache__ in the same dir
;;;   compile-python-sources  the real one: bake the tree's sources in parallel in a process pool and place them in __pycache__ (fork -- Hy macro expansion is most of the cost)
;;;   prepare-compile-path    add the roots inside the tree to the import path of the baking process (because a baked source's macros require other modules inside the tree)
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "bytecode" :role "foundation"})
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
(import doeff_hy_bytecode_guard._loader_hooks [bytecode-is-current source-to-code-as-import])
(import doeff_core_effects._file_effects [SourceNotCompiled])


;; Flags in the header of a PEP 552 hash-based .pyc: bit 0 = hash-based, bit 1 = verify the source hash at import time (checked).
(val CHECKED-HASH-FLAGS 0b11)


(defk checked-hash-pyc [code data]
  {:pre [(: code types.CodeType) (: data bytes)] :post [(: % bytes)]}
  "To turn baked code into the content of a .pyc that import verifies by the source hash (PEP 552 -- header = magic, flags, 8 bytes of source hash,
   followed by the marshaled code). Builds the same layout as the standard private implementation importlib._bootstrap_external._code_to_hash_pyc using the public API."
  (+ importlib.util.MAGIC-NUMBER
     (.to-bytes CHECKED-HASH-FLAGS 4 "little")
     (importlib.util.source-hash data)
     (marshal.dumps code)))


(defk compiled-pyc [rel name path data]
  {:pre [(: rel str) (: name str) (: path str) (: data bytes)] :post [(: % (| bytes SourceNotCompiled))]}
  "To turn the content of one source into the content of a .pyc in the form that import verifies (PEP 552 checked hash). When it cannot be baked, SourceNotCompiled
   (the same error appears at import time, so we only record it here). rel = relative path inside the tree, name = module name, path = location of the source (used to tell
   whether it is a Hy source, and the name shown in the error message). A Hy source is compiled inside the module being placed, as at import, and the record of the macros the expansion relied on is attached --
   a .pyc without that record gets recompiled by doeff-hy's staleness check at import time, so the baking would be wasted."
  (try
    (val loader (importlib.machinery.SourceFileLoader name path))
    (val code (source-to-code-as-import loader data path))
    (<- pyc bytes (checked-hash-pyc code data))
    pyc
    (except [error Exception]
      (SourceNotCompiled :path rel :reason (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 200))))))


(defk kept-pyc [path data existing]
  {:pre [(: path str) (: data bytes) (: existing bytes)] :post [(: % bool)]}
  "Whether to keep the content existing of a .pyc already in the tree (inherited from the previous tree) without re-baking -- keep it only when it can be used
   as is with the current source data and the macros of the current environment (checked the same way as doeff-hy's staleness check). path = location of the source (used to tell whether it is a Hy source)."
  (bytecode-is-current path existing data))


(defn #^ str pyc-path [#^ str source]  ; cannot be a defk: a path computation called inside comprehensions and the process pool
  "source path -> the path of the .pyc in __pycache__ in the same dir (the name import looks for -- <name>.<cache tag>.pyc)."
  (setv #(head tail) (posixpath.split source))
  (posixpath.join head "__pycache__" (+ (get (posixpath.splitext tail) 0) "." sys.implementation.cache-tag ".pyc")))


(defn #^ (| SourceNotCompiled None) compile-one [#^ str tree #^ str rel #^ str name]  ; cannot be a defk: called by a child of the process pool
  "Bake one source of the real tree and place it in __pycache__ (write to a separate file and replace). SourceNotCompiled when it cannot be baked or read."
  (setv source (posixpath.join tree rel))
  (try
    (with [f (open source "rb")] (setv data (.read f)))
    (except [error OSError]
      (return (SourceNotCompiled :path rel :reason (str error)))))   ; the message for an unreadable source has the same form as the refusal of the file effects (the OSError message)
  (setv cache (pyc-path source))
  ;; If the .pyc inherited from the previous tree matches the current source and the macros of the current environment, do not re-bake (ones that do not match are re-baked here.
  ;; Re-baking writes to a separate file and replaces, so the previous tree's .pyc behind a hardlink does not change).
  (when (os.path.isfile cache)
    (with [f (open cache "rb")] (setv existing (.read f)))
    (when (run (kept-pyc source data existing)) (return None)))
  (setv compiled (run (compiled-pyc rel name source data)))
  (when (isinstance compiled SourceNotCompiled) (return compiled))
  (os.makedirs (posixpath.dirname cache) :exist-ok True)
  (setv tmp (.format "{}.{}.tmp" cache (os.getpid)))
  (with [f (open tmp "wb")] (.write f compiled))
  (os.replace tmp cache)
  None)


(deff prepare-compile-path [tree roots]  ; cannot be a defk: the initializer of the process pool and the main of the console script (entry points called as plain functions)
  {:pre [(: tree str) (: roots tuple)] :post [(: % None)]}
  "Add the roots inside the tree to the import path of the baking process (earlier ones first), and keep imports during baking from writing timestamp-based .pyc files."
  (setv sys.dont-write-bytecode True)
  (for [root (reversed roots)]
    (.insert sys.path 0 (posixpath.join tree root))))


(deff _compile-item [item]  ; cannot be a defk: called as a plain function by a child of the process pool (passed to pool.map)
  {:pre [(: item tuple)] :post [(: % (| SourceNotCompiled None))]}
  "To bake one #(tree relative-path module-name) tuple in a pool child (SourceNotCompiled if it cannot be baked)."
  (compile-one #* item))


(defn #^ tuple compile-python-sources [#^ str tree #^ tuple items #^ int jobs #^ tuple roots]  ; cannot be a defk: runs the process pool
  "Bake the tree's sources (a sequence of #(relative-path module-name)) and place them in __pycache__, returning the sequence of SourceNotCompiled for those that could not be baked. Baking is independent
   per file and uses only CPU, so it is split across jobs processes (fork -- with spawn the child cannot resolve the function before re-importing the Hy modules)."
  (setv work (lfor #(rel name) items #(tree rel name)))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (lfor item work (_compile-item item))
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer prepare-compile-path :initargs #(tree roots))]
          (list (.map pool _compile-item work :chunksize 4)))))
  (tuple (gfor r results :if (is-not r None) r)))
