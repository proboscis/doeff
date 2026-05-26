;;; File operation handlers
;;; Handles WriteFile effect.

(require doeff_hy.macros [defhandler])
(import doeff [do :as _doeff-do])

(import subprocess)
(import pathlib [Path])

(import doeff_ml_nexus.effects [WriteFile])


(defhandler write-file-handler
  "Handle WriteFile: write content to a file on a host."
  (WriteFile [host path content]
    (if (= host "localhost")
        (.write-text (Path path) content)
        (subprocess.run ["ssh" host f"cat > {path}"]
                        :input (.encode content) :check True
                        :capture-output True))
    (resume path)))
