(defn write-log [message]
  (with [handle (open "workflow.log" "w")]
    (.write handle message)))
