;;; 実行環境の root の準備(env_prepare)の型・effect・定数 — 準備の Program(worker/core/env_prepare)が出し、worker の準備の係が答える(#2025 の 3 本目で分けた)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "intent"})
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass])
(import enum [StrEnum])  ; defenum の展開が使う
(import doeff [EffectBase])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure])
(import doeff_cluster.shared.intent.env_marker_model [BytecodeCounts])


(val ROOTS-PTH "_doeff_cluster_roots.pth")


;; --- 要求と答え ---------------------------------------------------------------------------

(defrecord KnownRoot
  "worker が既に完成させた root(展開の複製と bytecode の引き継ぎの元の候補)。made-ms = 完成マーカーを置いた時刻(epoch ミリ秒 —
   bytecode の引き継ぎ元を、近い版の root が無い時に最も新しく完成した root から選ぶため・#3515 の B)・hy-version = 完成マーカーに
   書いた、その root の venv の Hy の compiler の版(Hy の .pyc は compiler の版が同じ時だけ引き継げる — 欄の無い前の印の root と、
   venv に Hy の無い root は None = 分からない・引き継ぎ元にしない — #3706)。"
  (#^ RuntimeEnv env)
  (#^ str root)
  (#^ int made-ms)
  (#^ (| str None) hy-version))


(defrecord PrepareRequest
  "root 1 つの準備の要求。root = 最終の path(tmp から rename しない — venv が絶対 path を持つので)・known = 完成済みの root・
   min-free-bytes = 準備を始めてよい空きの下限・launched-ms = 準備の process を起こした刻(epoch ミリ秒 — 入口が要求の JSON の
   mtime から読む・None = 読めない。起こしてから最初の処理ステージまでの秒を印に載せるため・#3676)。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str platform)
  (#^ str root)
  (setv #^ tuple known #())
  (setv #^ int min-free-bytes 0)
  (setv #^ (| int None) launched-ms None))


(defrecord StagePart
  "処理ステージの中の 1 区切りの秒(#3676 — 木の処理ステージの repo ごと)。name = repo の名・how = 置き方(TREE-COPY = 同じ commit の
   別の root からの写し・TREE-EXPAND = mirror からの展開)・seconds = 経過の秒。"
  (#^ str name)
  (#^ str how)
  (#^ float seconds))


(val TREE-COPY "copy")
(val TREE-EXPAND "expand")


(defrecord StageTime
  "処理ステージ 1 つの経過の秒(計器とマーカーに載せる)。files = その処理ステージが root に書いた file の数(翻訳が既に知っている数 —
   bytecode = 焼いた数 + 引き継いだ数・roots = .pth の 1 つ。数を知らない処理ステージは None = 数えていない・0 ではない — #3676)・
   parts = 処理ステージの中の区切りの秒(StagePart の列 — 今は木の処理ステージの repo ごとだけ)。"
  (#^ str name)
  (#^ float seconds)
  (setv #^ (| int None) files None)
  (setv #^ tuple parts #()))


(defrecord VolumeKind
  "root の置き場の disk の種類(#3676 — /proc/self/mountinfo の、root を含む最も深い mount の行): fs-type = file system の型の名
   (ext4・xfs・overlay 等)・device = mount の元(block device の名 — /dev/nvme0n1p2 等。網や仮想の fs は fs の言う名)・mount = mount の点。"
  (#^ str fs-type)
  (#^ str device)
  (#^ str mount))


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
  "完成マーカーの中身: 宣言・キー・処理ステージの秒・bytecode を作った interpreter・子の約束の版・bytecode の処理ステージの数と秒
   (bytecode — 焼く木が無かった準備は None = 記録が無い。印の JSON に欄を足しただけなので、欄を書かない作り手の印も同じ形式の版のまま)・
   volume = root の置き場の disk の種類(読めなければ None)・startup-seconds = 準備の process を起こしてから最初の処理ステージまでの秒
   (起こした刻を読めなければ None — #3676)・hy-version = root の venv の Hy の compiler の版(venv に Hy が無ければ None — 次の準備が
   bytecode の引き継ぎ元を選ぶ時に比べる・#3706)・build-memory-bytes = 組みの山の memory(byte — 準備の後の cgroup の memory.peak − 準備の前の
   memory.current。準備の間に container の memory.peak が上がらなかった・読めなかった組みは None — worker が先の組みを始める前の memory の
   見積もりに読む・#3748)。"
  (#^ RuntimeEnv env)
  (#^ str key)
  (#^ str platform)
  (#^ tuple stages)
  (#^ int downloaded)
  (#^ int built)
  (#^ str interpreter)
  (#^ int child-protocol)
  (setv #^ (| BytecodeCounts None) bytecode None)
  (setv #^ (| VolumeKind None) volume None)
  (setv #^ (| float None) startup-seconds None)
  (setv #^ (| str None) hy-version None)
  (setv #^ (| int None) build-memory-bytes None))


(defrecord WheelReady
  "native の wheel の path。built = この準備で build した(キーの wheel が無かった)。"
  (#^ str path)
  (#^ bool built))


(defrecord SyncReport
  "依存を入れた結果。downloaded = cache に無く取りに行った package の数。"
  (#^ int downloaded))


(defrecord CarryFrom
  "bytecode の引き継ぎ元の木 1 つ: tree = 完成済みの root の中の同じ repo の木の path・commit = その木を展開した commit(新しい木の commit
   との git diff が、引き継がない「変わった file」を決める — #3675)。"
  (#^ str tree)
  (#^ str commit))


(defrecord BytecodeTree
  "bytecode を焼く木 1 つ(CompileTrees の欄)。tree = repo の木の path・roots = 木の中の焼く根(宣言の import の根と、venv に editable で
   入る dir)・mirror = その repo の bare mirror の path・commit = 木を展開した commit・carry = 引き継ぎ元(CarryFrom)か None・
   declared = 宣言の import の根を持つ repo の木か(その木の問題は env の失敗・editable で入るだけの木の問題は記録だけ)。"
  (#^ str tree)
  (#^ tuple roots)
  (#^ str mirror)
  (#^ str commit)
  (#^ (| CarryFrom None) carry)
  (#^ bool declared))


(defrecord TreeProblem
  "1 回の焼きの、木 1 つの問題(tree = BytecodeTree の tree・detail = 焼く道具が言う理由 — その木には完成の印が無い)。"
  (#^ str tree)
  (#^ str detail))


(defrecord BytecodeReport
  "bytecode を作った結果。interpreter = 作った interpreter の path(root の venv の物であることを完成マーカーで読む)・counts = 焼く道具が
   報告した数と秒(全部の木の合計・処理ごとの秒・木ごとの数 — 完成マーカーに載せる)・problems = 焼き終えたが検めの通らない木の
   TreeProblem の列(要求の木の順)。"
  (#^ str interpreter)
  (#^ BytecodeCounts counts)
  (#^ tuple problems))


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
  (setv #^ tuple stages #())
  (setv #^ (| BytecodeCounts None) bytecode None)
  ;; 今の処理ステージが root に書いた file の数と区切りの秒(prepare-env が StageTime へ移して次の処理ステージの前に空ける — #3676)。
  (setv #^ (| int None) written None)
  (setv #^ tuple parts #())
  (setv #^ (| VolumeKind None) volume None)
  ;; root の venv の Hy の compiler の版(bytecode の処理ステージが読み、引き継ぎ元の選びと完成マーカーへ渡す — #3706)。
  (setv #^ (| str None) hy-version None))


;; --- effect ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] StageStarted [EffectBase]
  "処理ステージ name を始めた・または長い処理ステージ(bytecode)の中で区切りを 1 つ進めた(進みの印 — 準備を頼んだ worker が停滞を
   見分けるため・同じ名で何度出してもよい)。答え = None。"
  (#^ str name))


(defclass [(dataclass :frozen True)] PrepareNote [EffectBase]
  "準備を止めない所見を 1 行記録する(例: editable で入るだけの依存の repo の bytecode を焼けなかった — 2026-09-27)。答え = None。"
  (#^ str text))


(defclass [(dataclass :frozen True)] DiskFree [EffectBase]
  "path を含む volume の空き(byte)。答え = int。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadCgroupMemory [EffectBase]
  "worker の container の cgroup(v2)の memory の file 1 つ(name = memory.current か memory.peak)の値(byte)を読む(組みの山を測る — #3748)。
   答え = int か None(cgroup v1・file が無い・数でない)。"
  (#^ str name))


(defclass [(dataclass :frozen True)] ReadVolume [EffectBase]
  "path を含む volume の disk の種類(#3676)。答え = VolumeKind か None(mount の表を読めない)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] EnsureMirror [EffectBase]
  "url の bare mirror を用意する(無ければ clone・url ごとに排他)。答え = MirrorReady か EnvFailure(repo-unreachable)。worker は url を
   断らない — worker の鍵の表に同じ repo が在れば表の綴りと鍵で、無ければ宣言の綴りのまま鍵なしで clone する。"
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
   答え = SyncReport か EnvFailure(sync-failed・python-unavailable — lock-stale は --frozen の準備では出ない・#2730)。"
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


(defclass [(dataclass :frozen True)] ReadHyVersion [EffectBase]
  "project の venv に入った Hy の compiler の版(site-packages の hy の dist-info の名から読む — Hy の .pyc は compiler の版が同じ時だけ
   引き継げるので、bytecode の引き継ぎ元の候補を比べるため・#3706)。答え = 版の str か None(venv に site-packages か Hy が無い)。"
  (#^ str project-dir))


(defclass [(dataclass :frozen True)] CompileTrees [EffectBase]
  "焼く根を持つ repo の木の全部の bytecode を、root の venv の interpreter で 1 回の焼きで作る(project-dir = その venv の project・
   trees = BytecodeTree の列・entries = 焼く範囲の入口の module — 全部の木に共通で、import を木をまたいで辿った閉包だけを焼く・空 = 全部の
   木の根の下を全部 — 宣言の bytecode-entries)。答え = BytecodeReport(木ごとの問題は problems)か EnvFailure(焼く道具そのものが
   答えを返さなかった)。"
  (#^ str project-dir)
  (#^ tuple trees)
  (#^ tuple entries))


(defclass [(dataclass :frozen True)] ProbeImports [EffectBase]
  "子と同じ起こし方で root を読む(roots = 絶対 path)。答え = ProbeReport か EnvFailure(env-incompatible)。"
  (#^ str project-dir)
  (#^ tuple roots))


(defclass [(dataclass :frozen True)] WriteEnvMarker [EffectBase]
  "完成マーカーを root に置く(JSON にして別の file へ書いて置き換える)。答え = None。"
  (#^ str root)
  (#^ EnvMarker marker))


;; --- 純粋な判断 ---------------------------------------------------------------------------
