(import subprocess)


(defn run-tests []
  (subprocess.run ["pytest"] :check True))
