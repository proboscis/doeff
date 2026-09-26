;;; 実行環境(root)の準備の Program と、その effect(2026-09-26)。
;;;
;;; worker は宣言(runtime_env_model の RuntimeEnv)を受けると、env のキーの root をこの Program で準備し、完成マーカーを置いた
;;; root の中の子 process で task を走らせる。worker の process は変わらない(新しい commit・lock・native は新しい root を作るだけ)。
;;; I/O は全部 effect で、本物の handler は env_handlers.hy の local-env、速い模擬の handler は env_fake.hy の fake-env。
;;;
;;; 処理ステージ(失敗はその場で EnvFailure を値で返し、後の処理ステージを走らせない — どれも子 process を起こす前):
;;;   1 空き      DiskFree                                   空きが下限を切れば disk-full
;;;   2 mirror    EnsureMirror / FetchCommit                 repo-denied・repo-unreachable・commit-missing
;;;   3 展開      MaterializeTree                            同じ commit のツリーを持つ別の root があれば複製(.venv・マーカー・__pycache__ を除く)
;;;   4 lock      FileSha256                                 展開した uv.lock が宣言の sha256 と違えば lock-mismatch
;;;   5 native    TreeHash / EnsureNativeWheel               キーの wheel が無ければ build(native-build-failed)
;;;   6 依存      SyncProject                                uv sync --locked(lock-stale・sync-failed・python-unavailable)
;;;   7 wheel     InstallWheels                              native の wheel を入れる
;;;   8 根        WriteImportRoots                           venv に import の根の .pth を置く(宣言の順)
;;;   9 bytecode  CompileTree                                root の venv の interpreter で作る(引き継ぎ元は lock と Python が同じ root)
;;;  10 確かめ    ProbeImports                               子の約束の版・根の最上位の名の解け先(env-incompatible)
;;;  11 完成      WriteEnvMarker                             完成マーカーを最後に置く(無い root は使わない)
;;;
;;; 時計は doeff-time の GetMonotonic(各処理ステージの秒をマーカーと答えに載せる)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defenum defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import enum [StrEnum])
(import doeff [EffectBase])
(import doeff_time [GetMonotonic])
(import .runtime_env_model [RuntimeEnv RepoCheckout NativeWheel EnvFailure EnvFailureKind env-failure native-key root-split
                            runtime-env->json CHILD-PROTOCOL SUPPORTED-CHILD-PROTOCOLS])

(val ENV-MARKER ".doeff-env-ready.json")
(val ENV-MARKER-FORMAT 1)
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

