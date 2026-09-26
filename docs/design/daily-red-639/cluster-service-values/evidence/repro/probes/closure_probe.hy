;;; 宣言を作りうる module の閉包を import し、生きている ServiceDef の値から本体の file と form を引けるかを測る(読むだけ)。
(import sys re gc time importlib inspect)
(import pathlib [Path])
(import hy)
(import hy.models [Expression Symbol])
(import doeff_cluster.service_model [ServiceDef])
(setv root (Path.cwd) bases ["controllers" "services"])
(setv HY-IMPORT (re.compile r"\(import\s+([^\[\]()]*)"))
(setv PY-IMPORT (re.compile r"(?m)^\s*(?:from\s+(\.*[\w.]+)\s+import|import\s+([\w.]+))"))
(defn dotted [p] (.join "." (. (.with-suffix (.relative-to p root) "") parts)))
(setv modules {})
(for [base bases]
  (for [p (sorted (+ (list (.rglob (/ root base) "*.hy")) (list (.rglob (/ root base) "*.py"))))]
    (when (in "adr" (. (.relative-to p root) parts)) (continue))
    (setv name (dotted p))
    (when (.endswith name ".__init__") (setv name (cut name 0 -9)))
    (setv (get modules name) p)))
(defn resolve-rel [name target]
  (if (.startswith target ".")
      (do (setv dots (- (len target) (len (.lstrip target "."))) parts (.split name "."))
          (.join "." (+ (cut parts 0 (- (len parts) dots)) (if (.lstrip target ".") [(.lstrip target ".")] []))))
      target))
(setv imports {})
(for [#(name p) (.items modules)]
  (setv text (.read-text p :encoding "utf-8") found #{})
  (if (= p.suffix ".hy")
      (for [m (.finditer HY-IMPORT text)]
        (for [tok (.split (.group m 1))]
          (when (and tok (not (.startswith tok ":"))) (.add found (resolve-rel name (hy.mangle tok))))))
      (for [m (.finditer PY-IMPORT text)]
        (.add found (resolve-rel name (or (.group m 1) (.group m 2))))))
  (setv (get imports name) found))
(setv closure (sfor #(n i) (.items imports) :if (in "doeff_cluster.service_model" i) n))
(setv grew True)
(while grew
  (setv more (sfor #(n i) (.items imports) :if (and (not-in n closure) (& i closure)) n))
  (setv grew (bool more))
  (|= closure more))
(print "modules" (len modules) "closure" (len closure))
(setv t0 (time.time) failed [])
(for [n (sorted closure)]
  (try (importlib.import-module n)
    (except [e Exception] (.append failed #(n (. (type e) __name__) (cut (str e) 0 120))))))
(print "import seconds" (round (- (time.time) t0) 1) "failed" failed)
(setv defs (lfor o (gc.get-objects) :if (isinstance o ServiceDef) o))
(setv facts {})
(for [s defs] (setv (get facts s.factory) s))
(print "ServiceDef objects" (len defs) "distinct factories" (len facts))
(setv files {} outside [] unread [])
(for [#(f s) (sorted (.items facts))]
  (setv fn (inspect.unwrap s.program-factory))
  (setv src (Path fn.__code__.co_filename))
  (if (not (.is-relative-to src root)) (.append outside #(f (str src)))
      (.setdefault files src [] ) )
  (when (.is-relative-to src root) (.append (get files src) fn.__name__)))
(setv t1 (time.time))
(for [#(src names) (.items files)]
  (setv tops (sfor form (hy.read-many (.read-text src :encoding "utf-8"))
                   :if (and (isinstance form Expression) (> (len form) 1) (isinstance (get form 1) Symbol))
                   #((str (get form 0)) (hy.mangle (str (get form 1))))))
  (for [n names]
    (setv heads (lfor #(h m) tops :if (= m n) h))
    (when (!= heads ["defk"]) (.append unread #((str (.relative-to src root)) n heads)))))
(print "body files" (len files) "read seconds" (round (- (time.time) t1) 1) "outside" outside "unread" unread)
