;;; 実行環境(root)の準備の Program と、その effect(2026-09-26)。
;;;
;;; worker は宣言(runtime_env_model の RuntimeEnv)を受けると、env のキーの root をこの Program で準備し、完成マーカーを置いた
;;; root の中の子 process で task を走らせる。worker の process は変わらない(新しい commit・lock・native は新しい root を作るだけ)。
;;; I/O は全部 effect で、答えるのは env_handlers.hy の翻訳 env-translation 1 つ(本番と模擬で同じ)。翻訳は doeff の汎用の effect(子 process・
;;; file system)を出し直し、本番は本物の答え手・模擬は env_world の台本と memory の置き場が答える。
;;;
;;; 処理ステージ(失敗はその場で EnvFailure を値で返し、後の処理ステージを走らせない — どれも子 process を起こす前):
;;;   1 空き      DiskFree                                   空きが下限を切れば disk-full
;;;   2 mirror    EnsureMirror / FetchCommit                 repo-unreachable・commit-missing(URL は断らない — 鍵の表に無い URL は鍵なしで clone)
;;;   3 展開      MaterializeTree                            同じ commit のツリーを持つ別の root があれば複製(.venv・マーカー・__pycache__ を除く)
;;;   4 lock      FileSha256                                 展開した uv.lock が宣言の sha256 と違えば lock-mismatch
;;;   5 native    TreeHash / EnsureNativeWheel               キーの wheel が無ければ build(native-build-failed)
;;;   6 依存      SyncProject                                uv sync --frozen(sync-failed・python-unavailable — lock は宣言の sha256 で縛り済み・#2730)
;;;   7 wheel     InstallWheels                              native の wheel を入れる
;;;   8 根        WriteImportRoots                           venv に import の根の .pth を置く(宣言の順)
;;;   9 bytecode  ReadEditableRoots / CompileTrees           root の venv の interpreter で、焼く根を持つ repo の木の全部を 1 回で作る
;;;                                                          (引き継ぎ元は lock と Python が同じ root のうち近い版の物 — carry-source)。
;;;                                                          焼く範囲 = 宣言の import の根 + venv に editable で入る root の中の dir
;;;                                                          (宣言の bytecode-entries が在れば、木をまたいだ import の閉包だけ)
;;;  10 確かめ    ProbeImports                               子の約束の版・根の最上位の名の解け先(env-incompatible)
;;;  11 完成      WriteEnvMarker                             完成マーカーを最後に置く(無い root は使わない)
;;;
;;; 時計は doeff-time の GetMonotonic(各処理ステージの秒をマーカーと答えに載せる)。
;;; 各処理ステージの頭で StageStarted を出す(worker は進みの印で準備の停滞を見分ける — env_upkeep.prepare-overdue)。1 つが長い
;;; bytecode の処理ステージは、1 回の焼きの前と後にも同じ名で出す(処理ステージの中でも進みが見える・#3515)。
(require doeff-hy.macros [defk defeffect <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(require doeff-hy.record [defenum defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])  ; dataclass は defrecord の展開が使う
(import doeff_time [GetMonotonic])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout EnvFailure EnvFailureKind CHILD-PROTOCOL
                                                       SUPPORTED-CHILD-PROTOCOLS])
(import doeff_cluster.shared.core.runtime_env_rules [env-failure native-key root-split runtime-env->json])
(import doeff_cluster.shared.core.runtime_env [project-dir])
(import doeff_cluster.worker.intent.env_prepare_model [PrepareRequest StageTime MirrorReady FetchState RepoMirror EnvMarker WheelReady SyncReport BytecodeTree BytecodeReport ProbeReport EnvReady PrepareState StageStarted PrepareNote DiskFree EnsureMirror FetchCommit MaterializeTree TreeHash EnsureNativeWheel SyncProject InstallWheels WriteImportRoots ReadEditableRoots CompileTrees ProbeImports WriteEnvMarker] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER-FORMAT FileSha256])

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


(defk editable-repo-roots [editable name]
  {:pre [(: editable tuple) (: name str)] :post [(: % tuple)]}
  "ReadEditableRoots の答えのうち repo name の中の dir(repo の中の相対の dir・repo の根は \".\"・答えの順)。"
  (var out [])
  (for [path editable]
    (val parts (.split path "/" 1))
    (when (= (get parts 0) name)
      (.append out (if (= (len parts) 1) "." (get parts 1)))))
  (tuple out))


(defk bytecode-roots [env editable name]
  {:pre [(: env RuntimeEnv) (: editable tuple) (: name str)] :post [(: % tuple)]}
  "repo name の中の焼く根: 宣言の import の根(宣言の順)の後に、venv に editable で入る dir のうち宣言に無い物(2026-09-27 —
   宣言の根は業務の repo だけなので、editable で入る依存の package(doeff の各 package の Hy)が焼かれず、子と入口の検めが毎回
   source から compile した)。"
  (<- declared tuple (repo-roots env name))
  (<- extra tuple (editable-repo-roots editable name))
  (+ declared (tuple (gfor r extra :if (not-in r declared) r))))


