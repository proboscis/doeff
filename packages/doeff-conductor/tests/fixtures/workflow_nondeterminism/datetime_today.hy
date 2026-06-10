(import datetime)


(defn build-prompt []
  (.isoformat (datetime.datetime.today)))
