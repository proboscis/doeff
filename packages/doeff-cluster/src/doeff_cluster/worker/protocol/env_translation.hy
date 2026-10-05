;;; 実行環境(root)の準備の要求を汎用の effect へ訳す handler(env-translation)と、準備の process の要求と答えの JSON(2026-09-26・翻訳の形
;;; 2026-09-27・#2028 で env_handlers.hy から移した — 準備の process の入口 main は worker/entry/env_tool)。
;;;
;;; worker(worker/protocol/env_store の env-host)は root 1 つの準備を、worker 自身の環境の入口(worker/entry/env_tool)を別の process として
;;; 起こす(worker のループは待たない・worker の process は変わらない)。
;;; 準備の判断は env_prepare の prepare-env。env-translation は準備の要求(env_prepare の effect)を doeff の汎用の effect へ訳し直すだけで、
;;; 自分では I/O をしない(本番と模擬で同じ 1 つ):
;;;   子 process       RunProcess(git・uv・tar・cp)— 本番 = subprocess-handler・模擬 = scripted-process-handler と env_world の台本
;;;   file system     StatPath・ReadText・ReadBytes・WriteText・AppendText・MakeDirectory・ListDirectory・WalkTree・RenamePath・
;;;                   RemoveTree・AcquireLock・ReleaseLock・ReadDiskFree — 本番 = os-file-handler・模擬 = memory-file-handler
;;; 設定は Ask で読む:
;;;   runtime-env.state         worker の state dir(mirrors/・wheels/・uv-cache/・python/・locks/・probe/ を置く)
;;;   runtime-env.repo-keys     鍵の表 = URL → deploy key の file(空文字 = 鍵なし)。URL を断る表ではない — 表に無い URL は宣言の綴りのまま
;;;                             鍵なしで clone する。宣言の url は同じ repo の別の綴り(ssh と https)でも表の項目に引き当て、clone と deploy key は
;;;                             表の綴りで引く(listed-url)
;;;   runtime-env.code-prepare  bytecode を作る道具(worker 自身の code の code_prepare.hy)の path
;;;   runtime-env.uv            uv の命令(既定 "uv" — PATH で引く)
;;;   runtime-env.progress      処理ステージの進みの印の file(空 = 書かない)
;;;   runtime-env.notes         準備の記録の行を足す file(入口の既定 /dev/stderr)
;;;
;;; git の子の環境は env-mode EXTEND(親を継いで足す)で、足すのは git-environment の 2 つだけ。uv の子は EXTEND に env-drop UV-DROP(呼び手の
;;; venv と uv の設定を持ち込まない)を添え、cache と Python は worker の state dir の下で共有する(UV_CACHE_DIR・UV_PYTHON_INSTALL_DIR)。uv 自身が
;;; process の間の錠を持つ。mirror は URL ごと、native の wheel はキーごとに file の錠(AcquireLock)で排他にする。
;;; 展開は git archive と tar、同じ commit の root からの複製は cp -al(hardlink)の後に持ち越さない物(.venv・__pycache__・完成マーカー)を消す。
(require doeff-hy.macros [defk deff defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import posixpath)
(import re)
(import sys)
(import doeff [Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome ReadEnvironment RunProcess])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld StatPath ReadText ReadBytes WriteText AppendText
                                         MakeDirectory ListDirectory WalkTree RenamePath RemoveTree AcquireLock ReleaseLock ReadDiskFree])
;; uv の子に継がせない変数の型(UV-DROP)・足す変数・native の wheel の置き場と錠と使った印は、起動の script(worker/entry/boot_wheel)と
;; 共有する定義点 native_wheel の物。
(import doeff_cluster.shared.core.native_wheel [UV-DROP WHEEL-USED uv-environment :as uv-variables wheel-dir wheel-lock wheel-tmp])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json])
(import doeff_cluster.worker.core.env_prepare [
                     
                      env-marker->json volume-of-mountinfo] doeff_cluster.worker.intent.env_prepare_model [StageStarted PrepareNote DiskFree ReadVolume VolumeKind EnsureMirror FetchCommit MaterializeTree TreeHash EnsureNativeWheel SyncProject InstallWheels WriteImportRoots ReadEditableRoots CompileTrees ProbeImports WriteEnvMarker MirrorReady FetchState WheelReady SyncReport TreeProblem BytecodeReport ProbeReport PrepareRequest KnownRoot EnvReady ROOTS-PTH] doeff_cluster.shared.intent.env_marker_model [BytecodeCounts FileSha256 ENV-MARKER TreeCounts])

