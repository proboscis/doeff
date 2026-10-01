;;; 実行環境の事実(ReadRuntimeFacts)の契約テストの解釈器(composition root)— 同じ契約の Program を、事実の答え手だけ替えて走らせる。
;;;
;;;   process-runtime-facts  本物: process-runtime-facts(汎用の効果への言い換え)+ 本物の汎用の答え手 subprocess-handler・os-file-handler
;;;                          (この検の process の環境変数・sys.prefix・印の file・module の置き場・pid を読む — #2344)
;;;   given-runtime-facts    fake: given-runtime-facts(渡した ProcessFacts で答える)
;;;
;;; 契約の世界(FactsWorld)は両方とも同じ make-world が走るたびに新しい一時 dir に作る: 宣言の root(完成の印・宣言の repo の dir の下に
;;; 確かめる package PROBE と __init__ を持たない package NAMESPACE・project の venv の形)・前の commit の印を持つ root(同じ形)・印の無い venv。
;;; 場面(Scene)は検の effect EnterScene で替える(既定 = DECLARED):
;;;   DECLARED    宣言とキーを渡され、宣言の root の venv で動く
;;;   UNDECLARED  宣言もキーも渡されず、宣言の root の venv で動く
;;;   UNMARKED    宣言とキーを渡され、印の無い venv で動く
;;;   STALE       宣言とキーを渡され、前の commit の印を持つ root の venv で動く
;;; 本物は場面を検の process に置く(環境変数と sys.prefix。宣言の root の repo の dir は sys.path の先頭)— 走る間だけ置き、走った後に
;;; 前の値へ戻す。fake には同じ場面の同じ事実を渡す: 置き場は検の process の module の属性(__file__・__path__)から、事実の読み手の関数を
;;; 借りずに作る(食い違いが出るのは答えの形の違いだけにする)。pid は両方ともこの検の process の id。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import importlib)
(import json)
(import os)
(import sys)
(import tempfile)
(import pathlib [Path])
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_cluster)
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout PythonProject])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json env-key])
(import doeff_cluster.worker.core.env_prepare [env-marker->json] doeff_cluster.worker.intent.env_prepare_model [EnvMarker] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.shared.intent.runtime_identity_model [ModuleOrigin ProcessFacts ReadRuntimeFacts])
(import doeff_cluster.shared.protocol.runtime_facts [given-runtime-facts process-runtime-facts])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])

(val PROCESS-RUNTIME-FACTS "process-runtime-facts")
(val GIVEN-RUNTIME-FACTS "given-runtime-facts")
(val PLATFORM "linux-x86_64")
(val PROBE "facts_contract_probe")
(val NAMESPACE "facts_contract_ns")
(val MISSING-MODULE "facts_contract_missing")
(val DECLARED-ENV "DOEFF_RUNTIME_ENV")
(val KEY-ENV "DOEFF_RUNTIME_ENV_KEY")


(defk declared-env [app-commit]
  {:pre [(: app-commit str)] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "契約の宣言(repo 2 つ・project は app の根)。app-commit だけ替えると別のキーの root になる。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.com/app.git" :commit app-commit)
                       (RepoCheckout :name "doeff" :url "https://example.com/doeff.git" :commit (* "d" 40)))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(defenum Scene
  (DECLARED "declared")
  (UNDECLARED "undeclared")
  (UNMARKED "unmarked")
  (STALE "stale"))


