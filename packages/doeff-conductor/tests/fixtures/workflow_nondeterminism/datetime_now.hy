(import datetime [datetime])


(defn build-prompt []
  (.isoformat (datetime.now)))
