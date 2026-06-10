(import pathlib [Path])


(defn write-log [message]
  (.write-text (Path "workflow.log") message))
