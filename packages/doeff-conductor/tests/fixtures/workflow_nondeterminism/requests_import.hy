(import requests)


(defn fetch-status [url]
  (. (requests.get url) status-code))
