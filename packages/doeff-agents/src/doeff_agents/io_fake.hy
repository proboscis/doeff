;;; driver 層の I/O の検用 handler(agora-redesign 段 7 lane 7c — 決定 1.3)。
;;;
;;; 既知の形: algebraic effects。本番 handler
;;; (`doeff_agents.io_handlers`)と同じ要求の語彙を、記憶の中の file 系と
;;; 台本にした子 process で果たす。検は実 file・実 tmux・実 socket を
;;; 触らないので、並走しても互いを壊さない。
;;;
;;; 観測(何を読み書きしたか)は `FakeIoWorld` の欄に残る — 検はそこを見る。
;;;
;;; 本物と同じ答えになるべき性質(不在の答え・親 dir の無い書きの例外・直下の一覧・実行の bit・socket の断り・
;;; 無い命令の答え・log の用意)は tests/test_driver_io_contract.hy が本物と fake の両方に当てる。

(require doeff-hy.macros [defk defhandler <- val])

(import errno)
(import fnmatch)
(import os)
(import posixpath)
(import stat)

(import collections.abc [Iterable Mapping])
(import doeff [EffectBase Program])
(import doeff_core_effects.file_effects [PathKind])
(import doeff_core_effects.process_effects [not-started-outcome start-refusal executable-file-answer])
(import doeff_agents.io_effects [
  ProcessOutcome
  WhichExecutable
  HomePath
  TempRoot
  ProcessId
  EnvValue
  PathExists
  ReadText
  WriteText
  AppendText
  MakeDirs
  TouchFile
  CopyFile
  ListDir
  RunProcess
  SpawnDetached
  UnixLineRequest
  UnixConnectProbe
  ExecutableAt
  MonotonicTime
  Sleep])


;; 台本の総当たりの鍵。どの argv にも当たらなかった時に引く。
(val ANY-COMMAND "*")


(defclass FakeIoWorld []
  "記憶の中の file 系と、台本にした子 process / socket。

   files    : path -> text(実在する file)
   modes    : path -> mode(与えられた時だけ)
   dirs     : 実在する dir の集合
   env      : 環境変数
   which    : 実行ファイル名 -> path(名簿に無い名は None)
   processes: argv の tuple -> ProcessOutcome か、argv を取る呼び出し可能
              (鍵は argv 全体の tuple → argv の先頭 → 総当たりの印 の順に引く)
   sockets  : socket path -> payload を取る呼び出し可能(応答の 1 行)
   executables : 実行できる file の path の集合
   commands : 走らせた argv の並び(観測)
   spawned  : 起こした常駐の並び(観測)
   requests : socket へ送った (path payload) の並び(観測)
   sleeps   : 待った秒の並び(観測)"

  (defn #^ None __init__ [self *
                          #^ (| Mapping None) [files None]
                          #^ (| Iterable None) [dirs None]
                          #^ (| Mapping None) [env None]
                          #^ (| Mapping None) [which None]
                          #^ (| Mapping None) [processes None]
                          #^ (| Mapping None) [sockets None]
                          #^ str [home "/home/agent"]
                          #^ str [temp-root "/tmp"]
                          #^ int [pid 4242]
                          #^ (| Iterable None) [executables None]]
    (setv self.files (dict (or files {})))
    (setv self.modes {})
    (setv self.dirs (set (or dirs [])))
    (setv self.env (dict (or env {})))
    (setv self.which (dict (or which {})))
    (setv self.processes (dict (or processes {})))
    (setv self.sockets (dict (or sockets {})))
    (setv self.home home)
    (setv self.temp-root temp-root)
    (setv self.pid pid)
    (setv self.executables (set (or executables [])))
    (setv self.clock 0.0)
    (setv self.commands [])
    (setv self.spawned [])
    (setv self.requests [])
    (setv self.sleeps [])
    (for [path (list self.files)]
      (self.remember-parents path))
    ;; home と一時 file の置き場は、本物と同じく在る dir として見える。
    (for [place [home temp-root]]
      (self.remember-parents (posixpath.join place "x"))))

  (defn #^ None remember-parents [self #^ str path]
    "file を置いたら親 dir も実在にする(実 file 系と同じ見え方)。"
    (setv parent (posixpath.dirname path))
    (while (and parent (not (in parent self.dirs)))
      (.add self.dirs parent)
      (setv parent (posixpath.dirname parent)))
    None))


;; ---------------------------------------------------------------------------
;; 世界の判断(本物の file 系・socket・子 process ならこう答える、を world の欄から作る)
;; ---------------------------------------------------------------------------

