(import urllib [request])


(defn fetch [url]
  (.read (request.urlopen url)))