(defk reuse-tree [known repo]
  {:pre [(: known tuple) (: repo RepoCheckout)] :post [(: % (| str None))]}
  "同じ url と commit のツリーを持つ完成済みの root の dir(展開を複製で済ませるため)。無ければ None。"
  (var found None)
  (for [k known]
    (for [r k.env.repos]
      (when (and (is found None) (= r.url repo.url) (= r.commit repo.commit))
        (:= found (.format "{}/{}" k.root r.name)))))
  found)


;; Hy の macro(doeff-hy)を持つ repo の宣言の名(送り手は doeff の checkout をこの名で並べる)。Hy の .pyc は、使った macro の
;; file が同じ時だけ使い回せる — この repo の commit が同じ root からの引き継ぎは、macro の file が同じなので安い(#3515 の B)。
;; 宣言にこの名の repo が無ければ、引き継ぎ元の選びの macro の条件(1 番目と 2 番目)は使わない(3 番目と 4 番目の条件だけで選ぶ)。
(val MACRO-REPO "doeff")


(defrecord CarryCandidate
  "bytecode の引き継ぎ元の候補 1 つ(lock の sha256 と Python が同じ完成済みの root の、同じ url の repo のツリー)。tree = ツリーの
   path・root = root の path・made-ms = root の完成の時刻・same-commit = ツリーの commit が新しい宣言の同じ repo と同じ・
   same-macros = macro の repo(MACRO-REPO)の commit が新しい宣言と同じ。"
  (#^ str tree)
  (#^ str root)
  (#^ int made-ms)
  (#^ bool same-commit)
  (#^ bool same-macros))


(defk carry-candidates [known env name]
  {:pre [(: known tuple) (: env RuntimeEnv) (: name str)] :post [(: % tuple)]
   :tags {:context "worker" :role "program"}}
  "repo name の bytecode の引き継ぎ元を選ぶ材料(候補の列・known の順)を作るため — lock の sha256 と Python が同じ完成済みの root
   (Hy の macro の展開が同じ Hy と doeff-hy で固定される組に限るため)の、同じ url の repo のツリー。"
  (val repo (next (gfor r env.repos :if (= r.name name) r)))
  (val macros (next (gfor r env.repos :if (= r.name MACRO-REPO) r) None))
  (tuple (gfor k known
               :if (and (= k.env.project.lock-sha256 env.project.lock-sha256) (= k.env.project.python env.project.python))
               r k.env.repos
               :if (= r.url repo.url)
               (CarryCandidate :tree (.format "{}/{}" k.root r.name) :root k.root :made-ms k.made-ms
                               :same-commit (= r.commit repo.commit)
                               :same-macros (and (is-not macros None)
                                                 (any (gfor m k.env.repos (and (= m.url macros.url) (= m.commit macros.commit)))))))))


(defk carry-source [known env name]
  {:pre [(: known tuple) (: env RuntimeEnv) (: name str)] :post [(: % (| str None))]
   :tags {:context "worker" :role "program"}}
  "repo name の bytecode の引き継ぎ元のツリーの path を返すため(組み直す .pyc を少なくする — 候補は carry-candidates)。無ければ None。
   候補が幾つも在れば、.pyc を使い回せる見込みの高い条件から順に選ぶ(#3515 の B — 前は dir の名の順で最初の root を選び、同じ commit
   で組んだ root が在っても古い commit の root から引き継いで約 2000 個を組み直した):
     1 その repo の commit も macro の repo(MACRO-REPO)の commit も同じ root(ツリーの file も macro の file も同じ)
     2 macro の repo の commit が同じ root(macro の file が同じ — 変わった file の .pyc だけを組む)
     3 その repo の commit が同じ root(Hy の .pyc は macro の file が変わると全部無効になるので、2 より後)
     4 どれも無ければ、候補の全部
   同じ条件に当たる候補の中では、完成の時刻が新しい root・同じ時刻なら root の path の順。宣言に macro の repo が無ければ 1 と 2 には
   誰も当たらず、3 → 4 の順になる。"
  (<- candidates tuple (carry-candidates known env name))
  (val tier (next (gfor group #((tuple (gfor c candidates :if (and c.same-commit c.same-macros) c))
                                (tuple (gfor c candidates :if c.same-macros c))
                                (tuple (gfor c candidates :if c.same-commit c))
                                candidates)
                        :if group group)
                  #()))
  (if tier
      (. (min tier :key (fn [c] #((- c.made-ms) c.root))) tree)
      None))


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
  "宣言した repo ごとに mirror を用意して commit を揃える(clone / fetch できない repo と、worker が取れない commit はここで止める —
   URL そのものは断らない)。"
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
      (<- ready (| WheelReady EnvFailure)
          (EnsureNativeWheel key wheel.package (.format "{}/{}/{}" request.root wheel.repo (get wheel.paths 0))))
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


(val BYTECODE-STAGE "bytecode")   ; bytecode の処理ステージの名(計器・マーカー・進みの印)


(defk bytecode-trees [request editable]
  {:pre [(: request PrepareRequest) (: editable tuple)] :post [(: % tuple)]}
  "焼く根を持つ repo ごとの焼く木(BytecodeTree の列・宣言の repo の順)を作るため — 焼く根 = 宣言の import の根と venv に editable で
   入る dir(bytecode-roots)・引き継ぎ元 = carry-source・宣言の根を持つかで木の問題の扱いが分かれる(bytecode-outcome)。"
  (var trees #())
  (for [repo request.env.repos]
    (<- roots tuple (bytecode-roots request.env editable repo.name))
    (when roots
      (<- declared tuple (repo-roots request.env repo.name))
      (<- carry (| str None) (carry-source request.known request.env repo.name))
      (:= trees (+ trees #((BytecodeTree :tree (.format "{}/{}" request.root repo.name) :roots roots :carry-from carry
                                         :declared (bool declared)))))))
  trees)


(defk bytecode-outcome [trees report state]
  {:pre [(: trees tuple) (: report (| BytecodeReport EnvFailure)) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "1 回の焼きの答えを次の state か EnvFailure にするため: 宣言の根を持つ木の問題は env の失敗(展開の失敗を捕まえるため)、editable で
   入るだけの木の問題は PrepareNote に記録して続ける(その bytecode は最適化 — 子は import の時に compile する)。焼く道具そのものが答えを
   返さなかった時(EnvFailure)も、宣言の根を持つ木が在れば env の失敗、editable で入るだけの木しか無ければ記録して続ける。"
  (val declared (frozenset (gfor t trees :if t.declared t.tree)))
  (match report
    (EnvFailure)
      (if declared
          report
          (do (<- (PrepareNote (.format "editable で入るだけの repo の木 {} の bytecode を焼けない(import の時に作られる): {}"
                                        (.join " " (gfor t trees t.tree)) report.detail)))
              state))
    (BytecodeReport :interpreter used :problems problems)
      (do (val fatal (tuple (gfor p problems :if (in p.tree declared) p)))
          (if fatal
              (do (<- failure EnvFailure
                      (env-failure EnvFailureKind.ENV-INCOMPATIBLE
                                   (.format "root の interpreter で bytecode を作れない: {}"
                                            (.join "; " (gfor p fatal (.format "{}: {}" p.tree p.detail))))))
                  failure)
              (do (for [p problems]
                    (<- (PrepareNote (.format "editable で入るだけの repo の木 {} の bytecode を焼けない(import の時に作られる): {}"
                                              p.tree p.detail))))
                  (replace state :interpreter used))))))


(defk stage-bytecode [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "焼く根(宣言の import の根と、venv に editable で入る dir)を持つ repo の木の全部を、root の venv の interpreter で 1 回の焼きで作る
   (worker の Hy で作ると macro の展開が違い得るため)。木ごとに起こすと、木 1 つの終わりを待つ間ほかの core が遊ぶ — 焼く道具が全部の木の
   焼く物を 1 つの pool に大きい順に渡す。焼く範囲の入口(bytecode-entries)は全部の木に共通で、import を木をまたいで辿った閉包だけを
   焼く(業務の repo の module が import する依存の repo の module も入る — 入口が無ければ全部の木の根の下を全部焼く: repo の根を指す
   editable — flat layout の package — は tests や docs も焼くが、冷えた root で 1 回だけ・以後は引き継ぐ)。木の問題の扱いは
   bytecode-outcome。1 回の焼きの前と後に進みの印を触り直す(StageStarted を同じ名で — 印の中身は変えず時刻だけ進む)。この処理ステージは
   macro の file が変わると全部を焼き直し、負荷の高い時に 267.9 秒かかった(#3515)。"
  (<- pdir str (project-dir request.env request.root))
  (<- editable tuple (ReadEditableRoots pdir request.root))
  (<- trees tuple (bytecode-trees request editable))
  (if (not trees)
      state
      (do (<- (StageStarted BYTECODE-STAGE))
          (<- report (| BytecodeReport EnvFailure) (CompileTrees pdir trees request.env.bytecode-entries))
          (<- (StageStarted BYTECODE-STAGE))
          (<- outcome (| PrepareState EnvFailure) (bytecode-outcome trees report state))
          outcome)))


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
               (Stage :name BYTECODE-STAGE :run stage-bytecode) (Stage :name "probe" :run stage-probe)))


;; --- Program -----------------------------------------------------------------------------

(defk prepare-env [request]
  {:pre [(: request PrepareRequest)] :post [(: % (| EnvReady EnvFailure))]}
  "宣言から root 1 つを準備する。どの処理ステージの失敗も値で返し、完成マーカーは全部が通った時にだけ最後に置く。"
  (var outcome (PrepareState))
  (for [stage STAGES]
    (when (isinstance outcome PrepareState)
      (<- (StageStarted stage.name))
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