(defk path-present [world path]
  {:pre [(: world FakeIoWorld) (: path str)] :post [(: % bool)] :tags {:context "driver-io" :role "judgment"}}
  "path が在るか(file・dir・聞いている socket — 本物の socket も file 系の上の path として在る)。"
  (or (in path world.files) (in path world.dirs) (in path world.sockets)))


(defk absent-error [path]
  {:pre [(: path str)] :post [(: % FileNotFoundError)] :tags {:context "driver-io" :role "judgment"}}
  "無い path への操作で本物の file 系が上げる例外(\"[Errno 2] No such file or directory: '/x'\" の形)。"
  (FileNotFoundError errno.ENOENT (os.strerror errno.ENOENT) path))


(defk require-parent [world path]
  {:pre [(: world FakeIoWorld) (: path str)] :post [(: % None)] :tags {:context "driver-io" :role "judgment"}}
  "path の親 dir が無ければ本物と同じ FileNotFoundError を上げる(書きは親を黙って作らない — 親は呼び手が MakeDirs で作る)。"
  (match (in (posixpath.dirname path) world.dirs)
    True None
    False (do (<- error FileNotFoundError (absent-error path))
              (raise error))))


(defk direct-entries [world path pattern]
  {:pre [(: world FakeIoWorld) (: path str) (: pattern str)] :post [(: % tuple)] :tags {:context "driver-io" :role "judgment"}}
  "dir の直下の file と dir で、名が pattern に合う物の path を昇順で(本物の Path.glob と同じく孫は並べない)。"
  (tuple (sorted (gfor entry (| (set world.files) world.dirs)
                       :if (and (= (posixpath.dirname entry) path)
                                (!= entry path)
                                (fnmatch.fnmatchcase (posixpath.basename entry) pattern))
                       entry))))


(defk executable-file [world path]
  {:pre [(: world FakeIoWorld) (: path str)] :post [(: % bool)] :tags {:context "driver-io" :role "judgment"}}
  "ExecutableAt に記憶の中の file 系で答えるため: 種類は dir の集合が先(dir は名簿に在っても dir)、次に file か名簿(executables — 名簿の
   path は実行できる file の代役)、どれでもなければ無い物。実行の許しは名簿に在るか、実行の bit の在る mode で置いたこと。判断は本物と同じ
   executable-file-answer(doeff_core_effects.process_effects)。"
  (val kind (cond
              (in path world.dirs) PathKind.DIRECTORY
              (or (in path world.files) (in path world.executables)) PathKind.FILE
              True PathKind.MISSING))
  (val runnable (or (in path world.executables)
                    (bool (& (.get world.modes path 0) (| stat.S-IXUSR stat.S-IXGRP stat.S-IXOTH)))))
  (<- answer bool (executable-file-answer kind runnable))
  answer)


(defk socket-refusal [world path]
  {:pre [(: world FakeIoWorld) (: path str)] :post [(: % OSError)] :tags {:context "driver-io" :role "judgment"}}
  "聞く相手の無い socket path へ繋いだ時に本物が上げる例外: path が無ければ FileNotFoundError、在るが聞く相手が無ければ
   ConnectionRefusedError(どちらも文は本物の connect と同じ形 — path を持たない)。"
  (<- present bool (path-present world path))
  (match present
    True (ConnectionRefusedError errno.ECONNREFUSED (os.strerror errno.ECONNREFUSED))
    False (FileNotFoundError errno.ENOENT (os.strerror errno.ENOENT))))


(defk scripted-outcome [world argv]
  {:pre [(: world FakeIoWorld) (: argv tuple)] :post [(: % ProcessOutcome)] :tags {:context "driver-io" :role "judgment"}}
  "台本の答え(鍵は argv 全体の tuple → argv の先頭 → 総当たりの印 の順に引く)。台本に無い命令は、本物が無い命令に返すのと
   同じ起こせない答え(not-started-outcome — exit-code 127・started False・OSError の文)。"
  (val found (next (gfor key [(tuple argv) (get argv 0) ANY-COMMAND] :if (in key world.processes) (get world.processes key)) None))
  (match found
    None (do (<- refusal str (start-refusal errno.ENOENT (get argv 0)))
             (<- refused ProcessOutcome (not-started-outcome refusal))
             refused)
    (ProcessOutcome) found
    scripted :if (callable scripted) (match (scripted (tuple argv))
                                       (ProcessOutcome) :as answered answered
                                       other (raise (TypeError (.format "台本 {!r} の答えが ProcessOutcome でない: {!r}" argv other))))
    other (raise (TypeError (.format "台本の {!r} の項が ProcessOutcome でも呼べる物でもない: {!r}" argv other)))))


