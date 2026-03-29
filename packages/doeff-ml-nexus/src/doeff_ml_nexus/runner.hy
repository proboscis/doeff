;;; Remote runner Programs.
;;;
;;; File-based (using env vars):
;;;   DOEFF_INPUT=/exchange/program.pkl DOEFF_OUTPUT=/exchange/result.pkl \
;;;     uv run doeff run --program doeff_ml_nexus.runner.p_run \
;;;       --interpreter doeff_ml_nexus.runner.runner_interpreter

(require doeff_hy.macros [defk defprogram <-])
(require doeff_docker.compose [with-handlers])
(import doeff [do :as _doeff-do])
(import doeff [run WithHandler])
(import doeff_core_effects [reader slog-handler writer scheduled])

(import os)
(import pathlib [Path])

(import doeff_ml_nexus.serializer [default-serializer])


(defk run-from-file [input-path output-path serializer]
  "Load pickled Program from input-path, execute, write result to output-path."
  (setv data (.read-bytes (Path input-path)))
  (setv program (.loads serializer data))
  (setv result (run program))
  (.write-bytes (Path output-path) (.dumps serializer result))
  output-path)

(defk get-env-paths []
  "Read input/output paths from environment variables at runtime."
  (setv input-path (os.environ.get "DOEFF_INPUT" "/tmp/doeff-exchange/program.pkl"))
  (setv output-path (os.environ.get "DOEFF_OUTPUT" "/tmp/doeff-exchange/result.pkl"))
  #(input-path output-path))

(defprogram p-run
  (<- #(input-path output-path) (get-env-paths))
  (<- result (run-from-file input-path output-path default-serializer))
  result)


(defn runner-interpreter [program * [env None]]  ; doeff: interpreter
  "Minimal interpreter for the runner program."
  (run (scheduled
    (with-handlers [(reader :env (or env {})) (slog-handler) (writer)]
      program))))
