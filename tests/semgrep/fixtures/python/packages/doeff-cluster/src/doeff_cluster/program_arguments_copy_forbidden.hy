;;; Sample (a file with a deliberately written violation): a copy that builds the body's arguments from settings outside service_model.program-arguments.
;;; doeff-cluster-program-arguments-are-built-in-one-place fires at line 8 and line 12.
(import json)
(import doeff_cluster._shared.entry.service_build [resolve])

(defn run-service [args]
  (setv config (json.loads args.config))
  (setv kwargs (dfor #(k v) (.items config) (hy.mangle k) v))
  ((resolve args.factory) #** kwargs))

(defn replay [factory config]
  (factory #** (dfor #(k v) (.items config) (hy.mangle k) v)))
