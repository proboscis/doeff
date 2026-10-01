;;; 入口の検めの材料(ReadRuntimeFacts)に答える handler 2 つ(runtime_identity から分けた・#2344)。
;;;
;;;   process-runtime-facts  この process を読んで答える。ReadRuntimeFacts を doeff の汎用の効果へ言い換えるだけで、I/O を持たない:
;;;                            環境変数 DOEFF_RUNTIME_ENV・DOEFF_RUNTIME_ENV_KEY = ReadEnvironment・interpreter の prefix と pid = ReadInterpreter・
;;;                            完成の印を持つ root = prefix から上へ辿って StatPath(file か)・印の中身 = ReadText・module の置き場 = ResolveModule。
;;;                            答えるのは外側の汎用の答え手(本物 = doeff_core_effects の subprocess-handler と os-file-handler・模擬 =
;;;                            scripted-process-handler と memory-file-handler)。以前は runtime_identity_process.hy が直に os・sys・importlib を
;;;                            読んでいた(#2344 の後半・汎用の効果 = #2347)。
;;;   given-runtime-facts    渡した材料で答える(検と模擬)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import posixpath)
(import doeff_core_effects.process_effects [ReadEnvironment ReadInterpreter ResolveModule InterpreterFacts ModuleFound ModuleNotFound])
(import doeff_core_effects.file_effects [StatPath ReadText PathKind PathStat FileFailed])
(import doeff_cluster.env_prepare [ENV-MARKER])
(import doeff_cluster.shared.intent.runtime_identity_model [ModuleOrigin ProcessFacts ReadRuntimeFacts])

;; 読む環境変数(宣言と、渡されたキー)。
(val DECLARED-ENV "DOEFF_RUNTIME_ENV")
(val KEY-ENV "DOEFF_RUNTIME_ENV_KEY")


(defk marked-root [start]
  {:pre [(: start str)] :post [(: % (| str None))]}
  "start(interpreter の prefix)から上へ辿って、完成の印を持つ最初の dir(無ければ None)を見つけるため — この process の venv を置いた root。"
  (val parent (posixpath.dirname start))
  (<- seen PathStat (StatPath (posixpath.join start ENV-MARKER)))
  (cond
    (= seen.kind PathKind.FILE) start
    (= parent start) None
    True (do (<- above (| str None) (marked-root parent))
             above)))


(defk module-origin [name]
  {:pre [(: name str)] :post [(: % ModuleOrigin)]}
  "module を import が解く置き場(解けなければ空)にするため — どの木の code を動かしているかを確かめる。__init__ を持たない package
   (file を持たない)は、import が submodule を探す最初の dir に / を付けた物を置き場とする(file だけを見て『import できない』と誤って断らない)。"
  (<- found (| ModuleFound ModuleNotFound) (ResolveModule name))
  (ModuleOrigin :module name
                :file (match found
                        (ModuleFound :origin origin) :if (is-not origin None) origin
                        (ModuleFound :search-locations locations) :if locations (+ (get locations 0) "/")
                        _ "")))


(defk env-value [env name]
  {:pre [(: env tuple) (: name str)] :post [(: % str)]}
  "ReadEnvironment の答え env から名 name の値を引くため(無ければ空 — 渡されていない)。"
  (next (gfor e env :if (= e.name name) e.value) ""))


(defk marker-absent []
  {:pre [] :post [(: % str)]}
  "完成の印を持つ root が無い時の印の中身(空)を、印を読んだ時と同じく Program の答えとして返すため。"
  "")


(defk marker-text [root]
  {:pre [(: root str)] :post [(: % str)]}
  "root の完成の印の中身を読むため(在る file が読めなければ例外 — 以前の直の read-text と同じく黙って続けない)。"
  (<- text (| str FileFailed) (ReadText (posixpath.join root ENV-MARKER)))
  (when (isinstance text FileFailed)
    (raise (RuntimeError (.format "完成の印 {} を読めない: {}" text.path text.detail))))
  text)


(defk process-facts [modules]
  {:pre [(: modules tuple)] :post [(: % ProcessFacts)]}
  "この process の検めの材料を、汎用の効果で読んで文字列のまま組むため(宣言の型への読み戻しと判断は core の Program の側)。"
  (<- env tuple (ReadEnvironment #(DECLARED-ENV KEY-ENV)))
  (<- interpreter InterpreterFacts (ReadInterpreter))
  (<- root (| str None) (marked-root interpreter.prefix))
  (<- marker str (if (is root None) (marker-absent) (marker-text root)))
  (var origins #())
  (for [m modules]
    (<- origin ModuleOrigin (module-origin m))
    (:= origins (+ origins #(origin))))
  (<- declared str (env-value env DECLARED-ENV))
  (<- key str (env-value env KEY-ENV))
  (ProcessFacts :declared-json declared
                :key key
                :root (or root "")
                :marker-json marker
                :origins origins
                :pid interpreter.pid))


(defhandler process-runtime-facts
  ;; この process を読んで検めの材料に答える(頭の註 — 汎用の効果へ言い換えるだけ)。
  (ReadRuntimeFacts [modules]
    (<- facts ProcessFacts (process-facts modules))
    (resume facts)))


(defhandler given-runtime-facts [#^ ProcessFacts facts]  ;; 引数に残す理由: 検と模擬が渡す材料そのもの(Ask で読む設定ではない)
  "渡した材料 facts(ProcessFacts)で ReadRuntimeFacts に答える handler — 問われた module だけを、渡した置き場から答える
   (無い module は import できない物)。"
  (ReadRuntimeFacts [modules]
    (val by-name (dfor o facts.origins o.module o))
    (resume (ProcessFacts :declared-json facts.declared-json :key facts.key :root facts.root :marker-json facts.marker-json
                          :origins (tuple (gfor m modules (.get by-name m (ModuleOrigin :module m :file ""))))
                          :pid facts.pid))))
