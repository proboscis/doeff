;;; 展開したコードの木の bytecode を、木の中だけに「実行時に source の hash を検める」方式で用意する。
;;;
;;; worker は版ごとに木を展開する。前の版の木から、中身の変わっていない file の .pyc を hardlink で
;;; 引き継ぎ、残りだけを焼く。検める方式(PEP 552 の checked hash)なので、引き継ぎを誤っても import が
;;; source の hash を突き合わせて焼き直す — 古い bytecode が黙って使われることはない。
;;; 共有の venv・doeff・標準 library には書かない(本番の image の焼き方 deploy/bytecode.py は木の外も歩き、
;;; 実行時に検めない方式で焼くので、中身の動く手元の環境には使えない)。
;;;
;;; 形: 判断(module 名・引き継ぐ組・焼く物)は純粋な関数、走査・hardlink・焼きは effect(handler は下の local-tree)。
;;; 経過の秒は doeff-time の GetMonotonic(main が sync-time-handler を被せる)。
;;;
;;; 完成の印: 焼いた後に木を走査し直し、焼くべき source ごとに .pyc が在ること(焼けなかった file は理由つきで
;;; 印に載せる)を検めてから、木の根に印の file(MARKER)を置く。検めが通らなければ印を置かず、0 でない終了で
;;; 終わる。worker(handlers.hy の CodeStore)は印の在る木だけを完成品として公開し、読む時にも印を検める。
;;;
;;; 道具は worker 自身のコードから file の path で起動する(準備する版の木から -m で起動すると、道具を持たない
;;; 古い版では道具が見つからない — 2026-09-23 に atlas の版 8d7181f が bytecode 0 のまま完成品になった原因)。
;;;
;;;   PYTHONDONTWRITEBYTECODE=1 hy <worker のコード>/doeff_cluster/code_prepare.hy <新しい木> --revision <版>
;;;       [--from <前の木> --changed <変わった path の一覧 file>] [--import-roots .,sub/dir]
;;;
;;; import の根(木の中の dir・`,` で並べる・既定 `.`)は業務の repo の形で、worker の CodeLayout(worker_model)が渡す。
;;;
;;; 焼く範囲(2026-09-26・#664 の実測): --entries <module,…> を渡すと、その module たちの import の閉包(Hy の import / require と
;;; Python の import を静的に辿る)だけを焼く。閉包の外の module は子が import した時に作られる(焼く物が減るだけで正しさは変わらない)。
;;; 並列数の既定は cgroup の CPU の上限(pod の limits)— node の CPU の数で焼くと、上限 4 の pod で 16 並列になり周期の 97% が絞られた。
(require doeff-hy.macros [defk defhandler <- val var])
(import argparse)
(import ast)
(import math)
(import dataclasses [dataclass])
(import importlib.machinery)
(import importlib.util)
(import json)
(import multiprocessing)
(import os)
(import sys)
(import concurrent.futures [ProcessPoolExecutor])
(import importlib._bootstrap_external [_code_to_hash_pyc])  ; PEP 552 の頭を組む公式の実装
(import collections.abc [Callable])
(import pathlib [Path PurePosixPath])
(import doeff [EffectBase run])
(import doeff_time [GetMonotonic sync-time-handler])
(import doeff_core_effects.file_effects [PathKind PathStat StatPath ReadText ReadBytes WriteText WriteBytes MakeDirectory WalkTree CopyFile
                                         file-done])