(defrecord FactsWorld
  "契約の世界。root / stale-root = 印を持つ root の絶対 path(stale-root は前の commit の印)・plain-venv = 印の無い venv・
   declared = 宣言・declared-json = worker が DOEFF_RUNTIME_ENV に置く宣言の文字列・key = 宣言のキー・marker-json / stale-marker-json =
   印の file の中身・stale-key = 前の commit のキー。"
  (#^ str root)
  (#^ str stale-root)
  (#^ str plain-venv)
  (#^ RuntimeEnv declared)
  (#^ str declared-json)
  (#^ str key)
  (#^ str marker-json)
  (#^ str stale-marker-json)
  (#^ str stale-key))


(defclass [(dataclass :frozen True)] EnterScene [EffectBase]
  "場面を替える(検の effect)。"
  (#^ Scene scene))


(defclass [(dataclass :frozen True)] WorldSeen [EffectBase]
  "契約の世界(FactsWorld)を読む(検の effect — 期待の値を世界から作る口)。")


(defk marker-text [env key]
  {:pre [(: env RuntimeEnv) (: key str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "準備が root に置くのと同じ完成の印の中身(本物の書き手 env-marker->json で作る — 形式を手書きしない)。"
  (<- raw dict (env-marker->json (EnvMarker :env env :key key :platform PLATFORM :stages #() :downloaded 0 :built 0
                                            :interpreter "/usr/bin/python3" :child-protocol 1)))
  (json.dumps raw))


(defk make-world [directory]
  {:pre [(: directory str)] :post [(: % FactsWorld)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "directory の下に契約の世界を作る(両方の解釈器が同じ形で呼ぶ)。"
  (val base (.resolve (Path directory)))
  (<- declared RuntimeEnv (declared-env (* "a" 40)))
  (<- stale RuntimeEnv (declared-env (* "b" 40)))
  (<- declared-raw dict (runtime-env->json declared))
  (<- key str (env-key declared PLATFORM))
  (<- stale-key str (env-key stale PLATFORM))
  (<- marker str (marker-text declared key))
  (<- stale-marker str (marker-text stale stale-key))
  (val root (/ base "envs" key))
  (val stale-root (/ base "envs" stale-key))
  (for [#(at text) #(#(root marker) #(stale-root stale-marker))]
    (.mkdir (/ at "app" PROBE) :parents True)
    (.write-text (/ at "app" PROBE "__init__.py") "")
    (.mkdir (/ at "app" ".venv"))
    (.write-text (/ at ENV-MARKER) text :encoding "utf-8"))
  (.mkdir (/ root "app" NAMESPACE))
  (.write-text (/ root "app" NAMESPACE "leaf.py") "")
  (.mkdir (/ base "plain" ".venv") :parents True)
  (FactsWorld :root (str root) :stale-root (str stale-root) :plain-venv (str (/ base "plain" ".venv")) :declared declared
              :declared-json (json.dumps declared-raw) :key key :marker-json marker :stale-marker-json stale-marker
              :stale-key stale-key))


(defk given-origins [world]
  {:pre [(: world FactsWorld)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "fake に渡す module の置き場(世界の repo の dir と検の process の module の属性から — 事実の読み手の関数を借りない)。
   __init__ を持たない package は dir の path に / を付ける。MISSING-MODULE は渡さない(import できない)。"
  (val app (+ world.root "/app/"))
  #((ModuleOrigin :module PROBE :file (+ app PROBE "/__init__.py"))
    (ModuleOrigin :module NAMESPACE :file (+ app NAMESPACE "/"))
    (ModuleOrigin :module "json" :file (str (.resolve (Path json.__file__))))
    (ModuleOrigin :module "doeff_cluster" :file (+ (str (.resolve (Path (get (list doeff_cluster.__path__) 0)))) "/"))))


(defk scene-facts [world scene]
  {:pre [(: world FactsWorld) (: scene Scene)] :post [(: % ProcessFacts)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "場面の事実(fake に渡す物)。"
  (<- origins tuple (given-origins world))
  (val pid (os.getpid))
  (match scene
    Scene.DECLARED (ProcessFacts :declared-json world.declared-json :key world.key :root world.root :marker-json world.marker-json
                                 :origins origins :pid pid)
    Scene.UNDECLARED (ProcessFacts :declared-json "" :key "" :root world.root :marker-json world.marker-json :origins origins :pid pid)
    Scene.UNMARKED (ProcessFacts :declared-json world.declared-json :key world.key :root "" :marker-json "" :origins origins :pid pid)
    Scene.STALE (ProcessFacts :declared-json world.declared-json :key world.key :root world.stale-root
                              :marker-json world.stale-marker-json :origins origins :pid pid)))


(defk scene-prefix [world scene]
  {:pre [(: world FactsWorld) (: scene Scene)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "場面の sys.prefix(本物が印を探し始める venv)。"
  (match scene
    Scene.DECLARED (+ world.root "/app/.venv")
    Scene.UNDECLARED (+ world.root "/app/.venv")
    Scene.UNMARKED world.plain-venv
    Scene.STALE (+ world.stale-root "/app/.venv")))


(defk place-scene [world scene]
  {:pre [(: world FactsWorld) (: scene Scene)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "場面を検の process に置く(環境変数の宣言とキー・sys.prefix)。"
  (match scene
    Scene.UNDECLARED (do (.pop os.environ DECLARED-ENV None) (.pop os.environ KEY-ENV None))
    _ (.update os.environ {DECLARED-ENV world.declared-json KEY-ENV world.key}))
  (<- prefix str (scene-prefix world scene))
  (setattr sys "prefix" prefix)
  None)


(defk restore-environment [saved]
  {:pre [(: saved dict)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "os.environ の名を saved の値(None = 無かった)へ戻す。"
  (for [#(name value) (.items saved)]
    (match value
      None (.pop os.environ name None)
      _ (.update os.environ {name value})))
  None)


(defhandler process-scene [#^ FactsWorld world]
  ;; 引数に残す理由: 世界は走るたびに作る一時 dir(組み立てが作って本物の場面の置き手に渡す — Ask で運ぶ設定ではない)。
  ;; 本物の側: 場面を検の process に置く。ReadRuntimeFacts は内側の process-runtime-facts が答える。
  (EnterScene [scene]
    (<- (place-scene world scene))
    (resume None))
  (WorldSeen []
    (resume world)))


(defhandler given-scene [#^ FactsWorld world]
  ;; 引数に残す理由: 世界は走るたびに作る一時 dir(組み立てが作って fake の場面の持ち手に渡す — Ask で運ぶ設定ではない)。
  ;; fake の側: 今の場面の事実を given-runtime-facts に渡して答えさせる(本物と同じ場面・同じ事実)。
  (session var current Scene.DECLARED)
  (EnterScene [scene]
    (:= current scene)
    (resume None))
  (WorldSeen []
    (resume world))
  (ReadRuntimeFacts [modules]
    (<- facts ProcessFacts (scene-facts world current))
    (<- answer ProcessFacts (with_handlers [(given-runtime-facts facts)] (ReadRuntimeFacts modules)))
    (resume answer)))


(defk under-process-runtime-facts [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の process-runtime-facts の下で program を走らせる。世界は新しい一時 dir、場面(環境変数・sys.prefix)と宣言の root の repo の dir
   (sys.path の先頭)は走る間だけ検の process に置き、走った後に前の値へ戻す。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (<- world FactsWorld (make-world directory))
    (val saved (dfor name #(DECLARED-ENV KEY-ENV) name (.get os.environ name)))
    (val saved-prefix sys.prefix)
    (val app-dir (+ world.root "/app"))
    (.insert sys.path 0 app-dir)
    (importlib.invalidate-caches)
    (try
      (<- (place-scene world Scene.DECLARED))
      (<- ran (with_handlers [(process-scene world) subprocess-handler os-file-handler process-runtime-facts] program))
      (:= answer ran)
      (finally
        (<- (restore-environment saved))
        (setattr sys "prefix" saved-prefix)
        (.remove sys.path app-dir))))
  answer)


(defk under-given-runtime-facts [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "fake の given-runtime-facts の下で program を走らせる。世界は本物と同じ形の新しい一時 dir(fake は読まない)。外から順に: state・場面の持ち手。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (<- world FactsWorld (make-world directory))
    (<- ran (with_handlers [(state) (given-scene world)] program))
    (:= answer ran))
  answer)


(val INTERPRETERS {PROCESS-RUNTIME-FACTS under-process-runtime-facts
                   GIVEN-RUNTIME-FACTS under-given-runtime-facts})
