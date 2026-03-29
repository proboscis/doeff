;;; Resolve handler
;;; Resolves targets to requested kinds using Ask-injected configuration.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass])
(import doeff_core_effects [Ask])

(import pathlib [Path])

(import doeff_ml_nexus.effects [Resolve])


(defk resolve-handler [effect k]
  "Resolve handler using Ask-injected source_root.
   Supports:
     Resolve(target=str, kind=Path) -> Path(source_root / target)
   Extend by adding more match branches."
  (if (isinstance effect Resolve)
      (cond
        ;; str id -> Path: look up in source_root
        (and (isinstance effect.target str) (is effect.kind Path))
        (do
          (<- source-root (Ask "source_root"))
          (yield (Resume k (/ (Path source-root) effect.target))))

        True
        (raise (NotImplementedError
                 f"Resolve: unsupported target={effect.target!r} kind={effect.kind!r}")))
      (yield (Pass effect k))))