(val DETAIL-CHARS 600)
;; この process の mount の表(置き場の disk の種類を読む — #3676)。
(val MOUNT-TABLE "/proc/self/mountinfo")
;; 展開の複製で持ち越さない dir の名(venv は元の root の絶対 path を持ち、.pyc は元の root の Hy で作った物)。
(val NOT-COPIED (frozenset #(".venv" "__pycache__")))
;; uv の出力で「lock が古い」と「一時の失敗(network)」を見分ける語。
(val LOCK-STALE-PATTERN (re.compile r"(?i)lockfile .*needs to be updated|lock file .*needs to be updated|--locked"))
(val NETWORK-PATTERN (re.compile r"(?i)failed to fetch|error sending request|dns error|connection (?:refused|reset)|timed out|could not resolve|temporary failure|could not read from remote"))
(val PYTHON-PATTERN (re.compile r"(?i)no interpreter found|failed to download .*python|python .*not found|no python"))
(val PREPARED-PATTERN (re.compile r"Prepared (\d+) package"))
;; worker の container の cgroup(v2)の memory の出来事の数え(oom_kill = memory の上限で殺した process の数)。組みの子の終わりが memory の
;; 上限によるかを、子の前後のこの数の差で読む(#3668・memory-killed)。読めない機体(cgroup v1・file が無い)では差を 0 と読む。
(val CGROUP-MEMORY-EVENTS "/sys/fs/cgroup/memory.events")
(val OOM-KILL-PATTERN (re.compile r"(?m)^oom_kill (\d+)$"))
;; signal 9(SIGKILL)での子の終わりの番号(子 process の答えは負の signal の番号)。
(val KILLED-CODE -9)
;; 焼く道具(worker/entry/code_prepare.hy)の stderr の報告の行: 全体の行(carried=… compiled=… failed=… carry_s=… compile_s=… closure_s=…
;; scan_s=… — 全部の欄を読む・#3607 の H2)と、木ごとの行(tree=<--tree の綴り> carried=… compiled=… failed=… problem=<文|->)。行の頭には
;; slog の印(INFO など)が付く。木の綴りは root の下の path(空白を含まない)。秒は道具が小数 2 桁に丸めて書く。
(val COMPILED-PATTERN
  (re.compile r"carried=(\d+) compiled=(\d+) failed=(\d+) carry_s=([0-9.]+) compile_s=([0-9.]+) closure_s=([0-9.]+) scan_s=([0-9.]+)"))
(val TREE-PATTERN (re.compile r"(?m)tree=(\S+) carried=(\d+) compiled=(\d+) failed=(\d+) problem=(.*)$"))
(val NO-TREE-LINE "焼く道具の報告にこの木の行が無い")
;; file system の effect の答えの型の和(失敗・様子・錠・中身・一覧・空きの byte・答えの無い書き)。
(val FILE-ANSWER (| FileFailed PathStat LockHeld str bytes tuple int None))

;; 準備の確かめ(処理ステージ 10)の本体。root の venv の hy で、cwd = 空の作業 dir から起こす(子と同じ起こし方)。
;; 出力 = JSON 1 行 {"childProtocol" 版 "misplaced" [根の外に解けた最上位の名]}。
;; 子の入口の約束の版は root の新旧の両方の置き場から読む — 新しい置き場(shared/intent・c56fa8634 から)を先に、無ければ旧い置き場
;; (それより前の doeff で宣言した root)。片方だけを読むと、worker の版と root の版の組によって約束の版を 0 と読み、起こせる root を
;; env-failed にする(issue #2413 — 2026-10-01 22:20 に service の宣言 1 つが env-failed)。旧い doeff の root が無くなったら旧い置き場を外す。
(val CHILD-PROTOCOL-PLACES #("doeff_cluster.shared.intent.runtime_env_model" "doeff_cluster.runtime_env_model"))
(val PROBE-PROGRAM (.join "\n" [
  "(import importlib importlib.util json os sys)"
  "(setv protocol 0)"
  (.format "(for [place [{}]]" (.join " " (gfor p CHILD-PROTOCOL-PLACES (.format "\"{}\"" p))))
  "  (when (= protocol 0)"
  "    (try (setv protocol (getattr (importlib.import-module place) \"CHILD_PROTOCOL\")) (except [Exception] None))))"
  "(defn has-source [d] (any (gfor #(p ds fs) (os.walk d) f fs (.endswith f #(\".py\" \".hy\")))))"
  "(setv misplaced [])"
  "(for [root (cut sys.argv 1 None)]"
  "  (setv real (os.path.realpath root))"
  "  (for [entry (sorted (os.listdir root))]"
  "    (setv path (os.path.join root entry))"
  "    (setv name (cond (.startswith entry \".\") None (= entry \"__pycache__\") None"
  "                     (and (os.path.isfile path) (.endswith entry #(\".py\" \".hy\"))) (get (.rsplit entry \".\" 1) 0)"
  "                     (and (os.path.isdir path) (has-source path)) entry True None))"
  "    (when (and name (.isidentifier name))"
  "      (setv spec (try (importlib.util.find-spec name) (except [Exception] None)))"
  "      (setv places (cond (is spec None) [] spec.origin [spec.origin] spec.submodule-search-locations (list spec.submodule-search-locations) True []))"
  ;; 根の中 = 根の dir の下で、根の下の venv(project の .venv — 第三者の package の置き場)の外。
  "      (when (not (any (gfor p places :setv rp (os.path.realpath p)"
  "                              (and (.startswith rp (+ real os.sep)) (not-in (+ os.sep \".venv\" os.sep) (cut rp (len real) None))))))"
  "        (.append misplaced name)))))"
  "(print (json.dumps {\"childProtocol\" protocol \"misplaced\" misplaced}))"]))


(defrecord CommandResult
  "外の命令 1 回の結果(準備の要求の答えを作るため)。"
  (#^ int code)
  (#^ str stdout)
  (#^ str stderr)
  ;; 子の間に cgroup の memory の上限で殺された process の数(uv の子だけが数える — 他の子は 0)。
  (setv #^ int oom-kills 0))


;; --- 汎用の effect を出す道具 ------------------------------------------------------------------

(defk settled [answer what]
  {:pre [(: answer FILE-ANSWER) (: what str)] :post [(: % (| PathStat LockHeld str bytes tuple int None))]}
  "file の effect の答えから失敗(FileFailed)を例外にするため(準備を続けられない I/O の失敗 — 準備の process の失敗として worker が読む)。
   file の effect は答えの型を宣言しない(EffectBase の答えは Any)ので、呼び手は答えを束ねる所で (<- 名 (| 成功の型 FileFailed) effect) と
   型を書く(doeff-hy の _bind-yield が実行時に isinstance で確かめる — #1682)。"
  ;; 型紙の match で FileFailed の欄を取り出す(isinstance では型検査がこの関数の中で答えを狭めない — #1672)。
  (match answer
    (FileFailed :path path :detail detail) (raise (RuntimeError (.format "{}: {} — {}" what path detail)))
    _ answer))


(defk outcome-result [outcome]
  {:pre [(: outcome ProcessOutcome)] :post [(: % CommandResult)]}
  "子 process の答えを命令の結果にするため(起こせなかった理由は stderr に足す)。"
  (CommandResult :code outcome.exit-code :stdout outcome.stdout :stderr (+ outcome.stderr outcome.start-error)))


(defk uv-environment [state-dir]
  {:pre [(: state-dir str)] :post [(: % tuple)]}
  "uv の子の環境へ足す変数を作るため: 共有の cache と Python を state dir の下に置く(呼び手の venv を外すのは env-drop UV-DROP)。
   変数の定義点は native_wheel.uv-environment の 1 つ — 起動の script が doeff-vm の wheel を組む時も同じ環境で組む。"
  (tuple (gfor v (uv-variables state-dir) (EnvEntry :name v.name :value v.value))))


(defk oom-kills-now []
  {:pre [] :post [(: % (| int None))]}
  "worker の container の cgroup の oom_kill の数を読むため(組みの子が memory の上限で殺されたかを前後の差で知る)。読めなければ None。"
  (<- read (| str FileFailed) (ReadText CGROUP-MEMORY-EVENTS))
  (match read
    (FileFailed) None
    _ (let [found (.search OOM-KILL-PATTERN read)]
        (if found (int (.group found 1)) None))))


(defk uv-command [args cwd env]
  {:pre [(: args tuple) (: cwd str) (: env tuple)] :post [(: % CommandResult)]}
  "uv を 1 回起こして結果を読むため(env = 親の環境から UV-DROP を外して足す変数)。組みの子が memory の上限で殺されたかを名乗る所は
   ここ 1 つ: 子の前後で cgroup の oom_kill を読み、差を結果に載せる(判断は memory-killed-of)。"
  (<- before (| int None) (oom-kills-now))
  (<- outcome ProcessOutcome (RunProcess :argv args :cwd cwd :env env :env-mode EnvMode.EXTEND :env-drop UV-DROP))
  (<- after (| int None) (oom-kills-now))
  (<- plain CommandResult (outcome-result outcome))
  (CommandResult :code plain.code :stdout plain.stdout :stderr plain.stderr
                 :oom-kills (if (and (is-not before None) (is-not after None)) (max 0 (- after before)) 0)))


(defk memory-killed-of [result what]
  {:pre [(: result CommandResult) (: what str)] :post [(: % (| EnvFailure None))]}
  "組みの子の終わりが cgroup の memory の上限によるなら memory-killed の失敗にするため(恒久 — 同じ上限の下で組み直しても殺される):
   signal 9 で終わり、子の間に oom_kill が増えた時だけ。それ以外(増えていない signal 9 を含む)は None — 呼び手が今の種類で答える。"
  (if (and (= result.code KILLED-CODE) (> result.oom-kills 0))
      (EnvFailure :kind EnvFailureKind.MEMORY-KILLED
                  :detail (.format "{} が signal 9 で終わり、cgroup の memory.events の oom_kill が {} 増えた(memory の上限で殺された)"
                                   what result.oom-kills)
                  :retryable False)
      None))


(defk git-environment [key-file]
  {:pre [(: key-file str)] :post [(: % tuple)]}
  "remote に触る git の子の環境へ足す変数を作るため(許可表の deploy key を使い、対話の問いを出さない — 親の環境は RunProcess の
   env-mode EXTEND が継ぐ)。worker が ssh の命令を持っていれば(起動の script が url ごとの Host の別名を書いた `ssh -F <設定>`)、
   鍵をそれに足す — 置き換えると別名が解けなくなる。"
  (<- seen tuple (ReadEnvironment #("GIT_SSH_COMMAND")))
  (val ssh (if seen (. (get seen 0) value) "ssh"))
  (+ #((EnvEntry :name "GIT_TERMINAL_PROMPT" :value "0"))
     (if key-file
         #((EnvEntry :name "GIT_SSH_COMMAND"
                     :value (.format "{} -i {} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes" ssh key-file)))
         #())))


(defk git [args env]
  {:pre [(: args tuple) (: env (| tuple None))] :post [(: % CommandResult)]}
  "git を 1 回起こして結果を読むため(env = 親の環境に足す変数 — None なら親の環境のまま)。"
  (<- outcome ProcessOutcome (RunProcess :argv (+ #("git") args) :env env :env-mode EnvMode.EXTEND))
  (<- result CommandResult (outcome-result outcome))
  result)


(defk tail-of [result]
  {:pre [(: result CommandResult)] :post [(: % str)]}
  "失敗の理由に載せる出力の末尾。"
  (val text (.strip (+ result.stderr "\n" result.stdout)))
  (cut text (- DETAIL-CHARS) None))


(defk digest16 [text]
  {:pre [(: text str)] :post [(: % str)]}
  "URL・キー・path から dir の名を作る(sha256 の頭 16 桁)。"
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 16))


;; git の URL の 2 つの形: URL 形(scheme://[user@]host[:port]/path)と scp 形(user@host:path — `//` が続かない `:`)。
(val URL-FORM (re.compile r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?([^/:]*)(?::[0-9]*)?(/.*)?$"))
(val SCP-FORM (re.compile r"^(?:[^@/]+@)?([^:/]+):(?!//)(.*)$"))


(defk repo-identity [url]
  {:pre [(: url str)] :post [(: % str)]}
  "git の URL が名指す repo を、綴りに依らない 1 つの形(host の小文字 + `/` + path — 前後の `/` と末尾の `.git` を外す)にするため。
   scheme・利用者・port は落とす(https と ssh と scp 形の git@ は同じ repo)。どちらの形でもなければ前後の空白を外した url のまま。"
  (val text (.strip url))
  (val found (or (.match URL-FORM text) (.match SCP-FORM text)))
  (if (is found None)
      text
      (.format "{}/{}" (.lower (.group found 1))
               (.removesuffix (.strip (or (.group found 2) "") "/") ".git"))))


(defk listed-url [url repo-keys]
  {:pre [(: url str) (: repo-keys dict)] :post [(: % (| str None))]}
  "宣言の url が名指す repo の、鍵の表の綴り(表の項目の URL)を引くため。完全一致が先・無ければ repo-identity が等しい項目がちょうど 1 つの時
   その項目。0 個・2 個以上(同じ repo を表が 2 つの綴りで持つ — どちらで取るか決められない)は None(宣言の綴りのまま鍵なしで取る)。
   表は鍵の表 1 つ: 宣言の側は送り手の checkout の remote の綴りのまま送り、worker が自分の表の綴りで取りに行く(2026-09-28 の事故 —
   ssh の remote の checkout から宣言した daily-verify が「表に無い URL」で 10 分落ちた)。"
  (if (in url repo-keys)
      url
      (do (<- wanted str (repo-identity url))
          (var hits [])
          (for [key repo-keys]
            (<- identity str (repo-identity key))
            (when (= identity wanted)
              (.append hits key)))
          (if (= (len hits) 1) (get hits 0) None))))


(defk kind-at [path]
  {:pre [(: path str)] :post [(: % PathKind)]}
  "path の種類を読むため(symlink は辿る)。"
  (<- seen (| PathStat FileFailed) (StatPath path))
  (<- stat PathStat (settled seen "stat できない"))
  stat.kind)


(defk locked [path body]
  {:pre [(: path str) (: body Program)] :post [(: % (| MirrorReady WheelReady EnvFailure))]}
  "錠 path を取って body(Program)を走らせ、放してから body の答えを返すため(mirror は URL ごと・wheel はキーごとの排他)。"
  (<- (MakeDirectory (posixpath.dirname path)))
  (<- got (| LockHeld FileFailed) (AcquireLock path))
  (<- held LockHeld (settled got "錠を取れない"))
  (try
    (<- answer (| MirrorReady WheelReady EnvFailure) body)
    (finally (<- (ReleaseLock held))))
  answer)


(defk remove-if-present [path]
  {:pre [(: path str)] :post [(: % None)]}
  "path が在れば中身ごと消すため(書きかけの tmp の片づけ)。"
  (<- kind PathKind (kind-at path))
  (when (!= kind PathKind.MISSING)
    (<- gone (| None FileFailed) (RemoveTree path))
    (<- (settled gone "消せない")))
  None)


;; --- 要求ごとの訳し ------------------------------------------------------------------------

(defk mirror-of [url mirror env]
  {:pre [(: url str) (: mirror str) (: env tuple)] :post [(: % (| MirrorReady EnvFailure))]}
  "url の bare mirror を用意する(在ればそのまま・無ければ clone して置き換える)。clone できなければ一時の repo-unreachable。"
  (<- kind PathKind (kind-at mirror))
  (if (!= kind PathKind.MISSING)
      (MirrorReady :path mirror)
      (do (<- (MakeDirectory (posixpath.dirname mirror)))
          (val tmp (+ mirror ".tmp"))
          (<- (remove-if-present tmp))
          (<- cloned CommandResult (git #("clone" "--bare" "--quiet" url tmp) env))
          (if (= cloned.code 0)
              (do (<- moved (| None FileFailed) (RenamePath tmp mirror))
                  (<- (settled moved "mirror を置けない"))
                  (MirrorReady :path mirror))
              (do (<- detail str (tail-of cloned))
                  (<- (remove-if-present tmp))
                  (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :retryable True
                              :detail (.format "{} を clone できない: {}" url detail)))))))


(defk fetch-commit [mirror commit repo-keys]
  {:pre [(: mirror str) (: commit str) (: repo-keys dict)] :post [(: % (| FetchState EnvFailure))]}
  "commit を mirror に揃える(在れば PRESENT・fetch して在れば FETCHED・remote に無ければ MISSING・届かなければ一時の repo-unreachable)。"
  (<- first CommandResult (git #("-C" mirror "cat-file" "-e" (+ commit "^{commit}")) None))
  (if (= first.code 0)
      FetchState.PRESENT
      (do (<- url-read CommandResult (git #("-C" mirror "config" "--get" "remote.origin.url") None))
          (val url (.strip url-read.stdout))
          (<- env tuple (git-environment (.get repo-keys url "")))
          (<- (git #("-C" mirror "fetch" "--quiet" "origin" commit) env))
          (<- all-heads CommandResult (git #("-C" mirror "fetch" "--quiet" "origin" "+refs/heads/*:refs/heads/*") env))
          (<- again CommandResult (git #("-C" mirror "cat-file" "-e" (+ commit "^{commit}")) None))
          (<- detail str (tail-of all-heads))
          (cond
            (= again.code 0) FetchState.FETCHED
            (and (!= all-heads.code 0) (.search NETWORK-PATTERN detail))
            (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :retryable True
                        :detail (.format "{} から fetch できない: {}" url detail))
            True FetchState.MISSING))))


(defk not-carried [dest]
  {:pre [(: dest str)] :post [(: % tuple)]}
  "複製した木のうち持ち越さない物(.venv・__pycache__ の dir と、根の完成マーカー)の path を並べるため(外側の dir を先に・内側は外側と一緒に消える)。"
  (<- listed (| tuple FileFailed) (WalkTree dest))
  (<- entries tuple (settled listed "複製した木を読めない"))
  (var out [])
  (for [entry entries]
    (val parts (.split entry.name "/"))
    (val hit (or (and (= entry.kind PathKind.DIRECTORY) (in (get parts -1) NOT-COPIED))
                 (= entry.name ENV-MARKER)))
    (val inside (any (gfor i (range (- (len parts) 1)) (in (get parts i) NOT-COPIED))))
    (when (and hit (not inside))
      (.append out (posixpath.join dest entry.name))))
  (tuple out))


(defk materialize [mirror commit dest reuse]
  {:pre [(: mirror str) (: commit str) (: dest str) (: reuse (| str None))] :post [(: % None)]}
  "commit の木を dest に置くため(reuse = 同じ commit の木を持つ別の root の dir — hardlink で複製して持ち越さない物を消す・None = mirror から
   git archive と tar で展開する)。"
  (<- (MakeDirectory dest))
  (if (is-not reuse None)
      (do (<- copy-run ProcessOutcome (RunProcess :argv #("cp" "-al" (+ reuse "/.") dest)))
          (<- copied CommandResult (outcome-result copy-run))
          (when (!= copied.code 0)
            (raise (RuntimeError (.format "{} を {} へ複製できない: {}" reuse dest copied.stderr))))
          (<- extra tuple (not-carried dest))
          (for [path extra]
            (<- gone (| None FileFailed) (RemoveTree path))
            (<- (settled gone "持ち越さない物を消せない"))))
      (do (val archive (+ dest ".tar"))
          (<- packed CommandResult (git #("-C" mirror "archive" "--format=tar" "-o" archive commit) None))
          (when (!= packed.code 0)
            (raise (RuntimeError (.format "git archive {} に失敗: {}" commit packed.stderr))))
          (<- unpack-run ProcessOutcome (RunProcess :argv #("tar" "-xf" archive "-C" dest)))
          (<- unpacked CommandResult (outcome-result unpack-run))
          (when (!= unpacked.code 0)
            (raise (RuntimeError (.format "{} を展開できない: {}" archive unpacked.stderr))))
          (<- gone (| None FileFailed) (RemoveTree archive))
          (<- (settled gone "展開の tar を消せない"))))
  None)


(defk file-sha256 [path]
  {:pre [(: path str)] :post [(: % (| str None))]}
  "file の中身の sha256(file でなければ None)。"
  (<- kind PathKind (kind-at path))
  (if (!= kind PathKind.FILE)
      None
      (do (<- read (| bytes FileFailed) (ReadBytes path))
          (<- content bytes (settled read "読めない"))
          (.hexdigest (hashlib.sha256 content)))))


(defk wheel-in [target]
  {:pre [(: target str)] :post [(: % (| str None))]}
  "キーの dir の wheel の path(名の順の先頭・dir が無いか wheel が無ければ None)。"
  (<- kind PathKind (kind-at target))
  (if (!= kind PathKind.DIRECTORY)
      None
      (do (<- listed (| tuple FileFailed) (ListDirectory target))
          (<- entries tuple (settled listed "wheel の dir を読めない"))
          (val wheels (lfor e entries :if (.endswith e.name ".whl") e.name))
          (if wheels (posixpath.join target (get wheels 0)) None))))


(defk wheel-of [package source-dir target state-dir uv]
  {:pre [(: package str) (: source-dir str) (: target str) (: state-dir str) (: uv str)]
   :post [(: % (| WheelReady EnvFailure))]}
  "キーの dir(target)の native の wheel を用意する(在ればそのまま・無ければ source-dir から build して置く)。
   signal での終了(OOM の kill 等)は一時、compiler の誤りは恒久の native-build-failed。"
  (<- existing (| str None) (wheel-in target))
  (if (is-not existing None)
      (WheelReady :path existing :built False)
      (do (val tmp (wheel-tmp target))
          (<- (remove-if-present tmp))
          (<- env tuple (uv-environment state-dir))
          (<- built CommandResult (uv-command #(uv "build" "--wheel" "--out-dir" tmp source-dir) source-dir env))
          (<- made (| str None) (wheel-in tmp))
          (if (and (= built.code 0) (is-not made None))
              (do (<- moved (| None FileFailed) (RenamePath tmp target))
                  (<- (settled moved "wheel を置けない"))
                  (WheelReady :path (posixpath.join target (posixpath.basename made)) :built True))
              (do (<- detail str (tail-of built))
                  (<- (remove-if-present tmp))
                  (<- killed (| EnvFailure None) (memory-killed-of built "native の build"))
                  (if (is-not killed None)
                      killed
                      (EnvFailure :kind EnvFailureKind.NATIVE-BUILD-FAILED :detail detail :retryable (< built.code 0))))))))


(defk site-packages [project-dir]
  {:pre [(: project-dir str)] :post [(: % (| str None))]}
  "project の venv の site-packages の dir(.venv/lib/python*/site-packages の名の順の先頭・無ければ None)。"
  (val lib (posixpath.join project-dir ".venv" "lib"))
  (<- kind PathKind (kind-at lib))
  (if (!= kind PathKind.DIRECTORY)
      None
      (do (<- listed (| tuple FileFailed) (ListDirectory lib))
          (<- entries tuple (settled listed "venv の lib を読めない"))
          (var found None)
          (for [e entries]
            (when (and (is found None) (.startswith e.name "python"))
              (val site (posixpath.join lib e.name "site-packages"))
              (<- site-kind PathKind (kind-at site))
              (when (= site-kind PathKind.DIRECTORY) (:= found site))))
          found)))


(defk editable-dirs [site root]
  {:pre [(: site str) (: root str)] :post [(: % tuple)]}
  "site-packages の .pth(import の根の .pth を除く・名の順)が sys.path に足す dir のうち root の中に在る物 → root からの相対 path の
   tuple(editable で入る package の dir — bytecode を焼く範囲に足すため)。symlink は両側を解いて比べる(StatPath の real-path)。
   拾うのは dir の path を書いた .pth(uv・hatchling・maturin の editable の形)だけ。setuptools の finder 型の editable(.pth が
   import の行で finder を入れる形)は dir を書かないので拾えない(その package は焼かれず、import の時に作られる)。"
  (<- root-seen (| PathStat FileFailed) (StatPath root))
  (<- root-stat PathStat (settled root-seen "root を読めない"))
  (val base root-stat.real-path)
  (<- listed (| tuple FileFailed) (ListDirectory site))
  (<- entries tuple (settled listed "site-packages を読めない"))
  (var out [])
  (for [e entries]
    (when (and (.endswith e.name ".pth") (!= e.name ROOTS-PTH))
      (<- read (| str FileFailed) (ReadText (posixpath.join site e.name)))
      (<- text str (settled read "pth を読めない"))
      (for [line (.splitlines text)]
        (val entry (.strip line))
        ;; site の規則: 空行と # の行は読まない・import で始まる行は実行される code(dir ではない)。
        (when (and entry (not (.startswith entry "#")) (not (.startswith entry #("import " "import\t"))))
          (<- seen (| PathStat FileFailed) (StatPath (if (posixpath.isabs entry) entry (posixpath.join site entry))))
          (<- stat PathStat (settled seen "pth の dir を読めない"))
          (val real stat.real-path)
          (when (and (= stat.kind PathKind.DIRECTORY) (.startswith real (+ base "/")))
            (val rel (posixpath.relpath real base))
            (when (not-in rel out) (.append out rel)))))))
  (tuple out))


(defk sync-failure [result]
  {:pre [(: result CommandResult)] :post [(: % EnvFailure)]}
  "uv sync の失敗を kind に分ける: cgroup の memory の上限で殺された = memory-killed・lock が古い = lock-stale・Python を取れない =
   python-unavailable・network = 一時の sync-failed・それ以外(sdist の build の失敗・解けない依存)= 恒久の sync-failed。"
  (<- detail str (tail-of result))
  (<- killed (| EnvFailure None) (memory-killed-of result "uv sync"))
  (cond
    (is-not killed None) killed
    (.search LOCK-STALE-PATTERN detail) (EnvFailure :kind EnvFailureKind.LOCK-STALE :detail detail :retryable False)
    (.search PYTHON-PATTERN detail) (EnvFailure :kind EnvFailureKind.PYTHON-UNAVAILABLE :detail detail :retryable True)
    (.search NETWORK-PATTERN detail) (EnvFailure :kind EnvFailureKind.SYNC-FAILED :detail detail :retryable True)
    True (EnvFailure :kind EnvFailureKind.SYNC-FAILED :detail detail :retryable False)))


(defk interpreter-of [project-dir]
  {:pre [(: project-dir str)] :post [(: % str)]}
  "root の venv の interpreter の実の path(symlink を辿った先)。"
  (<- seen (| PathStat FileFailed) (StatPath (posixpath.join project-dir ".venv" "bin" "python")))
  (<- stat PathStat (settled seen "venv の interpreter を読めない"))
  stat.real-path)


(defk compile-argv [uv code-prepare project-dir trees entries]
  {:pre [(: uv str) (: code-prepare str) (: project-dir str) (: trees tuple) (: entries tuple)] :post [(: % tuple)]}
  "焼く道具(worker 自身の code の code_prepare.hy)を root の venv の hy で 1 回起こす命令を組むため: 木ごとに --tree・--roots・--from を
   木の順に並べる(道具の揃え方 — --from は木ごとに 1 つ書き、引き継がない木は空文字)。entries は全部の木に共通。"
  (+ #(uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" code-prepare "--revision" "env")
     (if entries #("--entries" (.join "," entries)) #())
     (tuple (gfor t trees a #("--tree" t.tree "--roots" (.join "," t.roots) "--from" (or t.carry-from "")) a))))


(defk reported-counts [text trees]
  {:pre [(: text str) (: trees tuple)] :post [(: % (| BytecodeCounts None))]}
  "焼く道具の stderr の全体の行と木ごとの行を、完成マーカーに載せる数と秒にするため(全体の行が無い・欄が欠けていれば None — 0 で
   埋めない)。木ごとの数は要求の木の順で、木の名は root の下の dir の名(path は載せない)。"
  (val total (.search COMPILED-PATTERN text))
  (if (is total None)
      None
      (do (val lines (tuple (.finditer TREE-PATTERN text)))
          (BytecodeCounts :carried (int (.group total 1)) :compiled (int (.group total 2)) :failed (int (.group total 3))
                          :carry-seconds (float (.group total 4)) :compile-seconds (float (.group total 5))
                          :closure-seconds (float (.group total 6)) :scan-seconds (float (.group total 7))
                          :trees (tuple (gfor t trees m lines :if (= (.group m 1) t.tree)
                                              (TreeCounts :name (posixpath.basename t.tree) :carried (int (.group m 2))
                                                          :compiled (int (.group m 3)) :failed (int (.group m 4)))))))))


(defk compile-answer [result trees project-dir]
  {:pre [(: result CommandResult) (: trees tuple) (: project-dir str)] :post [(: % (| BytecodeReport EnvFailure))]}
  "焼く道具の終わりと stderr を CompileTrees の答えにするため: 全体の報告の行を全部の欄まで読め、終わりが 0(全部の木の検めが通った)か
   1(検めの通らない木が在る)なら BytecodeReport(数と秒・木ごとの行の problem を TreeProblem に — 行の無い木も問題)。道具は終わったが
   全体の行を読めない時は、その事を名指した EnvFailure(数を黙って 0 にしない)。それ以外(起こせない・途中で落ちた・使い方の誤り)は
   道具そのものの失敗の EnvFailure。"
  (<- counts (| BytecodeCounts None) (reported-counts result.stderr trees))
  (<- detail str (tail-of result))
  (cond
    (and (in result.code #(0 1)) (is-not counts None))
      (do (val said (tuple (gfor m (.finditer TREE-PATTERN result.stderr) #((.group m 1) (.strip (.group m 5))))))
          (<- interpreter str (interpreter-of project-dir))
          (BytecodeReport :interpreter interpreter :counts counts
                          :problems (tuple (gfor t trees
                                                 :setv line (next (gfor s said :if (= (get s 0) t.tree) (get s 1)) None)
                                                 :if (!= line "-")
                                                 (TreeProblem :tree t.tree :detail (if (is line None) NO-TREE-LINE line))))))
    (in result.code #(0 1))
      (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "焼く道具の全体の報告の行(carried=… compiled=… failed=… carry_s=… compile_s=… closure_s=… scan_s=…)を読めない: {}"
                                   detail))
    True
      (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                  :detail (.format "root の interpreter で bytecode を作れない: {}" detail))))


(defk write-replacing [path text]
  {:pre [(: path str) (: text str)] :post [(: % None)]}
  "file を別名に書いてから置き換えるため(書きかけを読ませない)。"
  (<- written (| None FileFailed) (WriteText path text :replace True))
  (<- (settled written "書けない"))
  None)


(defk asked-text [key]
  {:pre [(: key str)] :post [(: % str)]}
  "文字列の設定(runtime-env.state・code-prepare・uv・progress・notes)を読むため。Ask の答えは object なので、ここで str と確かめる
   (違う型が来たら、使う所ではなく読んだ所で落ちる)。"
  (<- value str (Ask key))
  value)


(defk asked-repo-keys []
  {:pre [] :post [(: % dict)]}
  "許可表(runtime-env.repo-keys = clone してよい URL → deploy key の file)を読むため。Ask の答えは object なので、ここで dict と確かめる。"
  (<- table dict (Ask "runtime-env.repo-keys"))
  table)


;; --- handler ------------------------------------------------------------------------------

(defhandler env-translation
  ;; 設定は Ask(runtime-env.*)で読む。state dir・許可表・道具の path はセッションで 1 回読む。
  (session val state-dir (! (asked-text "runtime-env.state")))
  (session val repo-keys (! (asked-repo-keys)))
  (session val code-prepare (! (asked-text "runtime-env.code-prepare")))
  (session val uv (! (asked-text "runtime-env.uv")))
  (session val progress (! (asked-text "runtime-env.progress")))
  (session val notes (! (asked-text "runtime-env.notes")))

  (StageStarted [name]
    ;; 進みの印: worker の env-host は印の file の時刻で準備の停滞を見分ける(空 = 印を書かない)。同じ名で
    ;; 出し直すと中身は変わらず時刻だけ進む。
    (when progress
      (<- (write-replacing progress (+ name "\n"))))
    (resume None))

  (DiskFree [path]
    (<- seen (| int FileFailed) (ReadDiskFree path))
    (<- free int (settled seen "空きを読めない"))
    (resume free))

  (ReadVolume [path]
    ;; 置き場の disk の種類(#3676): mount の表(/proc/self/mountinfo)を読み、path の実の path(symlink を解く — 無い path はそのまま)を含む
    ;; 最も深い mount の行を引く。表を読めない機体(macOS 等)は None。
    (<- table (| str FileFailed) (ReadText MOUNT-TABLE))
    (<- seen (| PathStat FileFailed) (StatPath path))
    (val real (match seen
                (PathStat) (if (= seen.kind PathKind.MISSING) path seen.real-path)
                _ path))
    (<- volume (| VolumeKind None) (match table
                                     (FileFailed) None
                                     _ (volume-of-mountinfo table real)))
    (resume volume))

  (EnsureMirror [url]
    ;; mirror の名・deploy key・clone の URL は鍵の表の綴り(同じ repo の別の綴り — ssh と https — も表の項目に引き当てる。完全一致の宣言では
    ;; 今までと同じ値 — 既存の mirror をそのまま使う)。表に無い URL は宣言の綴りのまま鍵なしで clone する(断らない)。
    (<- source (| str None) (listed-url url repo-keys))
    (val chosen (if (is source None) url source))
    (<- name str (digest16 chosen))
    (<- env tuple (git-environment (.get repo-keys chosen "")))
    (<- answer (| MirrorReady EnvFailure)
        (locked (posixpath.join state-dir "locks" (+ "mirror-" name))
                (mirror-of chosen (posixpath.join state-dir "mirrors" (+ name ".git")) env)))
    (resume answer))

  (FetchCommit [mirror commit]
    (<- answer (| FetchState EnvFailure) (fetch-commit mirror commit repo-keys))
    (resume answer))

  (MaterializeTree [mirror commit dest reuse]
    (<- (materialize mirror commit dest reuse))
    (resume None))

  (FileSha256 [path]
    (<- digest (| str None) (file-sha256 path))
    (resume digest))

  (TreeHash [mirror commit path]
    (<- result CommandResult (git #("-C" mirror "rev-parse" (.format "{}:{}" commit path)) None))
    (when (!= result.code 0)
      (raise (RuntimeError (.format "{} の {} の tree hash を読めない: {}" commit path result.stderr))))
    (resume (.strip result.stdout)))

  (EnsureNativeWheel [key package source-dir]
    (val target (wheel-dir state-dir package key))
    (<- wheel (| WheelReady EnvFailure)
        (locked (wheel-lock state-dir key)
                (wheel-of package source-dir target state-dir uv)))
    ;; 使った印(掃除は 7 日使われない wheel の dir を消す — env_upkeep.WHEEL-UNUSED-SECONDS)。
    (when (isinstance wheel WheelReady)
      (<- (write-replacing (posixpath.join target WHEEL-USED) "")))
    (resume wheel))

  (SyncProject [project-dir python groups no-install]
    (<- env tuple (uv-environment state-dir))
    ;; lock はそのまま使う(--frozen)。lock の中身は宣言の lock-sha256 で縛ってあり、確かめは宣言の側に在る。--locked は lock が
    ;; pyproject と合うかを解き直すので、入れない組(dev など)の git の依存まで取りに行き、worker が読めない private の repo が
    ;; 在ると準備が全部止まる(#2730 — 2026-10-02 の本番)。
    (val args (+ #(uv "sync" "--frozen" "--project" project-dir "--python" python "--no-default-groups")
                 (tuple (gfor g groups a ["--group" g] a))
                 (tuple (gfor n no-install a ["--no-install-package" n] a))))
    (<- result CommandResult (uv-command args project-dir env))
    (if (= result.code 0)
        (do (val found (.search PREPARED-PATTERN (+ result.stderr result.stdout)))
            (resume (SyncReport :downloaded (if found (int (.group found 1)) 0))))
        (do (<- failure EnvFailure (sync-failure result))
            (resume failure))))

  (InstallWheels [project-dir wheels]
    (<- env tuple (uv-environment state-dir))
    (<- result CommandResult (uv-command (+ #(uv "pip" "install" "--no-deps" "--python" (posixpath.join project-dir ".venv" "bin" "python"))
                                            (tuple wheels))
                                         project-dir env))
    (if (= result.code 0)
        (resume None)
        (do (<- detail str (tail-of result))
            (resume (EnvFailure :kind EnvFailureKind.SYNC-FAILED :retryable False
                                :detail (.format "native の wheel を入れられない: {}" detail))))))

  (WriteImportRoots [project-dir roots]
    (<- site (| str None) (site-packages project-dir))
    (when (is site None)
      (raise (RuntimeError (.format "{} の venv に site-packages が無い" project-dir))))
    (<- written (| None FileFailed) (WriteText (posixpath.join site ROOTS-PTH) (+ (.join "\n" roots) "\n")))
    (<- (settled written "import の根の .pth を書けない"))
    (resume None))

  (PrepareNote [text]
    ;; 記録は準備を止めない所見なので、書けなくても(FileFailed)準備は続ける。
    (<- (AppendText notes (.format "env: {}\n" text)))
    (resume None))

  (ReadEditableRoots [project-dir root]
    (<- site (| str None) (site-packages project-dir))
    (if (is site None)
        (resume #())
        (do (<- found tuple (editable-dirs site root))
            (resume found))))

  (CompileTrees [project-dir trees entries]
    ;; 焼く道具は全部の木を 1 回で焼く(cwd = project の dir — 木はどれも絶対 path で渡す)。
    (<- env tuple (uv-environment state-dir))
    (<- args tuple (compile-argv uv code-prepare project-dir trees entries))
    (<- result CommandResult (uv-command args project-dir (+ env #((EnvEntry :name "PYTHONDONTWRITEBYTECODE" :value "1")))))
    (<- compiled (| BytecodeReport EnvFailure) (compile-answer result trees project-dir))
    (resume compiled))

  (ProbeImports [project-dir roots]
    (<- env tuple (uv-environment state-dir))
    (<- name str (digest16 project-dir))
    (val empty (posixpath.join state-dir "probe" name))
    (<- (remove-if-present empty))
    (<- made (| None FileFailed) (MakeDirectory empty))
    (<- (settled made "確かめの作業 dir を作れない"))
    (<- result CommandResult (uv-command (+ #(uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" "-c" PROBE-PROGRAM)
                                            (tuple roots))
                                         empty (+ env #((EnvEntry :name "PYTHONDONTWRITEBYTECODE" :value "1")))))
    (<- (remove-if-present empty))
    (val lines (lfor line (.splitlines result.stdout) :if (.startswith (.strip line) "{") line))
    (if (and (= result.code 0) lines)
        (do (val seen (json.loads (get lines -1)))
            (resume (ProbeReport :child-protocol (int (get seen "childProtocol")) :misplaced (tuple (get seen "misplaced")))))
        (do (<- detail str (tail-of result))
            (resume (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                                :detail (.format "root で子と同じ起こし方ができない: {}" detail))))))

  (WriteEnvMarker [root marker]
    (<- content dict (env-marker->json marker))
    (<- (write-replacing (posixpath.join root ENV-MARKER) (json.dumps content :ensure-ascii False :indent 1)))
    (resume None)))


;; --- 準備の process の要求と答えの JSON(入口 worker/entry/env_tool が読み書きする)------------------------------

(defk request-of-json [data]
  {:pre [(: data dict)] :post [(: % PrepareRequest)]}
  "要求の JSON(worker の env-host が書く)→ PrepareRequest。"
  (<- env RuntimeEnv (runtime-env-of-json (get data "env")))
  (var known #())
  (for [k (.get data "known" [])]
    (<- known-env RuntimeEnv (runtime-env-of-json (get k "env")))
    (:= known (+ known #((KnownRoot :env known-env :root (get k "root") :made-ms (int (get k "madeMs")))))))
  (PrepareRequest :env env :key (get data "key") :platform (get data "platform") :root (get data "root")
                  :known known :min-free-bytes (int (.get data "minFreeBytes" 0))))


(defk answer-json [answer]
  {:pre [(: answer (| EnvReady EnvFailure))] :post [(: % dict)]}
  "準備の答え → 答えの JSON(worker の env-host が読む)。"
  (match answer
    (EnvFailure) {"failure" {"kind" answer.kind.value "detail" answer.detail "retryable" answer.retryable}}
    _ {"ready" {"key" answer.key "root" answer.root "interpreter" answer.interpreter
                "downloaded" answer.downloaded "built" answer.built}}))
