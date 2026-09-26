;;; 実行環境(root)の準備の本物の handler(local-env)と、準備の process の入口(2026-09-26)。
;;;
;;; worker(handlers.hy の EnvStore)は root 1 つの準備を、worker 自身の環境のこの module を別の process として起こす
;;; (worker のループは待たない・worker の process は変わらない):
;;;
;;;   hy -m doeff_cluster.env_handlers --request <要求の JSON> --result <答えの JSON> --state <state dir>
;;;      --repo-keys <許可表の JSON> --code-prepare <worker の code_prepare.hy> [--uv uv]
;;;
;;; 準備の判断は env_prepare の prepare-env。ここは effect の実 I/O だけ(git・uv・file・disk)。設定は Ask で読む:
;;;   runtime-env.state         worker の state dir(mirrors/・wheels/・uv-cache/・python/・locks/ を置く)
;;;   runtime-env.repo-keys     許可表 = clone してよい URL → deploy key の file(空文字 = 鍵なし)。表に無い URL は repo-denied
;;;   runtime-env.code-prepare  bytecode を作る道具(worker 自身の code の code_prepare.hy)の path
;;;   runtime-env.uv            uv の命令(既定 "uv" — PATH で引く)
;;;
;;; uv の cache と Python は worker の state dir の下で共有する(UV_CACHE_DIR・UV_PYTHON_INSTALL_DIR)。uv 自身が process の間の錠を持つ。
;;; mirror は URL ごと、native の wheel はキーごとに file の錠(fcntl)で排他にする。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import argparse)
(import contextlib [contextmanager])
(import dataclasses [dataclass])
(import fcntl)
(import glob)
(import hashlib)
(import json)
(import os)
(import re)
(import shutil)
(import subprocess)
(import sys)
(import tarfile)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader state])
(import doeff_time [sync-time-handler])
(import .runtime_env_model [EnvFailure EnvFailureKind RuntimeEnv runtime-env-of-json])
(import .env_prepare [StageStarted DiskFree EnsureMirror FetchCommit MaterializeTree FileSha256 TreeHash EnsureNativeWheel SyncProject
                      InstallWheels WriteImportRoots CompileTree ProbeImports WriteEnvMarker
                      MirrorReady FetchState WheelReady SyncReport BytecodeReport ProbeReport
                      PrepareRequest KnownRoot EnvReady prepare-env env-marker->json ENV-MARKER ROOTS-PTH])

