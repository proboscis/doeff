;;; File operation handlers
;;; Handles WriteFile effect.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass])

(import subprocess)
(import pathlib [Path])

(import doeff_ml_nexus.effects [WriteFile])


(defk write-file-handler [effect k]
  "Handle WriteFile: write content to a file on a host."
  (if (isinstance effect WriteFile)
      (do
        (if (= effect.host "localhost")
            (.write-text (Path effect.path) effect.content)
            (subprocess.run ["ssh" effect.host f"cat > {effect.path}"]
                            :input (.encode effect.content) :check True
                            :capture-output True))
        (yield (Resume k effect.path)))
      (yield (Pass effect k))))
