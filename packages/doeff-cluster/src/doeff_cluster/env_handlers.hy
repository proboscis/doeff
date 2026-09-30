;;; 実行環境(root)の準備の要求を汎用の effect へ訳す handler(env-translation)と、準備の process の入口(2026-09-26・翻訳の形
;;; 2026-09-27)。
;;;
;;; worker(handlers.hy の EnvStore)は root 1 つの準備を、worker 自身の環境のこの module を別の process として起こす
;;; (worker のループは待たない・worker の process は変わらない):
;;;
;;;   hy -m doeff_cluster.env_handlers --request <要求の JSON> --result <答えの JSON> --state <state dir>
;;;      --repo-keys <許可表の JSON> --code-prepare <worker の code_prepare.hy> [--uv uv]
;;;
;;; 準備の判断は env_prepare の prepare-env。env-translation は準備の要求(env_prepare の effect)を doeff の汎用の effect へ訳し直すだけで、
;;; 自分では I/O をしない(本番と模擬で同じ 1 つ):
;;;   子 process       RunProcess(git・uv・tar・cp)— 本番 = subprocess-handler・模擬 = scripted-process-handler と env_world の台本
;;;   file system     StatPath・ReadText・ReadBytes・WriteText・AppendText・MakeDirectory・ListDirectory・WalkTree・RenamePath・
;;;                   RemoveTree・AcquireLock・ReleaseLock・ReadDiskFree — 本番 = os-file-handler・模擬 = memory-file-handler
;;; 設定は Ask で読む:
;;;   runtime-env.state         worker の state dir(mirrors/・wheels/・uv-cache/・python/・locks/・probe/ を置く)
;;;   runtime-env.repo-keys     許可表 = clone してよい URL → deploy key の file(空文字 = 鍵なし)。表に無い URL を断るのは prepare-env(RepoAllowed)。
;;;                             宣言の url は同じ repo の別の綴り(ssh と https)でも表の項目に引き当て、clone と deploy key は表の綴りで引く(allowed-url)
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
(import argparse)
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import posixpath)
(import re)
(import sys)
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.process_effects [EnvEntry EnvMode ProcessOutcome ReadEnvironment RunProcess])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld StatPath ReadText ReadBytes WriteText AppendText
                                         MakeDirectory ListDirectory WalkTree RenamePath RemoveTree AcquireLock ReleaseLock ReadDiskFree])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_time [sync-time-handler])
(import .runtime_env_model [EnvFailure EnvFailureKind RuntimeEnv runtime-env-of-json])
(import .env_prepare [StageStarted PrepareNote DiskFree RepoAllowed EnsureMirror FetchCommit MaterializeTree FileSha256 TreeHash
                      EnsureNativeWheel SyncProject InstallWheels WriteImportRoots ReadEditableRoots CompileTree ProbeImports WriteEnvMarker
                      MirrorReady FetchState WheelReady SyncReport BytecodeReport ProbeReport
                      PrepareRequest KnownRoot EnvReady prepare-env env-marker->json ENV-MARKER ROOTS-PTH])