(val DETAIL-CHARS 600)
;; 展開の複製で持ち越さない物(venv は元の root の絶対 path を持ち、.pyc は元の root の Hy で作った物)。
(val NOT-COPIED (frozenset #(".venv" "__pycache__")))
;; uv の出力で「lock が古い」と「一時の失敗(network)」を見分ける語。
(val LOCK-STALE-PATTERN (re.compile r"(?i)lockfile .*needs to be updated|lock file .*needs to be updated|--locked"))
(val NETWORK-PATTERN (re.compile r"(?i)failed to fetch|error sending request|dns error|connection (?:refused|reset)|timed out|could not resolve|temporary failure"))
(val PYTHON-PATTERN (re.compile r"(?i)no interpreter found|failed to download .*python|python .*not found|no python"))

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
  "外の命令 1 回の結果(準備の I/O の答えを作るため)。"
  (#^ int code)
  (#^ str stdout)
  (#^ str stderr))


;; --- 実 I/O の道具(handler の中だけで使う) ---------------------------------------------

(defk command [args cwd env]
  {:pre [(: args list) (: cwd (| str None)) (: env (| dict None))] :post [(: % CommandResult)]}
  "外の命令(git・uv)を 1 回走らせて結果を読む。"
  (val done (subprocess.run args :cwd cwd :env env :capture-output True :text True))
  (CommandResult :code done.returncode :stdout done.stdout :stderr done.stderr))


(defk tail-of [result]
  {:pre [(: result CommandResult)] :post [(: % str)]}
  "失敗の理由に載せる出力の末尾。"
  (val text (.strip (+ result.stderr "\n" result.stdout)))
  (cut text (- DETAIL-CHARS) None))


(defn [contextmanager] file-lock [#^ Path path]  ; defk にできない: with で使う context manager(mirror と wheel の process の間の排他)
  (.mkdir path.parent :parents True :exist-ok True)
  (with [handle (open path "a")]
    (fcntl.flock handle fcntl.LOCK-EX)
    (try (yield) (finally (fcntl.flock handle fcntl.LOCK-UN)))))


(defk digest16 [text]
  {:pre [(: text str)] :post [(: % str)]}
  "URL・キーから dir の名を作る(sha256 の頭 16 桁)。"
  (cut (.hexdigest (hashlib.sha256 (.encode text "utf-8"))) 0 16))


(defk uv-environment [state-dir]
  {:pre [(: state-dir str)] :post [(: % dict)]}
  "uv を起こす環境変数: 共有の cache と Python を state dir の下に置き、呼び手の venv を持ち込まない。"
  (val base (dfor #(k v) (.items os.environ) :if (not (or (.startswith k "UV_") (.startswith k "PYTHON") (= k "VIRTUAL_ENV"))) k v))
  (| base {"UV_CACHE_DIR" (str (/ (Path state-dir) "uv-cache"))
           "UV_PYTHON_INSTALL_DIR" (str (/ (Path state-dir) "python"))
           "UV_NO_PROGRESS" "1"}))


(defk git-environment [key-file]
  {:pre [(: key-file str)] :post [(: % dict)]}
  "git を起こす環境変数(許可表の deploy key を使い、対話の問いを出さない)。"
  (| (dict os.environ)
     {"GIT_TERMINAL_PROMPT" "0"}
     (if key-file
         {"GIT_SSH_COMMAND" (.format "ssh -i {} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes" key-file)}
         {})))


(defk copy-tree [source dest]
  {:pre [(: source str) (: dest str)] :post [(: % int)]}
  "別の root の同じ commit のツリーを hardlink で複製する(.venv・__pycache__・完成マーカーは持ち越さない)。答え = 複製した file の数。"
  (var count 0)
  (for [#(dirpath dirnames filenames) (os.walk source)]
    (setv (cut dirnames) (lfor d dirnames :if (not-in d NOT-COPIED) d))
    (val rel (os.path.relpath dirpath source))
    (val target (if (= rel ".") dest (os.path.join dest rel)))
    (os.makedirs target :exist-ok True)
    (for [name filenames]
      (when (not (and (= rel ".") (= name ENV-MARKER)))
        (val src (os.path.join dirpath name))
        (val dst (os.path.join target name))
        (cond
          (os.path.islink src) (os.symlink (os.readlink src) dst)
          True (try (os.link src dst) (except [OSError] (shutil.copy2 src dst))))
        (:= count (+ count 1)))))
  count)


(defk mirror-of [url mirror env]
  {:pre [(: url str) (: mirror Path) (: env dict)] :post [(: % (| MirrorReady EnvFailure))]}
  "url の bare mirror を用意する(在ればそのまま・無ければ clone して置き換える)。clone できなければ一時の repo-unreachable。"
  (if (.exists mirror)
      (MirrorReady :path (str mirror))
      (do (.mkdir mirror.parent :parents True :exist-ok True)
          (val tmp (+ (str mirror) ".tmp"))
          (<- cloned CommandResult (command ["git" "clone" "--bare" "--quiet" url tmp] None env))
          (if (= cloned.code 0)
              (do (os.replace tmp mirror)
                  (MirrorReady :path (str mirror)))
              (do (<- detail str (tail-of cloned))
                  (when (.exists (Path tmp)) (shutil.rmtree tmp))
                  (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :retryable True
                              :detail (.format "{} を clone できない: {}" url detail)))))))


(defk wheel-of [package source-dir target state-dir uv]
  {:pre [(: package str) (: source-dir str) (: target Path) (: state-dir str) (: uv str)]
   :post [(: % (| WheelReady EnvFailure))]}
  "キーの dir(target)の native の wheel を用意する(在ればそのまま・無ければ source-dir から build して置く)。
   signal での終了(OOM の kill 等)は一時、compiler の誤りは恒久の native-build-failed。"
  (val existing (sorted (glob.glob (str (/ target "*.whl")))))
  (if existing
      (WheelReady :path (get existing 0) :built False)
      (do (val tmp (/ target.parent (.format ".{}.{}" target.name (os.getpid))))
          (<- env dict (uv-environment state-dir))
          (<- built CommandResult (command [uv "build" "--wheel" "--out-dir" (str tmp) source-dir] source-dir env))
          (val wheels (sorted (glob.glob (str (/ tmp "*.whl")))))
          (if (and (= built.code 0) wheels)
              (do (os.replace tmp target)
                  (WheelReady :path (str (/ target (. (Path (get wheels 0)) name))) :built True))
              (do (<- detail str (tail-of built))
                  (when (.exists tmp) (shutil.rmtree tmp))
                  (EnvFailure :kind EnvFailureKind.NATIVE-BUILD-FAILED :detail detail :retryable (< built.code 0)))))))


(defk site-packages [project-dir]
  {:pre [(: project-dir str)] :post [(: % (| str None))]}
  "project の venv の site-packages の dir(無ければ None)。"
  (val found (sorted (glob.glob (os.path.join project-dir ".venv" "lib" "python*" "site-packages"))))
  (if found (get found 0) None))


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


;; --- handler ------------------------------------------------------------------------------

(defhandler local-env
  ;; 設定は Ask(runtime-env.*)で読む。state dir・許可表・道具の path はセッションで 1 回読む。
  (session val state-dir (! (Ask "runtime-env.state")))
  (session val repo-keys (! (Ask "runtime-env.repo-keys")))
  (session val code-prepare (! (Ask "runtime-env.code-prepare")))
  (session val uv (! (Ask "runtime-env.uv")))
  (session val progress (! (Ask "runtime-env.progress")))

  (StageStarted [name]
    ;; 進みの印: worker の EnvStore は印の file の時刻で先読みの停滞を見分ける(空 = 印を書かない)。
    (when progress
      (val tmp (Path (+ progress ".tmp")))
      (.write-text tmp (+ name "\n") :encoding "utf-8")
      (os.replace tmp progress))
    (resume None))

  (DiskFree [path]
    (var probe (Path path))
    (while (not (.exists probe)) (:= probe probe.parent))
    (resume (. (shutil.disk-usage probe) free)))

  (EnsureMirror [url]
    (if (not-in url repo-keys)
        (resume (EnvFailure :kind EnvFailureKind.REPO-DENIED :retryable False
                            :detail (.format "worker の許可表に無い URL: {}" url)))
        (do (<- name str (digest16 url))
            (<- env dict (git-environment (get repo-keys url)))
            (with [_ (file-lock (/ (Path state-dir) "locks" (+ "mirror-" name)))]
              (<- answer (| MirrorReady EnvFailure)
                  (mirror-of url (/ (Path state-dir) "mirrors" (+ name ".git")) env)))
            (resume answer))))

  (FetchCommit [mirror commit]
    (<- first CommandResult (command ["git" "-C" mirror "cat-file" "-e" (+ commit "^{commit}")] None None))
    (if (= first.code 0)
        (resume FetchState.PRESENT)
        (do (<- url-read CommandResult (command ["git" "-C" mirror "config" "--get" "remote.origin.url"] None None))
            (val url (.strip url-read.stdout))
            (<- env dict (git-environment (.get repo-keys url "")))
            (<- by-sha CommandResult (command ["git" "-C" mirror "fetch" "--quiet" "origin" commit] None env))
            (<- all-heads CommandResult (command ["git" "-C" mirror "fetch" "--quiet" "origin" "+refs/heads/*:refs/heads/*"] None env))
            (<- again CommandResult (command ["git" "-C" mirror "cat-file" "-e" (+ commit "^{commit}")] None None))
            (<- detail str (tail-of all-heads))
            (cond
              (= again.code 0) (resume FetchState.FETCHED)
              (and (!= all-heads.code 0) (.search NETWORK-PATTERN (+ detail "could not read from remote")))
              (resume (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :retryable True
                                  :detail (.format "{} から fetch できない: {}" url detail)))
              True (resume FetchState.MISSING)))))

  (MaterializeTree [mirror commit dest reuse]
    (os.makedirs dest :exist-ok True)
    (if (is-not reuse None)
        (<- (copy-tree reuse dest))
        (do (val archive (+ dest ".tar"))
            (<- packed CommandResult (command ["git" "-C" mirror "archive" "--format=tar" "-o" archive commit] None None))
            (when (!= packed.code 0)
              (raise (RuntimeError (.format "git archive {} に失敗: {}" commit packed.stderr))))
            (with [t (tarfile.open archive)] (.extractall t dest :filter "data"))
            (os.remove archive)))
    (resume None))

  (FileSha256 [path]
    (val p (Path path))
    (resume (if (.is-file p) (.hexdigest (hashlib.sha256 (.read-bytes p))) None)))

  (TreeHash [mirror commit path]
    (<- result CommandResult (command ["git" "-C" mirror "rev-parse" (.format "{}:{}" commit path)] None None))
    (when (!= result.code 0)
      (raise (RuntimeError (.format "{} の {} の tree hash を読めない: {}" commit path result.stderr))))
    (resume (.strip result.stdout)))

  (EnsureNativeWheel [key package source-dir]
    (val wheel-dir (/ (Path state-dir) "wheels" (.format "{}-{}" package key)))
    (with [_ (file-lock (/ (Path state-dir) "locks" (+ "wheel-" key)))]
      (<- wheel (| WheelReady EnvFailure) (wheel-of package source-dir wheel-dir state-dir uv)))
    ;; 使った印(掃除は 7 日使われない wheel の dir を消す — env_upkeep.WHEEL-UNUSED-SECONDS)。
    (when (isinstance wheel WheelReady) (os.utime wheel-dir))
    (resume wheel))

  (SyncProject [project-dir python groups no-install]
    (<- env dict (uv-environment state-dir))
    (val args (+ [uv "sync" "--locked" "--project" project-dir "--python" python "--no-default-groups"]
                 (lfor g groups a ["--group" g] a)
                 (lfor n no-install a ["--no-install-package" n] a)))
    (<- result CommandResult (command args project-dir env))
    (if (= result.code 0)
        (do (val found (.search (re.compile r"Prepared (\d+) package") (+ result.stderr result.stdout)))
            (resume (SyncReport :downloaded (if found (int (.group found 1)) 0))))
        (do (<- failure EnvFailure (sync-failure result))
            (resume failure))))

  (InstallWheels [project-dir wheels]
    (<- env dict (uv-environment state-dir))
    (<- result CommandResult (command (+ [uv "pip" "install" "--no-deps" "--python" (os.path.join project-dir ".venv" "bin" "python")]
                                         (list wheels))
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
    (val target (/ (Path site) ROOTS-PTH))
    (.write-text target (+ (.join "\n" roots) "\n") :encoding "utf-8")
    (resume None))

  (CompileTree [project-dir tree roots carry-from entries]
    (<- env dict (uv-environment state-dir))
    (val args (+ [uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" code-prepare tree
                  "--revision" "env" "--import-roots" (.join "," roots)]
                 (if (is carry-from None) [] ["--from" carry-from])
                 (if entries ["--entries" (.join "," entries)] [])))
    (<- result CommandResult (command args tree (| env {"PYTHONDONTWRITEBYTECODE" "1"})))
    (if (= result.code 0)
        (do (val found (.search (re.compile r"carried=(\d+) compiled=(\d+)") result.stderr))
            (resume (BytecodeReport :interpreter (os.path.realpath (os.path.join project-dir ".venv" "bin" "python"))
                                    :compiled (if found (int (.group found 2)) 0)
                                    :carried (if found (int (.group found 1)) 0))))
        (do (<- detail str (tail-of result))
            (resume (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                                :detail (.format "root の interpreter で bytecode を作れない: {}" detail))))))

  (ProbeImports [project-dir roots]
    (<- env dict (uv-environment state-dir))
    (val empty (/ (Path state-dir) "probe" (str (os.getpid))))
    (.mkdir empty :parents True :exist-ok True)
    (<- result CommandResult (command (+ [uv "run" "--no-sync" "--frozen" "--project" project-dir "hy" "-c" PROBE-PROGRAM]
                                         (list roots))
                                      (str empty) (| env {"PYTHONDONTWRITEBYTECODE" "1"})))
    (shutil.rmtree empty :ignore-errors True)
    (val lines (lfor line (.splitlines result.stdout) :if (.startswith (.strip line) "{") line))
    (if (and (= result.code 0) lines)
        (do (val seen (json.loads (get lines -1)))
            (resume (ProbeReport :child-protocol (int (get seen "childProtocol")) :misplaced (tuple (get seen "misplaced")))))
        (do (<- detail str (tail-of result))
            (resume (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                                :detail (.format "root で子と同じ起こし方ができない: {}" detail))))))

  (WriteEnvMarker [root marker]
    (<- content dict (env-marker->json marker))
    (val target (/ (Path root) ENV-MARKER))
    (val tmp (/ (Path root) (+ ENV-MARKER ".tmp")))
    (.write-text tmp (json.dumps content :ensure-ascii False :indent 1) :encoding "utf-8")
    (os.replace tmp target)
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


(defn main []  ; defk にできない: process の入口(Program の外で handler の組を並べて走らせる)
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
                  "runtime-env.progress" args.progress})
  (setv request (run (request-of-json (json.loads (.read-text (Path args.request) :encoding "utf-8")))))
  (setv answer (run (with-handlers [(state) (sync-time-handler) (reader settings) local-env] (prepare-env request))))
  (setv content (run (answer-json answer)))
  (setv tmp (Path (+ args.result ".tmp")))
  (.write-text tmp (json.dumps content :ensure-ascii False) :encoding "utf-8")
  (os.replace tmp args.result))


(when (= __name__ "__main__")
  (main))
