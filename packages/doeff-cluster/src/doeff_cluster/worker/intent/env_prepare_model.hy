;;; 実行環境の root の準備(env_prepare)の型・effect・定数 — 準備の Program(worker/core/env_prepare)が出し、worker の準備の係が答える(#2025 の 3 本目で分けた)。
(require doeff-hy.macros [defk defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass])
(import enum [StrEnum])  ; defenum の展開が使う
(import doeff [EffectBase])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure])


(val ROOTS-PTH "_doeff_cluster_roots.pth")


;; --- 要求と答え ---------------------------------------------------------------------------

(defrecord KnownRoot
  "worker が既に完成させた root(展開の複製と bytecode の引き継ぎの元の候補)。"
  (#^ RuntimeEnv env)
  (#^ str root))


(defrecord PrepareRequest
  "root 1 つの準備の要求。root = 最終の path(tmp から rename しない — venv が絶対 path を持つので)・known = 完成済みの root・
   min-free-bytes = 準備を始めてよい空きの下限。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str platform)
  (#^ str root)
  (setv #^ tuple known #())
  (setv #^ int min-free-bytes 0))


(defrecord StageTime
  "処理ステージ 1 つの経過の秒(計器とマーカーに載せる)。"
  (#^ str name)
  (#^ float seconds))


(defrecord MirrorReady
  "repo の bare mirror の path。"
  (#^ str path))


;; commit を mirror に揃えた結果。
(defenum FetchState PRESENT FETCHED MISSING)


(defrecord RepoMirror
  "宣言の repo 1 つと、その bare mirror の path。"
  (#^ str name)
  (#^ str mirror))


(defrecord EnvMarker
  "完成マーカーの中身: 宣言・キー・処理ステージの秒・bytecode を作った interpreter・子の約束の版。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str platform)
  (#^ tuple stages)
  (#^ int downloaded)
  (#^ int built)
  (#^ str interpreter)
  (#^ int child-protocol))


(defrecord WheelReady
  "native の wheel の path。built = この準備で build した(キーの wheel が無かった)。"
  (#^ str path)
  (#^ bool built))


(defrecord SyncReport
  "依存を入れた結果。downloaded = cache に無く取りに行った package の数。"
  (#^ int downloaded))


(defrecord BytecodeReport
  "bytecode を作った結果。interpreter = 作った interpreter の path(root の venv の物であることを完成マーカーで読む)。"
  (#^ str interpreter)
  (#^ int compiled)
  (#^ int carried))


(defrecord ProbeReport
  "子と同じ起こし方で読んだ root の姿。child-protocol = root の中の子の入口の約束の版・
   misplaced = 根の最上位の名のうち、根の外(第三者の package 等)に解けた物。"
  (#^ int child-protocol)
  (#^ tuple misplaced))


(defrecord EnvReady
  "準備の済んだ root。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str root)
  (#^ tuple stages)
  (#^ int downloaded)
  (#^ int built)
  (#^ str interpreter))


(defrecord PrepareState
  "処理ステージの間で引き継ぐ途中の結果。"
  (setv #^ tuple mirrors #())
  (setv #^ tuple wheels #())
  (setv #^ int downloaded 0)
  (setv #^ int built 0)
  (setv #^ str interpreter "")
  (setv #^ tuple stages #()))


;; --- effect ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] StageStarted [EffectBase]
  "処理ステージ name を始めた(進みの印 — 準備を頼んだ worker が停滞を見分けるため)。答え = None。"
  (#^ str name))


(defclass [(dataclass :frozen True)] PrepareNote [EffectBase]
  "準備を止めない所見を 1 行記録する(例: editable で入るだけの依存の repo の bytecode を焼けなかった — 2026-09-27)。答え = None。"
  (#^ str text))


(defclass [(dataclass :frozen True)] DiskFree [EffectBase]
  "path を含む volume の空き(byte)。答え = int。"
  (#^ str path))


(defeffect RepoAllowed
  "url が名指す repo が worker の許可表(clone してよい URL)に在るか(綴りが違っても同じ repo なら在る — 取りに行く綴りは表の側)。
   答え = bool。断る判断(repo-denied)は prepare-env が持つ。"
  {:fields [(: url str)]
   :answer bool
   :tags {:context "runtime-env" :role "intent"}})


(defclass [(dataclass :frozen True)] EnsureMirror [EffectBase]
  "url の bare mirror を用意する(無ければ clone・url ごとに排他)。答え = MirrorReady か EnvFailure(repo-unreachable)。"
  (#^ str url))


(defclass [(dataclass :frozen True)] FetchCommit [EffectBase]
  "commit を mirror に揃える(在れば何もしない・無ければ fetch)。答え = FetchState か EnvFailure(repo-unreachable)。"
  (#^ str mirror)
  (#^ str commit))


(defclass [(dataclass :frozen True)] MaterializeTree [EffectBase]
  "commit のツリーを dest に置く。reuse = 同じ commit のツリーを持つ別の root の dir(複製する・.venv・完成マーカー・__pycache__ を除く)
   か None(mirror から展開する)。答え = None。"
  (#^ str mirror)
  (#^ str commit)
  (#^ str dest)
  (#^ (| str None) reuse))




(defclass [(dataclass :frozen True)] TreeHash [EffectBase]
  "commit の中の dir の git の tree hash。答え = str。"
  (#^ str mirror)
  (#^ str commit)
  (#^ str path))


(defclass [(dataclass :frozen True)] EnsureNativeWheel [EffectBase]
  "キーの native の wheel を用意する(無ければ source-dir — 宣言の paths の先頭の dir — から build・キーごとに排他)。
   答え = WheelReady か EnvFailure(native-build-failed)。"
  (#^ str key)
  (#^ str package)
  (#^ str source-dir))


(defclass [(dataclass :frozen True)] SyncProject [EffectBase]
  "project の依存を lock どおりに venv へ入れる(no-install = wheel で後から入れる package)。
   答え = SyncReport か EnvFailure(lock-stale・sync-failed・python-unavailable)。"
  (#^ str project-dir)
  (#^ str python)
  (#^ tuple groups)
  (#^ tuple no-install))


(defclass [(dataclass :frozen True)] InstallWheels [EffectBase]
  "wheel を依存なしで venv へ入れる。答え = None か EnvFailure(sync-failed)。"
  (#^ str project-dir)
  (#^ tuple wheels))


(defclass [(dataclass :frozen True)] WriteImportRoots [EffectBase]
  "venv の site-packages に import の根の .pth を置く(roots = 絶対 path・宣言の順)。答え = None。"
  (#^ str project-dir)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] ReadEditableRoots [EffectBase]
  "project の venv に editable で入る package の dir のうち、root の中に在る物(2026-09-27)。答え = root からの相対 path
   \"<repo の名>/<repo の中の相対の dir>\"(repo の根そのものは \"<repo の名>\")の tuple(venv の .pth の名の順・import の根の .pth は除く)。"
  (#^ str project-dir)
  (#^ str root))


(defclass [(dataclass :frozen True)] CompileTree [EffectBase]
  "tree の bytecode を root の venv の interpreter で作る(project-dir = その venv の project・roots = tree の中の import の根・
   carry-from = 引き継ぎ元の同じ repo のツリーか None・entries = 焼く範囲の入口の module(空 = 根の下を全部 — 宣言の
   bytecode-entries))。答え = BytecodeReport か EnvFailure。"
  (#^ str project-dir)
  (#^ str tree)
  (#^ tuple roots)
  (#^ (| str None) carry-from)
  (setv #^ tuple entries #()))


(defclass [(dataclass :frozen True)] ProbeImports [EffectBase]
  "子と同じ起こし方で root を読む(roots = 絶対 path)。答え = ProbeReport か EnvFailure(env-incompatible)。"
  (#^ str project-dir)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] WriteEnvMarker [EffectBase]
  "完成マーカーを root に置く(JSON にして別の file へ書いて置き換える)。答え = None。"
  (#^ str root)
  (#^ EnvMarker marker))


;; --- 純粋な判断 ---------------------------------------------------------------------------
