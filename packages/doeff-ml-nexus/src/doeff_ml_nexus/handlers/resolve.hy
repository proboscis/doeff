;;; Resolve handler
;;; Resolves targets to requested kinds using Ask-injected configuration.

(require doeff_hy.macros [<- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [Ask])

(import pathlib [Path])

(import doeff_ml_nexus.effects [Resolve])


(defhandler resolve-handler
  "Resolve handler using Ask-injected source_root.
   Supports:
     Resolve(target=str, kind=Path) -> Path(source_root / target)
   Extend by adding more match branches."
  (Resolve [target kind]
    ;; str id -> Path: look up in source_root
    (if (and (isinstance target str) (is kind Path))
        (do
          (<- source-root (Ask "source_root"))
          (resume (/ (Path source-root) target)))
        (raise (NotImplementedError
                 f"Resolve: unsupported target={target!r} kind={kind!r}")))))
