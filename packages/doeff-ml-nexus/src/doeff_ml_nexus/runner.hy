;;; Remote runner Programs.
;;;
;;; File-based (launcher env vars translated into Program env):
;;;   DOEFF_INPUT=/exchange/program.pkl DOEFF_OUTPUT=/exchange/result.pkl \
;;;     uv run doeff run --program doeff_ml_nexus.runner.p_run \
;;;       --interpreter doeff_ml_nexus.runner.runner_interpreter

(require doeff_hy.macros [defk defp <-])
(require doeff_docker.compose [with-handlers])
(import doeff [do :as _doeff-do])
(import doeff [run WithHandler])
(import doeff_core_effects [Ask reader slog-handler writer scheduled])

(import pathlib [Path])

(import doeff_ml_nexus.serializer [default-serializer])
(import doeff_ml_nexus.runner_env [RUNNER-INPUT-PATH-KEY RUNNER-OUTPUT-PATH-KEY
                                   resolve-runner-env])


(defk run-from-file [input-path output-path serializer]
  "Load pickled Program from input-path, execute, write result to output-path."
  {:pre [(: input-path str) (: output-path str) (: serializer "Serializer protocol")]
   :post [(: % str)]}
  (setv data (.read-bytes (Path input-path)))
  (setv program (.loads serializer data))
  (setv result (run program))
  (.write-bytes (Path output-path) (.dumps serializer result))
  output-path)

(defk get-exchange-paths []
  "Read runner file exchange paths from injected doeff Program env."
  {:pre []
   :post [(: % tuple)]}
  (<- input-path str (Ask RUNNER-INPUT-PATH-KEY))
  (<- output-path str (Ask RUNNER-OUTPUT-PATH-KEY))
  #(input-path output-path))

(defp p-run
  {:post [(: % str)]}
  (<- #(input-path output-path) (get-exchange-paths))
  (<- result (run-from-file input-path output-path default-serializer))
  result)


(defn runner-interpreter [program * [env None]]  ; doeff: interpreter
  "Minimal interpreter for the runner program."
  (setv resolved-env (resolve-runner-env env))
  (run (scheduled
    (with-handlers [(reader :env resolved-env) (slog-handler) (writer)]
      program))))
