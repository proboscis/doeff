(import time)


(defn build-prompt []
  (str (time.monotonic)))
