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
;;;   9 bytecode  ReadEditableRoots / ReadHyVersion /        root の venv の interpreter で、焼く根を持つ repo の木の全部を 1 回で作る
;;;               CompileTrees                               (.pyc は source の中身で引く保存先 — doeff-hy の code_store — から書き、
;;;                                                          無い物と今の macro に合わない物だけを焼く・版と root をまたぐ — #3858)。
;;;                                                          焼く範囲 = 宣言の import の根 + venv に editable で入る root の中の dir
;;;                                                          (宣言の bytecode-entries が在れば、木をまたいだ import の閉包だけ)。
;;;                                                          焼いた数と処理ごとの秒は完成マーカーの bytecode の欄へ(#3607 の H2)
;;;  10 確かめ    ProbeImports                               子の約束の版・根の最上位の名の解け先(env-incompatible)
;;;  11 完成      WriteEnvMarker                             完成マーカーを最後に置く(無い root は使わない)
;;;
;;; 時計は doeff-time の GetMonotonic(各処理ステージの秒をマーカーと答えに載せる)。#3676 から、印の処理ステージに書いた file の数と
;;; 区切りの秒(木の repo ごとの写し・展開)、印に置き場の disk の種類(ReadVolume)と起こしてから最初の処理ステージまでの秒(GetTime と
;;; 要求の launched-ms)を載せ、同じ値を計時の 1 行(PrepareNote)で記録する。
;;; 各処理ステージの頭で StageStarted を出す(worker は進みの印で準備の停滞を見分ける — env_upkeep.prepare-overdue)。1 つが長い
;;; bytecode の処理ステージは、1 回の焼きの前と後にも同じ名で出す(処理ステージの中でも進みが見える・#3515)。
(require doeff-hy.macros [defk defeffect <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(require doeff-hy.record [defenum defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])  ; dataclass は defrecord の展開が使う
(import datetime [datetime])
(import re)
(import doeff_time [GetMonotonic GetTime])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout EnvFailure EnvFailureKind CHILD-PROTOCOL
                                                       SUPPORTED-CHILD-PROTOCOLS])
(import doeff_cluster.shared.core.runtime_env_rules [env-failure native-key root-split runtime-env->json])
(import doeff_cluster.shared.core.runtime_env [project-dir])
(import doeff_cluster.worker.intent.env_prepare_model [PrepareRequest StageTime StagePart TREE-COPY TREE-EXPAND VolumeKind MirrorReady FetchState RepoMirror EnvMarker WheelReady SyncReport BytecodeTree BytecodeReport ProbeReport EnvReady PrepareState StageStarted PrepareNote DiskFree ReadVolume ReadCgroupMemory EnsureMirror FetchCommit MaterializeTree TreeHash EnsureNativeWheel SyncProject InstallWheels WriteImportRoots ReadEditableRoots ReadHyVersion CompileTrees ProbeImports WriteEnvMarker] doeff_cluster.shared.intent.env_marker_model [ENV-MARKER-FORMAT FileSha256])

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


(defk env-marker->json [marker]
  {:pre [(: marker EnvMarker)] :post [(: % dict)]}
  "完成マーカーを file に書く JSON の値にする(file の境界の 1 か所)。bytecode の欄は焼いた数と処理ごとの秒(綴りは読み手の defwire
   BytecodeCounts の camel の名と同じ — 読み戻しは test_runtime_identity が確かめる・焼く木が無かった準備は null = 記録が無い — #3607 の H2)。
   dump を使わないのは、生成の型の宣言(.pyi)の defwire の型が dump の受ける型(WireValue)に当たらず、型の検査が通らないため。"
  (<- declared dict (runtime-env->json marker.env))
  (val counts marker.bytecode)
  (val bytecode (if (is counts None)
                    None
                    {"stored" counts.stored "rebuilt" counts.rebuilt "reused" counts.reused "failed" counts.failed
                     "scanSeconds" counts.scan-seconds "closureSeconds" counts.closure-seconds
                     "compileSeconds" counts.compile-seconds
                     "trees" (lfor t counts.trees {"name" t.name "stored" t.stored "rebuilt" t.rebuilt "reused" t.reused
                                                   "failed" t.failed})}))
  ;; 処理ステージの files(root に書いた file の数 — 数えない処理ステージは null)・parts(区切りの秒)・volume(disk の種類)・
  ;; startupSeconds(起こしてから最初の処理ステージまで)は #3676 で足した欄(読み手の置き場の名指しは読まない — 報告だけ)。
  (val volume marker.volume)
  {"format" ENV-MARKER-FORMAT "key" marker.key "platform" marker.platform "env" declared
   "stages" (lfor s marker.stages {"name" s.name "seconds" (round s.seconds 3) "files" s.files
                                   "parts" (lfor p s.parts {"name" p.name "how" p.how "seconds" (round p.seconds 3)})})
   "downloaded" marker.downloaded "built" marker.built
   "interpreter" marker.interpreter "childProtocol" marker.child-protocol
   "bytecode" bytecode
   "volume" (if (is volume None) None {"fsType" volume.fs-type "device" volume.device "mount" volume.mount})
   "startupSeconds" (if (is marker.startup-seconds None) None (round marker.startup-seconds 3))
   ;; hyVersion(venv の Hy の compiler の版 — 次の準備の引き継ぎ元の選びが読む・#3706)。置き場の名指し(decode-marker・known-roots の
   ;; 同一性)は読まない。
   "hyVersion" marker.hy-version
   ;; buildMemoryBytes(組みの山の memory — 先の組みを始める前の memory の見積もりに worker の掃除の数えが読む・#3748)。null = 測れなかった。
   "buildMemoryBytes" marker.build-memory-bytes})


;; /proc/self/mountinfo の 1 行: `<id> <親> <major:minor> <根> <mount の点> <選択> [<任意の欄>…] - <fs の型> <mount の元> <super の選択>`。
;; 名の中の空白・tab・改行・\ は 8 進の \ooo で書かれる。
(setv #^ (get re.Pattern str) MOUNT-ESCAPE (re.compile r"\\([0-7]{3})"))


(defk mount-unescaped [text]
  {:pre [(: text str)] :post [(: % str)]}
  "mountinfo の 8 進の書き換え(\\040 = 空白)を戻すため。"
  (.sub MOUNT-ESCAPE (fn [m] (chr (int (.group m 1) 8))) text))


(defk volume-of-mountinfo [text path]
  {:pre [(: text str) (: path str)] :post [(: % (| VolumeKind None))]}
  "mountinfo の中身から path を含む最も深い mount の fs の型と mount の元を返すため(#3676 — root の置き場が何の disk かを印に残す)。
   path は呼び手が symlink を解いた実の path で渡す。形の崩れた行は読まない・当たる行が無ければ None。"
  (var best None)
  (for [line (.splitlines text)]
    (val fields (.split line " "))
    (when (and (in "-" fields) (>= (len fields) 5))
      (val dash (.index fields "-"))
      (when (>= (len fields) (+ dash 3))
        (<- point str (mount-unescaped (get fields 4)))
        (val inside (or (= point "/") (= path point) (.startswith path (+ point "/"))))
        (when (and inside (or (is best None) (>= (len point) (len best.mount))))
          (<- device str (mount-unescaped (get fields (+ dash 2))))
          (:= best (VolumeKind :fs-type (get fields (+ dash 1)) :device device :mount point))))))
  best)


(defk timing-line [stages volume startup]
  {:pre [(: stages tuple) (: volume (| VolumeKind None)) (: startup (| float None))] :post [(: % str)]}
  "準備の計時の 1 行(準備の記録 — env_tool の log へ出る・#3676): 起こしてから最初の処理ステージまでの秒・disk の種類・処理ステージごとの
   秒と書いた file の数と区切り。読めない値は `-`。"
  (val parts (lfor s stages
                   (.format "{}={:.3f}s{}{}" s.name s.seconds
                            (if (is s.files None) "" (.format "/{}files" s.files))
                            (if s.parts
                                (.format "({})" (.join "," (gfor p s.parts (.format "{}:{}={:.3f}s" p.name p.how p.seconds))))
                                ""))))
  (.format "計時: 起動→最初の処理ステージ {} 秒・disk {}・処理ステージ {}"
           (if (is startup None) "-" (.format "{:.3f}" startup))
           (if (is volume None) "-" (.format "{} {} ({})" volume.fs-type volume.device volume.mount))
           (.join " " parts)))


(defk build-memory-of [current-before peak-before peak-after]
  {:pre [(: current-before (| int None)) (: peak-before (| int None)) (: peak-after (| int None))] :post [(: % (| int None))]}
  "組みの山の memory(byte)を、準備の前後の cgroup の読みから求めるため(#3748)。memory.peak は container の始まりからの最大で戻せない
   (同じ file を別に開いて読むので fd ごとの reset は使えない)ので、準備の間に memory.peak が上がった時だけ、上がった後の peak − 準備の前の
   current を組みの山と読む(組みが足した量の上の端 — 見積もりは早めに断る側)。上がらなかった(前の山の方が高い)・どれかが読めない時は
   None(測れなかった — 次の組みは既定の値か、前の実測で見積もる)。"
  (match #(current-before peak-before peak-after)
    #((int) (int) (int)) :if (> peak-after peak-before) (- peak-after current-before)
    _ None))


;; --- 処理ステージ ---------------------------------------------------------------------------
;; どれも (request state) → 次の state か EnvFailure。

(defk stage-disk [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "空きが下限を切っていれば準備を始めない(途中の ENOSPC で壊れた root を作らないため)。置き場の disk の種類も読んで印へ渡す(#3676)。"
  (<- free int (DiskFree request.root))
  (<- volume (| VolumeKind None) (ReadVolume request.root))
  (if (< free request.min-free-bytes)
      (do (<- failure EnvFailure (env-failure EnvFailureKind.DISK-FULL
                                              (.format "空き {} byte が下限 {} byte を切る" free request.min-free-bytes)))
          failure)
      (replace state :volume volume)))


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
  "repo ごとのツリーを root の下に兄弟で並べる(同じ commit のツリーが既にあれば複製で済ませる)。repo ごとの秒と置き方(写し・展開)を
   区切りとして印へ渡す(#3676)。"
  (val mirrors (dfor m state.mirrors m.name m.mirror))
  (var parts #())
  (for [repo request.env.repos]
    (<- reuse (reuse-tree request.known repo))
    (<- started float (GetMonotonic))
    (<- (MaterializeTree (get mirrors repo.name) repo.commit (.format "{}/{}" request.root repo.name) reuse))
    (<- ended float (GetMonotonic))
    (:= parts (+ parts #((StagePart :name repo.name :how (if (is reuse None) TREE-EXPAND TREE-COPY) :seconds (- ended started))))))
  (replace state :parts parts))


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
  "import の根を venv の .pth に宣言の順で並べる(子に PYTHONPATH を置かずに根を解くため)。書く file は .pth の 1 つ(#3676)。"
  (<- pdir str (project-dir request.env request.root))
  (<- roots tuple (absolute-roots request.env request.root))
  (<- (WriteImportRoots pdir roots))
  (replace state :written 1))


(val BYTECODE-STAGE "bytecode")   ; bytecode の処理ステージの名(計器・マーカー・進みの印)


(defk bytecode-trees [request state editable]
  {:pre [(: request PrepareRequest) (: state PrepareState) (: editable tuple)] :post [(: % tuple)]}
  "焼く根を持つ repo ごとの焼く木(BytecodeTree の列・宣言の repo の順)を作るため — 焼く根 = 宣言の import の根と venv に editable で
   入る dir(bytecode-roots)・宣言の根を持つかで木の問題の扱いが分かれる(bytecode-outcome)。"
  (var trees #())
  (for [repo request.env.repos]
    (<- roots tuple (bytecode-roots request.env editable repo.name))
    (when roots
      (<- declared tuple (repo-roots request.env repo.name))
      (:= trees (+ trees #((BytecodeTree :tree (.format "{}/{}" request.root repo.name) :roots roots :declared (bool declared)))))))
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
    (BytecodeReport :interpreter used :counts counts :problems problems)
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
                  ;; 書いた file の数 = 焼いて書いた .pyc(rebuilt)と保存先の code から書いた .pyc(stored)(#3676・#3858)。焼かずに
                  ;; 残した .pyc(reused)は書いていないので足さない(#3675)。
                  (replace state :interpreter used :bytecode counts :written (+ counts.rebuilt counts.stored)))))))


(defk stage-bytecode [request state]
  {:pre [(: request PrepareRequest) (: state PrepareState)] :post [(: % (| PrepareState EnvFailure))]}
  "焼く根(宣言の import の根と、venv に editable で入る dir)を持つ repo の木の全部を、root の venv の interpreter で 1 回の焼きで作る
   (worker の Hy で作ると macro の展開が違い得るため)。木ごとに起こすと、木 1 つの終わりを待つ間ほかの core が遊ぶ — 焼く道具が全部の木の
   焼く物を 1 つの pool に大きい順に渡す。焼く範囲の入口(bytecode-entries)は全部の木に共通で、import を木をまたいで辿った閉包だけを
   焼く(業務の repo の module が import する依存の repo の module も入る — 入口が無ければ全部の木の根の下を全部焼く: repo の根を指す
   editable — flat layout の package — は tests や docs も焼くが、中身の同じ source は保存先から書く)。木の問題の扱いは
   bytecode-outcome。1 回の焼きの前と後に進みの印を触り直す(StageStarted を同じ名で — 印の中身は変えず時刻だけ進む)。.pyc は source の
   中身で引く保存先から書き、焼くのは中身の変わった file と macro の出所の変わった使う側だけ(#3858 — 前は前の root から引き継ぎ、
   引き継ぎ元の無い root と macro の file が変わった版は全部を焼き直した・負荷の高い時に 267.9 秒 — #3515)。
   焼く前に venv の Hy の compiler の版を読む(完成マーカーに残す — #3706)。"
  (<- pdir str (project-dir request.env request.root))
  (<- editable tuple (ReadEditableRoots pdir request.root))
  (<- hy-version (| str None) (ReadHyVersion pdir))
  (val with-hy (replace state :hy-version hy-version))
  (<- trees tuple (bytecode-trees request with-hy editable))
  (if (not trees)
      with-hy
      (do (<- (StageStarted BYTECODE-STAGE))
          (<- report (| BytecodeReport EnvFailure) (CompileTrees pdir trees request.env.bytecode-entries))
          (<- (StageStarted BYTECODE-STAGE))
          (<- outcome (| PrepareState EnvFailure) (bytecode-outcome trees report with-hy))
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
  "宣言から root 1 つを準備する。どの処理ステージの失敗も値で返し、完成マーカーは全部が通った時にだけ最後に置く。処理ステージごとの秒・
   書いた file の数・区切りの秒と、起こしてから最初の処理ステージまでの秒を印に載せ、同じ値を計時の 1 行で記録する(#3676 — 失敗した
   準備も、通った処理ステージまでの 1 行を出す)。"
  (<- first-at datetime (GetTime))
  ;; 組みの山の memory の前の読み(#3748 — 後の読みと build-memory-of で山を求めて印へ)。
  (<- current-before (| int None) (ReadCgroupMemory "memory.current"))
  (<- peak-before (| int None) (ReadCgroupMemory "memory.peak"))
  (val startup (if (is request.launched-ms None)
                   None
                   (/ (- (* 1000 (.timestamp first-at)) request.launched-ms) 1000.0)))
  (var outcome (PrepareState))
  (var done #())
  (var volume None)
  (for [stage STAGES]
    (when (isinstance outcome PrepareState)
      (<- (StageStarted stage.name))
      (<- started float (GetMonotonic))
      (<- after (| PrepareState EnvFailure) (stage.run request outcome))
      (<- ended float (GetMonotonic))
      (:= outcome (match after
                    (EnvFailure) after
                    _ (do (val timed (StageTime :name stage.name :seconds (- ended started) :files after.written :parts after.parts))
                          (replace after :stages (+ after.stages #(timed)) :written None :parts #()))))
      (match outcome
        (PrepareState :stages stages :volume v) (do (:= done stages) (:= volume v))
        _ None)))
  (<- line str (timing-line done volume startup))
  (<- (PrepareNote line))
  (<- peak-after (| int None) (ReadCgroupMemory "memory.peak"))
  (<- build-memory (| int None) (build-memory-of current-before peak-before peak-after))
  (match outcome
    (EnvFailure) outcome
    _ (do (<- (WriteEnvMarker request.root
                              (EnvMarker :env request.env :key request.key :platform request.platform
                                         :stages outcome.stages :downloaded outcome.downloaded :built outcome.built
                                         :interpreter outcome.interpreter :child-protocol CHILD-PROTOCOL
                                         :bytecode outcome.bytecode :volume outcome.volume :startup-seconds startup
                                         :hy-version outcome.hy-version :build-memory-bytes build-memory)))
          (EnvReady :env request.env :key request.key :root request.root :stages outcome.stages
                    :downloaded outcome.downloaded :built outcome.built :interpreter outcome.interpreter))))
