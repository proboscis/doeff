(require doeff-hy.handle [defhandler])

(defn forbidden-factory []
  (defhandler nested-handler []
    (object []
      (resume None))))
