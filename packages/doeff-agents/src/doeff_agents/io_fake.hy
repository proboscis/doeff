;;; driver 層の I/O の検用 handler(agora-redesign 段 7 lane 7c — 決定 1.3)。
;;;
;;; 既知の形: algebraic effects。本番 handler
;;; (`doeff_agents.io_handlers`)と同じ要求の語彙を、記憶の中の file 系と
;;; 台本にした子 process で果たす。検は実 file・実 tmux・実 socket を
;;; 触らないので、並走しても互いを壊さない。
;;;
;;; 観測(何を読み書きしたか)は `FakeIoWorld` の欄に残る — 検はそこを見る。

(require doeff-hy.handle [defhandler])

(import posixpath)

(import doeff [run])
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
(setv ANY-COMMAND "*")


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

  (defn __init__ [self * [files None] [dirs None] [env None] [which None]
                  [processes None] [sockets None] [home "/home/agent"]
                  [temp-root "/tmp"] [pid 4242] [executables None]]
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
      (self.remember-parents path)))

  (defn remember-parents [self path]
    "file を置いたら親 dir も実在にする(実 file 系と同じ見え方)。"
    (setv parent (posixpath.dirname path))
    (while (and parent (not (in parent self.dirs)))
      (.add self.dirs parent)
      (setv parent (posixpath.dirname parent)))
    None)

  (defn exists? [self path]
    (or (in path self.files) (in path self.dirs)))

  (defn outcome-for [self argv]
    "台本の答え。名簿に無い argv は「その命令は無い」= exit 127。"
    (setv scripted (.get self.processes (tuple argv)))
    (when (is scripted None)
      (setv scripted (.get self.processes (get argv 0))))
    (when (is scripted None)
      (setv scripted (.get self.processes ANY-COMMAND)))
    (cond
      (is scripted None) (ProcessOutcome :exit-code 127 :stdout ""
                                         :stderr f"fake: 台本に無い命令 {(list argv)}"
                                         :timed-out False)
      (callable scripted) (scripted (tuple argv))
      True scripted)))


(defhandler fake-driver-io-handler [world]
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
    (resume (.exists? world path)))

  (ReadText [path]
    (resume (.get world.files path)))

  (WriteText [path text mode]
    (setv (get world.files path) text)
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (.remember-parents world path)
    (resume None))

  (AppendText [path text]
    (setv (get world.files path) (+ (.get world.files path "") text))
    (.remember-parents world path)
    (resume None))

  (MakeDirs [path mode]
    (.add world.dirs path)
    (.remember-parents world (posixpath.join path "x"))
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (resume None))

  (TouchFile [path mode]
    (when (not (in path world.files))
      (setv (get world.files path) ""))
    (when (is-not mode None)
      (setv (get world.modes path) mode))
    (.remember-parents world path)
    (resume None))

  (CopyFile [source target]
    (if (in source world.files)
        (do
          (setv (get world.files target) (get world.files source))
          (.remember-parents world target)
          (resume True))
        (resume False)))

  (ListDir [path pattern]
    (import fnmatch)
    (resume (tuple (sorted (gfor entry (list world.files)
                                 :if (and (= (posixpath.dirname entry) path)
                                          (fnmatch.fnmatchcase (posixpath.basename entry) pattern))
                                 entry)))))

  (RunProcess [argv stdin timeout cwd]
    (.append world.commands #((tuple argv) stdin cwd))
    (resume (.outcome-for world argv)))

  (SpawnDetached [argv log-path cwd]
    (.append world.spawned #((tuple argv) log-path cwd))
    (resume world.pid))

  (UnixLineRequest [socket-path payload timeout]
    (.append world.requests #(socket-path payload))
    (setv responder (.get world.sockets socket-path))
    (when (is responder None)
      (raise (ConnectionRefusedError f"fake: socket が無い {socket-path}")))
    (resume (responder payload)))

  (UnixConnectProbe [socket-path timeout]
    (resume (if (in socket-path world.sockets) "accepting" "refused")))

  (ExecutableAt [path]
    (resume (in path world.executables)))

  (MonotonicTime []
    (setv world.clock (+ world.clock 0.001))
    (resume world.clock))

  (Sleep [seconds]
    (.append world.sleeps seconds)
    (setv world.clock (+ world.clock seconds))
    (resume None)))


(defclass SpawnLedger []
  "起こした常駐と待った秒だけを控える台帳(観測)。"

  (defn __init__ [self * [pid 4242]]
    (setv self.spawned [])
    (setv self.sleeps [])
    (setv self.pid pid)))


(defhandler recorded-spawn-handler [ledger]
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


(defn run-fake-io [world program]
  "composition root(検): driver 層の program を記憶の中の世界で回す。"
  (run ((fake-driver-io-handler world) program)))
