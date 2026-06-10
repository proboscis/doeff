(import httpx)


(defn fetch-status [url]
  (. httpx (get url) status-code))