(setv SOURCE-SUFFIXES #(".py" ".hy"))
(setv DEFAULT-IMPORT-ROOTS #("."))
;; 完成の印の file(木の根・隠し file なので走査と git archive の中身には混ざらない)と、その形の版。
(setv MARKER ".doeff-code-ready.json")
(setv MARKER-FORMAT 1)


;; --- 純粋な判断 ------------------------------------------------------------------------

(defn #^ (| str None) module-name [#^ str rel #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "木の根からの相対 path(posix)→ import の根(roots の順)からの module 名。根の外なら None。
   根 `.` は、ほかの根の先頭の dir の下を数えない(その下は別の根からの名で import される)。"
  (setv path (PurePosixPath rel)
        nested (sfor r roots :if (!= r ".") (get (. (PurePosixPath r) parts) 0)))
  (for [root roots]
    (setv parts
      (if (= root ".")
          (list (. (.with-suffix path "") parts))
          (if (.is-relative-to path root)
              (list (. (.with-suffix (.relative-to path root) "") parts))
              None)))
    (when (is parts None) (continue))
    (when (and (= root ".") parts (in (get parts 0) nested)) (continue))
    (when (and parts (= (get parts -1) "__init__")) (setv parts (cut parts 0 -1)))
    (return (.join "." parts)))
  None)


(defn #^ (| str None) source-of-pyc [#^ str pyc-rel #^ frozenset sources]
  "a/__pycache__/m.cpython-314.pyc → a/m.py か a/m.hy のうち木に在る方。"
  (setv pyc (PurePosixPath pyc-rel)
        stem (get (.split pyc.name "." 1) 0)
        base (. pyc parent parent))
  (for [suffix SOURCE-SUFFIXES]
    (setv candidate (.as-posix (/ base (+ stem suffix))))
    (when (in candidate sources) (return candidate)))
  None)


(defn #^ list carry-pairs [#^ list old-pycs #^ frozenset old-sources #^ frozenset new-sources #^ frozenset new-pycs
                           #^ frozenset changed]
  "前の木の .pyc のうち、source が変わっておらず新しい木にも在り、新しい木にまだ .pyc の無い物(相対 path の列)。"
  (lfor pyc old-pycs
        :setv source (source-of-pyc pyc old-sources)
        :if (and (is-not source None) (not-in source changed) (in source new-sources) (not-in pyc new-pycs))
        pyc))


(defn #^ list compile-plan [#^ list sources #^ frozenset pycs #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "焼く物 = (相対 path module 名) の列。.pyc が既に在る物と、import の根の外の物は除く。"
  (lfor source sources
        :setv name (module-name source roots)
        :if (and (is-not name None) (not-in (cache-rel source) pycs))
        #(source name)))


(defn #^ str cache-rel [#^ str source-rel]
  (setv path (PurePosixPath source-rel))
  (.as-posix (/ path.parent "__pycache__"
                (+ path.stem "." sys.implementation.cache-tag ".pyc"))))


(defn #^ list compilable [#^ list sources #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "焼くべき source(import の根の中に在る物)の相対 path の列。"
  (lfor source sources :if (is-not (module-name source roots) None) source))


(defn #^ list missing-pycs [#^ list sources #^ frozenset pycs #^ frozenset failed #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "焼くべき source のうち、.pyc が無く、焼けなかった物としても記録されていない物。空なら検めが通る。"
  (lfor source (compilable sources roots)
        :if (and (not-in (cache-rel source) pycs) (not-in source failed))
        source))


(defn #^ (| str None) tree-problem [#^ list sources #^ frozenset pycs #^ frozenset failed #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "焼いた後の木の検め。通れば None、通らなければ理由。"
  (setv wanted (compilable sources roots) missing (missing-pycs sources pycs failed roots))
  (cond
    (not wanted) "木に焼くべき source が 1 つも無い(展開に失敗した木に見える)"
    (and failed (= (len failed) (len wanted))) f"焼くべき {(len wanted)} file が全部焼けなかった(道具か環境の失敗に見える)"
    missing (+ f"焼いたはずの .pyc が {(len missing)} file 無い: " (.join " " (cut missing 0 5)))
    True None))


(defn #^ dict marker-content [#^ str revision #^ bool bytecode #^ list sources #^ frozenset pycs #^ list failures
                             #^ tuple [roots DEFAULT-IMPORT-ROOTS]]
  "完成の印の中身。failures = #(相対 path 理由) の列。"
  {"format" MARKER-FORMAT "revision" revision "bytecode" bytecode
   "compilable" (len (compilable sources roots)) "pycs" (len pycs)
   "failed" (lfor #(rel reason) failures {"path" rel "reason" reason})})


(defn #^ (| str None) marker-problem [#^ (| str None) text #^ str revision #^ bool want-bytecode #^ int pycs-on-disk]
  "読む時の完成の印の検め。text = 印の file の中身(無ければ None)。通れば None、通らなければ理由。"
  (when (is text None) (return "完成の印が無い(印を置く前の形で作られた木か、途中で止まった木)"))
  (try
    (setv marker (json.loads text))
    (except [error ValueError] (return f"完成の印を読めない: {(repr error)}")))
  (when (not (isinstance marker dict)) (return "完成の印の形が違う"))
  (setv form (.get marker "format") named (.get marker "revision") pycs (.get marker "pycs" 0))
  (cond
    (!= form MARKER-FORMAT) f"完成の印の形の版が違う: {form}"
    (!= named revision) f"完成の印の版({named})が木の名前と違う"
    (and want-bytecode (not (.get marker "bytecode"))) "bytecode を焼かずに作られた木"
    (and want-bytecode (< pycs-on-disk pycs)) f"印では .pyc が {pycs} file のはずが {pycs-on-disk} file しか無い"
    True None))


;; --- effect ----------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ScanTree [EffectBase]
  "結果 = #(source の相対 path の列 .pyc の相対 path の列)(隠し file と __pycache__ の外の .pyc は除く)。"
  (#^ str tree))


(defclass [(dataclass :frozen True)] LinkPycs [EffectBase]
  "old の .pyc を new の同じ相対 path へ hardlink する。結果 = 張った数。"
  (#^ str old)
  (#^ str new)
  (#^ tuple pycs))


(defclass [(dataclass :frozen True)] CompileSources [EffectBase]
  "結果 = 焼けなかった物の #(相対 path 理由) の列。"
  (#^ str tree)
  (#^ tuple items)
  (#^ int jobs)
  (setv #^ tuple roots DEFAULT-IMPORT-ROOTS))


(defclass [(dataclass :frozen True)] ImportClosure [EffectBase]
  "結果 = entries(module 名)の import の閉包に入る source の相対 path の frozenset(焼く範囲を絞るため)。"
  (#^ str tree)
  (#^ tuple sources)
  (#^ tuple entries)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] WriteMarker [EffectBase]
  "完成の印を木の根へ置く(別の file へ書いて置き換える)。"
  (#^ str tree)
  (#^ dict content))


(defclass [(dataclass :frozen True)] Note [EffectBase]
  (#^ str line))


;; --- Program ---------------------------------------------------------------------------

;; 木を焼き、検めが通れば完成の印を置く。結果の "problem" が None でなければ印は置いていない。
(defk prepare-tree [tree revision old changed jobs roots [entries #()]]
  {:pre [(: tree str) (: revision str) (: old (| str None)) (: changed frozenset) (: jobs int) (: roots tuple) (: entries tuple)]
   :post [(: % dict)]}
  ;; entries が在れば、焼く物と検めの対象をその import の閉包に絞る(閉包の外は import の時に作られる)。
  (<- started float (GetMonotonic))
  (<- scanned tuple (ScanTree tree))
  (setv #(all-sources pycs) scanned)
  (setv scope None)
  (when entries
    (<- closure frozenset (ImportClosure tree (tuple all-sources) entries roots))
    (setv scope closure))
  (setv sources (if (is scope None) all-sources (lfor s all-sources :if (in s scope) s)))
  (setv carried 0)
  (when (is-not old None)
    (<- old-scan tuple (ScanTree old))
    (setv #(old-sources old-pycs) old-scan)
    (setv pairs (carry-pairs old-pycs (frozenset old-sources) (frozenset sources) (frozenset pycs) changed))
    (<- linked int (LinkPycs old tree (tuple pairs)))
    (setv carried linked pycs (+ pycs pairs)))
  (<- carried-at float (GetMonotonic))
  (setv plan (compile-plan sources (frozenset pycs) roots))
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
        failed (frozenset (gfor #(rel _) failures rel))
        problem (tree-problem after-sources (frozenset after-pycs) failed roots))
  (if (is problem None)
      (<- (WriteMarker tree (marker-content revision True after-sources (frozenset after-pycs) failures roots)))
      (<- (Note (+ "検めが通らないので完成の印を置きません: " problem))))
  (| summary {"problem" problem}))


;; --- handler(実 I/O) ------------------------------------------------------------------

;; --- 本物(local-tree)と fake(files-tree)が同じく通る判断 ------------------------------------------

(defk tree-listing [rels]
  {:pre [(: rels (| list tuple))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
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


(defk compiled-pyc [rel name path data]
  {:pre [(: rel str) (: name str) (: path str) (: data bytes)] :post [(: % (| bytes tuple))] :tags {:context "doeff-cluster" :role "judgment"}}
  "source の中身 1 つを、import が検める方式(PEP 552 の checked hash)の .pyc の中身にするため。焼けない時は #(相対 path 理由)
   (import の時に同じ誤りが出るので、ここでは記録だけ)。path = source の在処(Hy の source かの見分けと、誤りの文に出る名)。"
  (try
    (val loader (importlib.machinery.SourceFileLoader name path))
    (val code (.source-to-code loader data path))
    (bytes (_code-to-hash-pyc code (importlib.util.source-hash data) True))
    (except [error Exception]
      #(rel (.format "{}: {}" (. (type error) __name__) (cut (str error) 0 200))))))


(defk marker-text [content]
  {:pre [(: content dict)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "完成の印の中身を file の text にするため。"
  (json.dumps content :ensure-ascii False :indent 1))


(defk closure-of [sources entries roots read]
  {:pre [(: sources (| list tuple)) (: entries tuple) (: roots tuple) (: read Callable)] :post [(: % frozenset)]
   :tags {:context "doeff-cluster" :role "judgment"}}
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


;; --- 本物の file system の答え手の部品 ------------------------------------------------------------------

(defn #^ (| tuple None) compile-one [#^ str tree #^ str rel #^ str name]
  "1 file を焼く。焼けない時は #(相対 path 理由) を返す(焼きの判断は compiled-pyc — fake と同じ関数)。"
  (setv source (/ (Path tree) rel) data (.read-bytes source))
  (setv compiled (run (compiled-pyc rel name (str source) data)))
  (when (isinstance compiled tuple) (return compiled))
  (setv cache (Path (importlib.util.cache-from-source (str source))))
  (.mkdir cache.parent :parents True :exist-ok True)
  (setv tmp (.with-suffix cache (.format ".{}.tmp" (os.getpid))))
  (.write-bytes tmp compiled)
  (os.replace tmp cache)
  None)


(defn _init-pool [#^ str tree #^ tuple roots]
  (setv sys.dont-write-bytecode True)
  (for [root (reversed roots)]
    (.insert sys.path 0 (str (/ (Path tree) root)))))


(defn _compile-task [item]
  (compile-one #* item))


(defk scan [tree]
  {:pre [(: tree str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本物の木を走査して #(source の列 .pyc の列) を答えるため(隠し dir の下へは入らない — どれを数えるかの判断は tree-listing)。"
  (val root (Path tree))
  (val rels [])
  (for [#(dirpath dirnames filenames) (os.walk root)]
    (val kept (lfor d dirnames :if (not (.startswith d ".")) d))
    ;; os.walk は dirnames の list そのものを見て降りる先を決めるので、中身を入れ替える。
    (.clear dirnames)
    (.extend dirnames kept)
    (val rel-dir (.as-posix (.relative-to (Path dirpath) root)))
    (for [name filenames]
      (.append rels (if (= rel-dir ".") name (+ rel-dir "/" name)))))
  (<- listed tuple (tree-listing rels))
  listed)


(defn #^ int cpu-limit-of [#^ (| str None) cpu-max #^ int available]
  "cgroup v2 の cpu.max の中身(\"<quota> <period>\" か \"max <period>\")と使える CPU の数 → 焼きの並列数(pod の上限を越えないため)。"
  (setv parts (if cpu-max (.split cpu-max) []))
  (if (and (= (len parts) 2) (!= (get parts 0) "max"))
      (max 1 (min available (math.ceil (/ (int (get parts 0)) (int (get parts 1))))))
      (max 1 available)))


(defn #^ int usable-cpus []
  "この process が使える CPU の数(affinity と cgroup の上限の小さい方)— 焼きの並列数の既定。"
  (setv available (if (hasattr os "sched_getaffinity") (len (os.sched-getaffinity 0)) (or (os.cpu-count) 1))
        path (Path "/sys/fs/cgroup/cpu.max"))
  (cpu-limit-of (if (.is-file path) (.read-text path) None) available))


(defn #^ tuple imported-names [#^ str rel #^ str text]
  "source 1 つが import する名の列 #(#(点の数 名 取り出す名の tuple) …)(Hy は import と require の形、Python は ast)。
   読めない file は空(閉包から外れるだけ — その module は import の時に作られる)。"
  (setv found [])
  (if (.endswith rel ".py")
      (try
        (for [node (ast.walk (ast.parse text))]
          (cond
            (isinstance node ast.Import) (for [a node.names] (.append found #(0 a.name #())))
            (isinstance node ast.ImportFrom)
              (.append found #(node.level (or node.module "") (tuple (gfor a node.names a.name))))))
        (except [SyntaxError] None))
      (try
        (import hy)
        (defn walk [form]
          (when (isinstance form hy.models.Expression)
            (when (and form (isinstance (get form 0) hy.models.Symbol) (in (str (get form 0)) #("import" "require")))
              (setv items (list (cut form 1 None)) i 0 current None)
              (while (< i (len items))
                (setv item (get items i))
                (cond
                  (isinstance item hy.models.Keyword) (+= i 1)
                  (isinstance item hy.models.Symbol)
                    (do (setv name (str item) dots (- (len name) (len (.lstrip name "."))))
                        (setv current #(dots (.join "." (gfor part (.split (.lstrip name ".") ".") :if part (hy.mangle part))) []))
                        (.append found current))
                  ;; 点を含む名は (. a b) の形で読まれる。相対の名は (. None b)・(.. None a b)(点の数 = 頭の記号の長さ)。
                  (and (isinstance item hy.models.Expression) item (isinstance (get item 0) hy.models.Symbol)
                       (= (.strip (str (get item 0)) ".") ""))
                    (do (setv rest (list (cut item 1 None))
                              relative (and rest (= (str (get rest 0)) "None"))
                              dots (if relative (len (str (get item 0))) 0)
                              parts (if relative (cut rest 1 None) rest))
                        (setv current #(dots (.join "." (gfor part parts (hy.mangle (str part)))) []))
                        (.append found current))
                  (and (isinstance item hy.models.List) (is-not current None))
                    (.extend (get current 2) (gfor x item :if (isinstance x hy.models.Symbol) (hy.mangle (str x)))))
                (+= i 1))))
          (when (isinstance form hy.models.Sequence)
            (for [x form] (walk x))))
        (for [form (hy.read-many text :filename rel)] (walk form))
        (except [Exception] None)))
  (tuple (gfor #(dots name names) found #(dots name (tuple names)))))


(defk import-closure [tree sources entries roots]
  {:pre [(: tree str) (: sources (| list tuple)) (: entries tuple) (: roots tuple)] :post [(: % frozenset)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "本物の木の file を読んで、entries の import の閉包に入る source の相対 path を求めるため(辿り方の判断は closure-of)。"
  (<- closure frozenset (closure-of sources entries roots (fn [rel] (.read-text (/ (Path tree) rel) :encoding "utf-8" :errors "replace"))))
  closure)


(defn #^ int link-pycs [#^ str old #^ str new #^ tuple pycs]
  (setv count 0)
  (for [rel pycs]
    (setv target (/ (Path new) rel))
    (.mkdir target.parent :parents True :exist-ok True)
    (when (not (.exists target))
      ;; import が焼き直す時は別 file へ書いて置き換えるので、共有しても壊れない。
      (os.link (/ (Path old) rel) target)
      (+= count 1)))
  count)


(defn #^ list compile-sources [#^ str tree #^ tuple items #^ int jobs #^ tuple roots]
  (setv work (lfor #(rel name) items #(tree rel name)))
  (setv results
    (if (or (<= jobs 1) (<= (len work) 1))
        (lfor item work (_compile-task item))
        ;; 焼きは 1 file ずつ独立で CPU だけを使うので、process に分ける(Hy の macro 展開が大半)。
        ;; fork にする: spawn では子が Hy の module を import し直す前に関数を解けない。
        (with [pool (ProcessPoolExecutor :max-workers jobs :mp-context (multiprocessing.get-context "fork")
                                         :initializer _init-pool :initargs #(tree roots))]
          (list (.map pool _compile-task work :chunksize 4)))))
  (lfor r results :if (is-not r None) r))


(defk write-marker [tree content]
  {:pre [(: tree str) (: content dict)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "完成の印を本物の木の根へ置くため(別の file へ書いて置き換える — 書きかけの印を読ませない)。"
  (val target (/ (Path tree) MARKER))
  (val tmp (/ (Path tree) (+ MARKER ".tmp")))
  (<- text str (marker-text content))
  (.write-text tmp text :encoding "utf-8")
  (os.replace tmp target)
  None)


(defhandler local-tree []
  ;; 本物: 木の走査・hardlink・焼きを os と process の pool で行う(本番の入口 = 下の main)。
  (ScanTree [tree]
    (<- found tuple (scan tree))
    (resume found))
  (LinkPycs [old new pycs] (resume (link-pycs old new pycs)))
  (ImportClosure [tree sources entries roots]
    (<- closure frozenset (import-closure tree sources entries roots))
    (resume closure))
  (CompileSources [tree items jobs roots] (resume (compile-sources tree items jobs roots)))
  (WriteMarker [tree content]
    (<- (write-marker tree content))
    (resume None))
  (Note [line] (print line :file sys.stderr :flush True) (resume None)))


;; --- fake: file system の effect の上の木(files-tree)--------------------------------------------------
;; 木の効果に、汎用の file system の effect(doeff_core_effects.file_effects)で答える。模擬の世界では memory-file-handler を外側に
;; 被せて、I/O なしで焼きの Program(prepare-tree)を走らせる。走査・閉包・焼き・印の中身の判断は本物と同じ関数(tree-listing・
;; closure-of・compiled-pyc・marker-text)を通る。本物との違い: hardlink の代わりに写す(中身は同じ)・焼きは並列にしない
;; (jobs を読まない)・焼きの間の import の路を足さない(焼く source の macro が木の中の別の module を require する時は本物だけが解ける)・
;; Note は捨てる(模擬の世界に stderr は無い)・Hy の source は焼けない(doeff-hy の _could_be_hy_src が os.path.isfile で Hy の source かを
;; 見るので、disk に無い source は Python として読まれ SyntaxError の失敗になる — 契約テスト test_tree_contract.hy の頭の註)。

(defk tree-file-rels [tree]
  {:pre [(: tree str)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木の下の file の相対 path を並べるため(無い木は空 — 本物の os.walk が無い dir で何も出さないのと同じ)。"
  (<- walked (WalkTree tree))
  (if (isinstance walked tuple)
      (lfor entry walked :if (= entry.kind PathKind.FILE) entry.name)
      []))


(defk copy-pycs [old new pycs]
  {:pre [(: old str) (: new str) (: pycs tuple)] :post [(: % int)] :tags {:context "doeff-cluster" :role "foundation"}}
  "前の木の .pyc を新しい木の同じ相対 path へ写し、写した数を返すため(写し先が在れば写さない — 本物の hardlink の代わり)。"
  (var count 0)
  (for [rel pycs]
    (val target (os.path.join new rel))
    (<- (file-done (MakeDirectory (os.path.dirname target))))
    (<- found (file-done (StatPath target)))
    (match found
      (PathStat :kind PathKind.MISSING) (do (<- (file-done (CopyFile (os.path.join old rel) target)))
                                            (:= count (+ count 1)))
      _ None))
  count)


(defk source-texts [tree sources]
  {:pre [(: tree str) (: sources (| list tuple))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "木の source の相対 path → text を読むため(閉包の辿りに渡す)。"
  (val texts {})
  (for [rel sources]
    (<- text (file-done (ReadText (os.path.join tree rel))))
    (.update texts {rel text}))
  texts)


(defk place-compiled [tree rel name data]
  {:pre [(: tree str) (: rel str) (: name str) (: data bytes)] :post [(: % (| tuple None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "source 1 つの中身を compiled-pyc で焼いて __pycache__ へ置くため(焼けなければ置かずに #(相対 path 理由) を返す)。"
  (<- compiled (compiled-pyc rel name (os.path.join tree rel) data))
  (match compiled
    (bytes) (do (val cache (os.path.join tree (cache-rel rel)))
                (<- (file-done (MakeDirectory (os.path.dirname cache))))
                (<- (file-done (WriteBytes cache compiled :replace True)))
                None)
    failure failure))


(defk compile-in-files [tree items]
  {:pre [(: tree str) (: items tuple)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "焼く物を読んで place-compiled で焼き、焼けなかった物の #(相対 path 理由) の list を返すため。"
  (val failures [])
  (for [#(rel name) items]
    (<- data (file-done (ReadBytes (os.path.join tree rel))))
    (match data
      (bytes) (do (<- failure (place-compiled tree rel name data))
                  (when (is-not failure None)
                    (.append failures failure)))
      other (raise (TypeError (.format "ReadBytes の答えが bytes でない: {!r}" other)))))
  failures)


(defhandler files-tree
  ;; fake(上の註): 木の効果を file system の effect へ出し直す。
  (ScanTree [tree]
    (<- rels list (tree-file-rels tree))
    (<- listed tuple (tree-listing rels))
    (resume listed))
  (LinkPycs [old new pycs]
    (<- copied int (copy-pycs old new pycs))
    (resume copied))
  (ImportClosure [tree sources entries roots]
    (<- texts dict (source-texts tree sources))
    (<- closure frozenset (closure-of sources entries roots (fn [rel] (get texts rel))))
    (resume closure))
  (CompileSources [tree items jobs roots]
    (<- failures list (compile-in-files tree items))
    (resume failures))
  (WriteMarker [tree content]
    (<- text str (marker-text content))
    (<- (file-done (WriteText (os.path.join tree MARKER) text :replace True)))
    (resume None))
  (Note [line]
    (resume None)))


(defn main []
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "tree")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--from" :dest "old")
  (.add-argument parser "--changed")
  (.add-argument parser "--jobs" :type int :default (usable-cpus) :help "焼きの並列数(既定 = cgroup の CPU の上限)")
  (.add-argument parser "--entries" :default "" :help "焼く範囲の入口の module(`,` で並べる・空 = 根の下を全部)")
  (.add-argument parser "--import-roots" :default "." :help "木の中の import の根(`,` で並べる・前が先)")
  (setv args (.parse-args parser))
  (setv roots (tuple (gfor r (.split args.import-roots ",") :if r r)))
  (setv tree (str (.resolve (Path args.tree))))
  ;; 焼く途中の import(Hy の require 等)が timestamp 方式の .pyc を書かないようにする。
  (setv sys.dont-write-bytecode True)
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster)が sys.path の先頭に入る。
  ;; 焼く木の module 名がそこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (_init-pool tree roots)
  (setv changed (frozenset (if args.changed (.split (.read-text (Path args.changed))) [])))
  (setv old (if args.old (str (.resolve (Path args.old))) None))
  (setv entries (tuple (gfor e (.split args.entries ",") :if e e)))
  (setv summary (run ((sync-time-handler) ((local-tree) (prepare-tree tree args.revision old changed args.jobs roots entries)))))
  (when (is-not (get summary "problem") None)
    (print (+ "準備に失敗: " (get summary "problem")) :file sys.stderr :flush True)
    (sys.exit 1)))


(when (= __name__ "__main__")
  (main))
