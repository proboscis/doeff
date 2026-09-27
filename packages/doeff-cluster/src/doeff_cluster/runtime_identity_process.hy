;;; 入口の検め(runtime_identity.hy)の ReadRuntimeFacts に、この process を読んで答える handler — 環境変数・sys.prefix・root の完成の印・
;;; module の置き場・pid を読み、文字列のまま答える(宣言の型への読み戻しと判断は runtime_identity.hy の Program の側)。
(require doeff-hy.macros [defhandler])
(import importlib.util)
(import os)
(import pathlib [Path])
(import sys)
(import doeff_cluster.env_prepare [ENV-MARKER])
(import doeff_cluster.runtime_identity [ModuleOrigin ProcessFacts ReadRuntimeFacts])


(defn _marked-root [#^ Path start]  ; defk にできない: handler の中の file system の走査
  "start から上へ辿って、完成の印を持つ最初の dir(無ければ None)— この process の venv を置いた root を見つけるため。"
  (for [d (+ [start] (list start.parents))]
    (when (.is-file (/ d ENV-MARKER)) (return d)))
  None)


(defn _origin [#^ str module]  ; defk にできない: handler の中の import の解決
  "module を import した先の path(解けなければ空)— どの木の code を動かしているかを確かめるため。__init__ を持たない package
   (origin が無い)は、import が submodule を探す最初の dir を置き場とする(origin だけを見て『import できない』と誤って断らない)。"
  (setv spec (try (importlib.util.find-spec module) (except [Exception] None)))
  (setv locations (if spec (list (or spec.submodule-search-locations [])) []))
  (setv found (cond
                (and spec spec.origin (not-in spec.origin #("built-in" "frozen"))) spec.origin
                (and spec (is spec.origin None) locations) (+ (str (.resolve (Path (get locations 0)))) "/")
                True ""))
  (ModuleOrigin :module module :file (if (and found (not (.endswith found "/"))) (str (.resolve (Path found))) found)))


(defn _read-facts [#^ tuple modules]  ; defk にできない: handler の中の I/O(環境変数・印の file)
  "この process の検めの材料を読む(文字列のまま)。"
  (setv root (_marked-root (.resolve (Path sys.prefix))))
  (ProcessFacts :declared-json (os.environ.get "DOEFF_RUNTIME_ENV" "")
                :key (os.environ.get "DOEFF_RUNTIME_ENV_KEY" "")
                :root (if root (str root) "")
                :marker-json (if root (.read-text (/ root ENV-MARKER) :encoding "utf-8") "")
                :origins (tuple (gfor m modules (_origin m)))
                :pid (os.getpid)))


(defhandler process-runtime-facts
  ;; この process を読んで検めの材料に答える。
  (ReadRuntimeFacts [modules]
    (resume (_read-facts modules))))