(val DETAIL-CHARS 600)
;; 展開の複製で持ち越さない dir の名(venv は元の root の絶対 path を持ち、.pyc は元の root の Hy で作った物)。
(val NOT-COPIED (frozenset #(".venv" "__pycache__")))
;; uv の子に継がせない呼び手の環境変数の型(呼び手の venv と uv・Python の設定)。
(val UV-DROP #("UV_*" "PYTHON*" "VIRTUAL_ENV"))
;; native の wheel の dir の使った印(掃除は dir の mtime を読む — 名を変えて置く書き直しで dir の mtime が進む)。
(val WHEEL-USED ".used")
;; uv の出力で「lock が古い」と「一時の失敗(network)」を見分ける語。
(val LOCK-STALE-PATTERN (re.compile r"(?i)lockfile .*needs to be updated|lock file .*needs to be updated|--locked"))
(val NETWORK-PATTERN (re.compile r"(?i)failed to fetch|error sending request|dns error|connection (?:refused|reset)|timed out|could not resolve|temporary failure|could not read from remote"))
(val PYTHON-PATTERN (re.compile r"(?i)no interpreter found|failed to download .*python|python .*not found|no python"))
(val PREPARED-PATTERN (re.compile r"Prepared (\d+) package"))
(val COMPILED-PATTERN (re.compile r"carried=(\d+) compiled=(\d+)"))
;; file system の effect の答えの型の和(失敗・様子・錠・中身・一覧・空きの byte・答えの無い書き)。
(val FILE-ANSWER (| FileFailed PathStat LockHeld str bytes tuple int None))

;; 準備の確かめ(処理ステージ 10)の本体。root の venv の hy で、cwd = 空の作業 dir から起こす(子と同じ起こし方)。
;; 出力 = JSON 1 行 {"childProtocol" 版 "misplaced" [根の外に解けた最上位の名]}。
(val PROBE-PROGRAM (.join "\n" [
  "(import importlib.util json os sys)"
  "(setv protocol 0)"
  "(try (do (import doeff_cluster.runtime_env_model [CHILD-PROTOCOL]) (setv protocol CHILD-PROTOCOL)) (except [Exception] None))"
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
  (#^ str stderr))


;; --- 汎用の effect を出す道具 ------------------------------------------------------------------

(defk settled [answer what]
  {:pre [(: answer FILE-ANSWER) (: what str)] :post [(: % (| PathStat LockHeld str bytes tuple int None))]}
  "file の effect の答えから失敗(FileFailed)を例外にするため(準備を続けられない I/O の失敗 — 準備の process の失敗として worker が読む)。
   file の effect は答えの型を宣言しない(EffectBase の答えは Any)ので、呼び手は答えを束ねる所で (<- 名 (| 成功の型 FileFailed) effect) と
   型を書く(doeff-hy の _bind-yield が実行時に isinstance で確かめる — #1682)。"
  (when (isinstance answer FileFailed)
    (raise (RuntimeError (.format "{}: {} — {}" what answer.path answer.detail))))
  answer)


(defk outcome-result [outcome]
  {:pre [(: outcome ProcessOutcome)] :post [(: % CommandResult)]}
  "子 process の答えを命令の結果にするため(起こせなかった理由は stderr に足す)。"
  (CommandResult :code outcome.exit-code :stdout outcome.stdout :stderr (+ outcome.stderr outcome.start-error)))


(defk uv-environment [state-dir]
  {:pre [(: state-dir str)] :post [(: % tuple)]}
  "uv の子の環境へ足す変数を作るため: 共有の cache と Python を state dir の下に置く(呼び手の venv を外すのは env-drop UV-DROP)。"
  #((EnvEntry :name "UV_CACHE_DIR" :value (posixpath.join state-dir "uv-cache"))
    (EnvEntry :name "UV_PYTHON_INSTALL_DIR" :value (posixpath.join state-dir "python"))
    (EnvEntry :name "UV_NO_PROGRESS" :value "1")))


(defk uv-command [args cwd env]
  {:pre [(: args tuple) (: cwd str) (: env tuple)] :post [(: % CommandResult)]}
  "uv を 1 回起こして結果を読むため(env = 親の環境から UV-DROP を外して足す変数)。"
  (<- outcome ProcessOutcome (RunProcess :argv args :cwd cwd :env env :env-mode EnvMode.EXTEND :env-drop UV-DROP))
  (<- result CommandResult (outcome-result outcome))
  result)


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


(defk allowed-url [url repo-keys]
  {:pre [(: url str) (: repo-keys dict)] :post [(: % (| str None))]}
  "宣言の url が名指す repo の、許可表の綴り(表の項目の URL)を引くため。完全一致が先・無ければ repo-identity が等しい項目がちょうど 1 つの時
   その項目。0 個・2 個以上(同じ repo を表が 2 つの綴りで持つ — どちらで取るか決められない)は None(断る)。
   表は許可表 1 つ: 宣言の側は送り手の checkout の remote の綴りのまま送り、worker が自分の表の綴りで取りに行く(2026-09-28 の事故 —
   ssh の remote の checkout から宣言した daily-verify が「許可表に無い URL」で 10 分落ちた)。"
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
      (do (val tmp (posixpath.join (posixpath.dirname target) (.format ".{}.tmp" (posixpath.basename target))))
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
                  (EnvFailure :kind EnvFailureKind.NATIVE-BUILD-FAILED :detail detail :retryable (< built.code 0)))))))


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
  "uv sync の失敗を kind に分ける: lock が古い = lock-stale・Python を取れない = python-unavailable・network = 一時の sync-failed・
   それ以外(sdist の build の失敗・解けない依存)= 恒久の sync-failed。"
  (<- detail str (tail-of result))
  (cond
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
    ;; 進みの印: worker の EnvStore は印の file の時刻で先読みの停滞を見分ける(空 = 印を書かない)。
    (when progress
      (<- (write-replacing progress (+ name "\n"))))
    (resume None))

  (DiskFree [path]
    (<- seen (| int FileFailed) (ReadDiskFree path))
    (<- free int (settled seen "空きを読めない"))
    (resume free))

  (RepoAllowed [url]
    ;; 同じ repo の別の綴り(ssh と https)も許可表の項目に引き当てる(allowed-url)。
    (<- source (| str None) (allowed-url url repo-keys))
    (resume (is-not source None)))

  (EnsureMirror [url]
    ;; mirror の名・deploy key・clone の URL は許可表の綴り(完全一致の宣言では今までと同じ値 — 既存の mirror をそのまま使う)。
    (<- source (| str None) (allowed-url url repo-keys))
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
    (val wheel-dir (posixpath.join state-dir "wheels" (.format "{}-{}" package key)))
    (<- wheel (| WheelReady EnvFailure)
        (locked (posixpath.join state-dir "locks" (+ "wheel-" key))
                (wheel-of package source-dir wheel-dir state-dir uv)))
    ;; 使った印(掃除は 7 日使われない wheel の dir を消す — env_upkeep.WHEEL-UNUSED-SECONDS)。
    (when (isinstance wheel WheelReady)
      (<- (write-replacing (posixpath.join wheel-dir WHEEL-USED) "")))
    (resume wheel))

  (SyncProject [project-dir python groups no-install]
    (<- env tuple (uv-environment state-dir))
    (val args (+ #(uv "sync" "--locked" "--project" project-dir "--python" python "--no-default-groups")
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

  (CompileTree [project-dir tree roots carry-from entries]
    (<- env tuple (uv-environment state-dir))
    (val args (+ #(uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" code-prepare tree
                   "--revision" "env" "--import-roots" (.join "," roots))
                 (if (is carry-from None) #() #("--from" carry-from))
                 (if entries #("--entries" (.join "," entries)) #())))
    (<- result CommandResult (uv-command args tree (+ env #((EnvEntry :name "PYTHONDONTWRITEBYTECODE" :value "1")))))
    (if (= result.code 0)
        (do (val found (.search COMPILED-PATTERN result.stderr))
            (<- interpreter str (interpreter-of project-dir))
            (resume (BytecodeReport :interpreter interpreter
                                    :compiled (if found (int (.group found 2)) 0)
                                    :carried (if found (int (.group found 1)) 0))))
        (do (<- detail str (tail-of result))
            (resume (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                                :detail (.format "root の interpreter で bytecode を作れない: {}" detail))))))

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


;; --- 準備の process の入口 ----------------------------------------------------------------

(defk request-of-json [data]
  {:pre [(: data dict)] :post [(: % PrepareRequest)]}
  "要求の JSON(worker の EnvStore が書く)→ PrepareRequest。"
  (<- env RuntimeEnv (runtime-env-of-json (get data "env")))
  (var known [])
  (for [k (.get data "known" [])]
    (<- known-env RuntimeEnv (runtime-env-of-json (get k "env")))
    (.append known (KnownRoot :env known-env :root (get k "root"))))
  (PrepareRequest :env env :key (get data "key") :platform (get data "platform") :root (get data "root")
                  :known (tuple known) :min-free-bytes (int (.get data "minFreeBytes" 0))))


(defk answer-json [answer]
  {:pre [(: answer (| EnvReady EnvFailure))] :post [(: % dict)]}
  "準備の答え → 答えの JSON(worker の EnvStore が読む)。"
  (match answer
    (EnvFailure) {"failure" {"kind" answer.kind.value "detail" answer.detail "retryable" answer.retryable}}
    _ {"ready" {"key" answer.key "root" answer.root "interpreter" answer.interpreter
                "downloaded" answer.downloaded "built" answer.built}}))


(deff main []  ; defk にできない: process の入口(`hy -m` の __main__ が Program の外で handler の組を並べて走らせる)
  {:pre [] :post [(: % None)] :tags {:context "runtime-env" :role "entry"}}
  "実行環境(root)1 つを準備して答えの JSON を書く入口(並び = 土台の本物の答え手 + 翻訳)。"
  (setv parser (argparse.ArgumentParser :description "実行環境(root)1 つの準備"))
  (.add-argument parser "--request" :required True)
  (.add-argument parser "--result" :required True)
  (.add-argument parser "--state" :required True)
  (.add-argument parser "--repo-keys" :default "")
  (.add-argument parser "--code-prepare" :required True)
  (.add-argument parser "--uv" :default "uv")
  (.add-argument parser "--progress" :default "" :help "処理ステージの進みの印の file(worker が停滞を見分ける)")
  (setv args (.parse-args parser))
  (setv keys (if args.repo-keys (json.loads (.read-text (Path args.repo-keys) :encoding "utf-8")) {}))
  (setv settings {"runtime-env.state" args.state "runtime-env.repo-keys" keys
                  "runtime-env.code-prepare" args.code-prepare "runtime-env.uv" args.uv
                  "runtime-env.progress" args.progress "runtime-env.notes" "/dev/stderr"})
  (setv request (run (request-of-json (json.loads (.read-text (Path args.request) :encoding "utf-8")))))
  (setv answer (run (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                                   (prepare-env request))))
  (setv content (run (answer-json answer)))
  (setv tmp (Path (+ args.result ".tmp")))
  (.write-text tmp (json.dumps content :ensure-ascii False) :encoding "utf-8")
  (.replace tmp args.result)
  None)


(when (= __name__ "__main__")
  (main))