(defclass [(dataclass :frozen True)] DiskFree [EffectBase]
  "path を含む volume の空き(byte)。答え = int。"
  (#^ str path))


(defclass [(dataclass :frozen True)] EnsureMirror [EffectBase]
  "url の bare mirror を用意する(無ければ clone・url ごとに排他)。答え = MirrorReady か EnvFailure(repo-denied・repo-unreachable)。"
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


(defclass [(dataclass :frozen True)] FileSha256 [EffectBase]
  "file の中身の sha256(16 進)。答え = str か None(file が無い)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] TreeHash [EffectBase]
  "commit の中の dir の git の tree hash。答え = str。"
  (#^ str mirror)
  (#^ str commit)
  (#^ str path))


(defclass [(dataclass :frozen True)] EnsureNativeWheel [EffectBase]
  "キーの native の wheel を用意する(無ければ project-dir の workspace で build・キーごとに排他)。
   答え = WheelReady か EnvFailure(native-build-failed)。"
  (#^ str key)
  (#^ str package)
  (#^ str project-dir))


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


(defclass [(dataclass :frozen True)] CompileTree [EffectBase]
  "tree の bytecode を root の venv の interpreter で作る(project-dir = その venv の project・roots = tree の中の import の根・
   carry-from = 引き継ぎ元の同じ repo のツリーか None)。答え = BytecodeReport か EnvFailure。"
  (#^ str project-dir)
  (#^ str tree)
  (#^ tuple roots)
  (#^ (| str None) carry-from))


(defclass [(dataclass :frozen True)] ProbeImports [EffectBase]
  "子と同じ起こし方で root を読む(roots = 絶対 path)。答え = ProbeReport か EnvFailure(env-incompatible)。"
  (#^ str project-dir)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] WriteEnvMarker [EffectBase]
  "完成マーカーを root に置く(JSON にして別の file へ書いて置き換える)。答え = None。"
  (#^ str root)
  (#^ EnvMarker marker))


;; --- 純粋な判断 ---------------------------------------------------------------------------

(defk project-dir [env root]
  {:pre [(: env RuntimeEnv) (: root str)] :post [(: % str)]}
  "venv を持つ project の dir(uv の --project に渡す path)。"
  (if (= env.project.path ".")
      (.format "{}/{}" root env.project.repo)
      (.format "{}/{}/{}" root env.project.repo env.project.path)))


(defk absolute-roots [env root]
  {:pre [(: env RuntimeEnv) (: root str)] :post [(: % tuple)]}
  "import の根の絶対 path(宣言の順)— .pth に並べる値。"
  (var out [])
  (for [r env.import-roots]
    (<- parts tuple (root-split r))
    (.append out (if (= (get parts 1) ".")
                     (.format "{}/{}" root (get parts 0))
                     (.format "{}/{}/{}" root (get parts 0) (get parts 1)))))
  (tuple out))


(defk repo-roots [env name]
  {:pre [(: env RuntimeEnv) (: name str)] :post [(: % tuple)]}
  "repo 1 つの中の import の根(repo の中の相対の dir・宣言の順)。bytecode を作る範囲を決めるため。"
  (var out [])
  (for [r env.import-roots]
    (<- parts tuple (root-split r))
    (when (= (get parts 0) name) (.append out (get parts 1))))
  (tuple out))


(defk reuse-tree [known repo]
  {:pre [(: known tuple) (: repo RepoCheckout)] :post [(: % (| str None))]}
  "同じ url と commit のツリーを持つ完成済みの root の dir(展開を複製で済ませるため)。無ければ None。"
  (var found None)
  (for [k known]
    (for [r k.env.repos]
      (when (and (is found None) (= r.url repo.url) (= r.commit repo.commit))
        (:= found (.format "{}/{}" k.root r.name)))))
  found)


(defk carry-source [known env name]
  {:pre [(: known tuple) (: env RuntimeEnv) (: name str)] :post [(: % (| str None))]}
  "bytecode の引き継ぎ元 = lock の sha256 と Python が同じ完成済みの root の、同じ url の repo のツリー(Hy の macro の展開が
   同じ Hy と doeff-hy で固定される組に限るため)。無ければ None。"
  (val url (next (gfor r env.repos :if (= r.name name) r.url)))
  (var found None)
  (for [k known]
    (when (and (is found None)
               (= k.env.project.lock-sha256 env.project.lock-sha256)
               (= k.env.project.python env.project.python))
      (for [r k.env.repos]
        (when (and (is found None) (= r.url url))
          (:= found (.format "{}/{}" k.root r.name))))))
  found)


(defk env-marker->json [marker]
  {:pre [(: marker EnvMarker)] :post [(: % dict)]}
  "完成マーカーを file に書く JSON の値にする(file の境界の 1 か所)。"
  (<- declared dict (runtime-env->json marker.env))
  {"format" ENV-MARKER-FORMAT "key" marker.key "platform" marker.platform "env" declared
   "stages" (lfor s marker.stages {"name" s.name "seconds" (round s.seconds 3)})
   "downloaded" marker.downloaded "built" marker.built
   "interpreter" marker.interpreter "childProtocol" marker.child-protocol})


;; --- 処理ステージ ---------------------------------------------------------------------------
;; どれも (request state) → 次の state か EnvFailure。

(defk stage-disk [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "空きが下限を切っていれば準備を始めない(途中の ENOSPC で壊れた root を作らないため)。"
  (<- free int (DiskFree request.root))
  (if (< free request.min-free-bytes)
      (do (<- failure EnvFailure (env-failure EnvFailureKind.DISK-FULL
                                              (.format "空き {} byte が下限 {} byte を切る" free request.min-free-bytes)))
          failure)
      state))


(defk stage-mirrors [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "宣言した repo ごとに mirror を用意して commit を揃える(worker が取れない commit はここで断る)。"
  (var mirrors [])
  (var failure None)
  (for [repo request.env.repos]
    (when (is failure None)
      (<- ready (| MirrorReady EnvFailure) (EnsureMirror repo.url))
      (match ready
        (EnvFailure) (:= failure ready)
        (MirrorReady :path path)
        (do (<- fetched (| FetchState EnvFailure) (FetchCommit path repo.commit))
            (match fetched
              (EnvFailure) (:= failure fetched)
              FetchState.MISSING
              (do (<- missing EnvFailure
                      (env-failure EnvFailureKind.COMMIT-MISSING
                                   (.format "repo {} の commit {} が remote に無い(push していない?)" repo.name repo.commit)))
                  (:= failure missing))
              _ (.append mirrors (RepoMirror :name repo.name :mirror path)))))))
  (if (is failure None) (replace state :mirrors (tuple mirrors)) failure))


(defk stage-trees [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % PrepareState)]}
  "repo ごとのツリーを root の下に兄弟で並べる(同じ commit のツリーが既にあれば複製で済ませる)。"
  (val mirrors (dfor m state.mirrors m.name m.mirror))
  (for [repo request.env.repos]
    (<- reuse (reuse-tree request.known repo))
    (<- (MaterializeTree (get mirrors repo.name) repo.commit (.format "{}/{}" request.root repo.name) reuse)))
  state)


(defk stage-lock [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "展開した uv.lock が宣言の sha256 と同じかを確かめる(宣言の project の path の誤りを展開の直後に捕まえるため)。"
  (<- pdir str (project-dir request.env request.root))
  (<- found (| str None) (FileSha256 (.format "{}/uv.lock" pdir)))
  (if (= found request.env.project.lock-sha256)
      state
      (do (<- failure EnvFailure
              (env-failure EnvFailureKind.LOCK-MISMATCH
                           (.format "{}/uv.lock の sha256 が宣言と違う(宣言 {} ・展開 {})" pdir request.env.project.lock-sha256 found)))
          failure)))


(defk stage-native [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "native の package を、source の tree hash をキーにした wheel で用意する(source が同じなら build し直さないため)。"
  (val mirrors (dfor m state.mirrors m.name m.mirror))
  (val commits (dfor r request.env.repos r.name r.commit))
  (<- pdir str (project-dir request.env request.root))
  (var wheels [])
  (var built 0)
  (var failure None)
  (for [wheel request.env.project.native]
    (when (is failure None)
      (var hashes [])
      (for [path wheel.paths]
        (<- h str (TreeHash (get mirrors wheel.repo) (get commits wheel.repo) path))
        (.append hashes h))
      (<- key str (native-key wheel (tuple hashes) request.env.project.python request.platform))
      (<- ready (| WheelReady EnvFailure) (EnsureNativeWheel key wheel.package pdir))
      (match ready
        (EnvFailure) (:= failure ready)
        (WheelReady :path path :built b) (do (.append wheels path) (when b (:= built (+ built 1)))))))
  (if (is failure None) (replace state :wheels (tuple wheels) :built built) failure))


(defk stage-sync [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "依存を lock どおりに入れる(native は wheel で後から入れるので除く)。依存を入れるのはここだけ(子の起動は --no-sync)。"
  (<- pdir str (project-dir request.env request.root))
  (val project request.env.project)
  (<- report (| SyncReport EnvFailure)
      (SyncProject pdir project.python project.groups (tuple (gfor w project.native w.package))))
  (match report
    (EnvFailure) report
    (SyncReport :downloaded n) (replace state :downloaded n)))


(defk stage-wheels [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "native の wheel を venv へ入れる。"
  (<- pdir str (project-dir request.env request.root))
  (if (not state.wheels)
      state
      (do (<- done (InstallWheels pdir state.wheels))
          (if (isinstance done EnvFailure) done state))))


(defk stage-roots [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % PrepareState)]}
  "import の根を venv の .pth に宣言の順で並べる(子に PYTHONPATH を置かずに根を解くため)。"
  (<- pdir str (project-dir request.env request.root))
  (<- roots tuple (absolute-roots request.env request.root))
  (<- (WriteImportRoots pdir roots))
  state)


(defk stage-bytecode [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "import の根を持つ repo ごとに、root の venv の interpreter で bytecode を作る(worker の Hy で作ると macro の展開が違い得るため)。"
  (<- pdir str (project-dir request.env request.root))
  (var interpreter "")
  (var failure None)
  (for [repo request.env.repos]
    (<- roots tuple (repo-roots request.env repo.name))
    (when (and roots (is failure None))
      (<- carry (carry-source request.known request.env repo.name))
      (<- report (| BytecodeReport EnvFailure)
          (CompileTree pdir (.format "{}/{}" request.root repo.name) roots carry))
      (match report
        (EnvFailure) (:= failure report)
        (BytecodeReport :interpreter used) (:= interpreter used))))
  (if (is failure None) (replace state :interpreter interpreter) failure))


(defk stage-probe [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "子と同じ起こし方で root を読み、子の約束の版と根の名前の影を確かめる(cloudpickle の参照が送り手と同じ source に解けるため)。"
  (<- pdir str (project-dir request.env request.root))
  (<- roots tuple (absolute-roots request.env request.root))
  (<- report (| ProbeReport EnvFailure) (ProbeImports pdir roots))
  (match report
    (EnvFailure) report
    ;; 欄の名に - を含むので、型だけで受けて欄は属性で読む(match のキーワードの型は - の欄へ写らない)。
    (ProbeReport)
    (cond
      (not-in report.child-protocol SUPPORTED-CHILD-PROTOCOLS)
      (do (<- failure EnvFailure
              (env-failure EnvFailureKind.ENV-INCOMPATIBLE
                           (.format "root の子の入口の約束の版 {} を worker が扱えない(扱える版 = {})"
                                    report.child-protocol (sorted SUPPORTED-CHILD-PROTOCOLS))))
          failure)
      report.misplaced
      (do (<- failure EnvFailure
              (env-failure EnvFailureKind.ENV-INCOMPATIBLE
                           (.format "import の根の名が根の外に解ける(名前の影): {}" (.join " " report.misplaced))))
          failure)
      True state)))


(defrecord Stage
  "処理ステージ 1 つ: 計器とマーカーに載せる名と、(request state) → 次の state か EnvFailure の defk。"
  (#^ str name)
  (#^ Callable run))


(val STAGES #((Stage :name "disk" :run stage-disk) (Stage :name "mirror" :run stage-mirrors)
               (Stage :name "tree" :run stage-trees) (Stage :name "lock" :run stage-lock)
               (Stage :name "native" :run stage-native) (Stage :name "sync" :run stage-sync)
               (Stage :name "wheels" :run stage-wheels) (Stage :name "roots" :run stage-roots)
               (Stage :name "bytecode" :run stage-bytecode) (Stage :name "probe" :run stage-probe)))


;; --- Program -----------------------------------------------------------------------------

(defk prepare-env [request]
  {:pre [(: request PrepareRequest)] :post [(: % (| EnvReady EnvFailure))]}
  "宣言から root 1 つを準備する。どの処理ステージの失敗も値で返し、完成マーカーは全部が通った時にだけ最後に置く。"
  (var outcome (PrepareState))
  (for [stage STAGES]
    (when (isinstance outcome PrepareState)
      (<- started float (GetMonotonic))
      (<- after (| PrepareState EnvFailure) (stage.run request outcome))
      (<- ended float (GetMonotonic))
      (:= outcome (match after
                    (EnvFailure) after
                    _ (replace after :stages (+ after.stages #((StageTime :name stage.name :seconds (- ended started)))))))))
  (match outcome
    (EnvFailure) outcome
    _ (do (<- (WriteEnvMarker request.root
                              (EnvMarker :env request.env :key request.key :platform request.platform
                                         :stages outcome.stages :downloaded outcome.downloaded :built outcome.built
                                         :interpreter outcome.interpreter :child-protocol CHILD-PROTOCOL)))
          (EnvReady :env request.env :key request.key :root request.root :stages outcome.stages
                    :downloaded outcome.downloaded :built outcome.built :interpreter outcome.interpreter))))
