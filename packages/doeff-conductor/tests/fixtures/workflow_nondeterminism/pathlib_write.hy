(import pathlib)


(defn write-log [message]
  (.write-text (pathlib.Path "workflow.log") message))
