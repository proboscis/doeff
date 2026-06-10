(import urllib.request)


(defn fetch [url]
  (.read (urllib.request.urlopen url)))
