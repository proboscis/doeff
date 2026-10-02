;;; 展開したコードの木の bytecode の準備の純粋な判断 — module 名・引き継ぐ .pyc の組・焼く物・完成の印の中身と検め・import の静的な辿り
;;; (code_prepare.hy から分けた・#2027)。effect は worker/intent/code_model、焼きの Program は worker/core/code_prepare。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import ast)
(import pathlib [PurePosixPath])
(import json)
(import sys)
(import doeff_cluster.worker.intent.code_model [DEFAULT-IMPORT-ROOTS])


(setv SOURCE-SUFFIXES #(".py" ".hy"))




;; 完成の印の file(木の根・隠し file なので走査と git archive の中身には混ざらない)と、その形の版。
(setv MARKER ".doeff-code-ready.json")


(setv MARKER-FORMAT 1)


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
  "焼く物 = (相対 path module 名) の列。import の根の外の物と、.pyc が既に在る Python の source は除く。Hy の source は .pyc が在っても
   焼く物に入れる — 前の木から引き継いだ .pyc の展開が依った macro は今の木で変わりうるので、焼く所(doeff-core-effects の
   compile-python-sources)が今の macro と照らし、合う物は焼き直さずに残し、合わない物を焼き直す(#2598 — ここで除くと、
   macro の変わった版の初回の import が引き継いだ Hy の module を全部 compile し直す)。"
  (lfor source sources
        :setv name (module-name source roots)
        :if (and (is-not name None) (or (.endswith source ".hy") (not-in (cache-rel source) pycs)))
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
        ;; form は読んだ Hy の値(Expression だけを読み、列は中へ降りる)。
        (defn #^ None walk [#^ hy.models.Object form]
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
