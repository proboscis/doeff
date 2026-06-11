(import yaml)


(defn parse-config [raw]
  (yaml.safe-load raw))