(defhandler fake-driver-io-handler [#^ FakeIoWorld world]
  "記憶の中の世界で driver 層の I/O 要求を果たす。"

  (WhichExecutable [name]
    (resume (.get world.which name)))

  (HomePath []
    (resume world.home))

  (TempRoot []
    (resume world.temp-root))

  (ProcessId []
    (resume world.pid))

  (EnvValue [name]
    (resume (.get world.env name)))

  (PathExists [path]
    (<- present bool (path-present world path))
    (resume present))

  (ReadText [path]
    (resume (.get world.files path)))

  (WriteText [path text mode]
    (<- (require-parent world path))
    (setv (get world.files path) text)
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (.remember-parents world path)
    (resume None))

  (AppendText [path text]
    (<- (require-parent world path))
    (setv (get world.files path) (+ (.get world.files path "") text))
    (.remember-parents world path)
    (resume None))

  (MakeDirs [path mode]
    (.remember-parents world (posixpath.join path "x"))
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (resume None))

  (TouchFile [path mode]
    (<- (require-parent world path))
    (when (not (in path world.files))
      (setv (get world.files path) ""))
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (.remember-parents world path)
    (resume None))

  (CopyFile [source target]
    (if (in source world.files)
        (do
          (<- (require-parent world target))
          (setv (get world.files target) (get world.files source))
          (.remember-parents world target)
          (resume True))
        (resume False)))

  (ListDir [path pattern]
    (<- entries tuple (direct-entries world path pattern))
    (resume entries))

  (RunProcess [argv stdin timeout cwd]
    (.append world.commands #((tuple argv) stdin cwd))
    (<- outcome ProcessOutcome (scripted-outcome world argv))
    (resume outcome))

  (SpawnDetached [argv log-path cwd]
    ;; 本物と同じく log-path を親 dir ごと用意する(在る中身は変えない — 本物は "ab" で開く)。
    (.remember-parents world log-path)
    (.setdefault world.files log-path "")
    (.append world.spawned #((tuple argv) log-path cwd))
    (resume world.pid))

  (UnixLineRequest [socket-path payload timeout]
    (.append world.requests #(socket-path payload))
    (match (.get world.sockets socket-path)
      None (do (<- refusal OSError (socket-refusal world socket-path))
               (raise refusal))
      responder (resume (responder payload))))

  (UnixConnectProbe [socket-path timeout]
    (resume (if (in socket-path world.sockets) "accepting" "refused")))

  (ExecutableAt [path]
    (<- found bool (executable-file world path))
    (resume found))

  (MonotonicTime []
    (setv world.clock (+ world.clock 0.001))
    (resume world.clock))

  (Sleep [seconds]
    (.append world.sleeps seconds)
    (setv world.clock (+ world.clock seconds))
    (resume None)))


(defclass SpawnLedger []
  "起こした常駐と待った秒だけを控える台帳(観測)。"

  (defn #^ None __init__ [self * #^ int [pid 4242]]
    (setv self.spawned [])
    (setv self.sleeps [])
    (setv self.pid pid)))


(defhandler recorded-spawn-handler [#^ SpawnLedger ledger]
  "常駐の起動と待ちだけを控え、残りの要求は外側の handler へ渡す。

   実 socket・実 file を使う検が『daemon を本当に起こさない・本当に待たない』
   1 点だけを差し替えるための handler。名指しした要求以外は解釈しないので、
   外側に本番の handler を置けば他の I/O はそのまま実世界で起きる。"

  (SpawnDetached [argv log-path cwd]
    (.append ledger.spawned #((tuple argv) log-path cwd))
    (resume ledger.pid))

  (Sleep [seconds]
    (.append ledger.sleeps seconds)
    (resume None)))


(defk with-fake-io [world program]
  {:pre [(: world FakeIoWorld) (: program (| Program EffectBase))] :post [(: % "program の答え(型は program ごと)")]
   :tags {:context "driver-io" :role "foundation"}}
  "driver 層の program を記憶の中の世界(fake-driver-io-handler)の下で走らせる。Program の外の呼び手は `(run (with-fake-io world program))`
   で受け、答えは io_root の as_* で絞る。"
  (<- answer ((fake-driver-io-handler world) program))
  answer)
