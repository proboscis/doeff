;;; 検体(違反をわざと書いた file): 設定から本体の引数を service_model.program-arguments の外で作る写し。
;;; doeff-cluster-program-arguments-are-built-in-one-place が 8 行目と 12 行目で発火する。
(import json)
(import doeff_cluster.service_model [resolve])

(defn run-service [args]
  (setv config (json.loads args.config))
  (setv kwargs (dfor #(k v) (.items config) (hy.mangle k) v))
  ((resolve args.factory) #** kwargs))

(defn replay [factory config]
  (factory #** (dfor #(k v) (.items config) (hy.mangle k) v)))
