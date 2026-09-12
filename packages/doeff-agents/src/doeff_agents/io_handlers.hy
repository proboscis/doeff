;;; driver 層の I/O の本番 handler(agora-redesign 段 7 lane 7c — 決定 1.3)。
;;;
;;; 既知の形: algebraic effects。この module は
;;; `doeff_agents.io_effects` の要求を実世界で果たす**唯一の家**で、
;;; driver 層(adapters / tmux / session の台帳 / agentd の client)の
;;; 生の syscall はここにしか無い。
;;;
;;; 検では `doeff_agents.io_fake` の handler を同じ program に
;;; 当てる(composition root が選ぶ)。

(require doeff-hy.handle [defhandler])

(import os)
(import shutil)
(import socket)
(import subprocess)
(import tempfile)
(import time)
(import pathlib [Path])

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


(defn _write-text-io [path text mode]
  "書いてから mode を与える。親 dir は呼び手が MakeDirs で先に作る。"
  (setv target (Path path))
  (.write-text target text :encoding "utf-8")
  (when (is-not mode None)
    (.chmod target mode))
  None)


(defn _touch-file-io [path mode]
  (setv target (Path path))
  (.touch target :exist-ok True)
  (when (is-not mode None)
    (.chmod target mode))
  None)


(defn _run-process-io [argv stdin timeout cwd]
  (try
    (setv outcome (subprocess.run (list argv)
                                  :input stdin
                                  :capture-output True
                                  :text True
                                  :encoding "utf-8"
                                  :timeout timeout
                                  :cwd cwd
                                  :check False))
    (ProcessOutcome :exit-code outcome.returncode
                    :stdout (or outcome.stdout "")
                    :stderr (or outcome.stderr "")
                    :timed-out False)
    (except [error subprocess.TimeoutExpired]
      (ProcessOutcome :exit-code 124
                      :stdout (_decoded error.stdout)
                      :stderr (_decoded error.stderr)
                      :timed-out True))))


(defn _decoded [value]
  "TimeoutExpired が持つ部分出力は bytes のことがある。"
  (cond
    (is value None) ""
    (isinstance value bytes) (.decode value "utf-8" "replace")
    True (str value)))


(defn _spawn-detached-io [argv log-path cwd]
  (.mkdir (. (Path log-path) parent) :parents True :exist-ok True)
  (with [log-file (open log-path "ab")]
    (setv child (subprocess.Popen (list argv)
                                  :stdin subprocess.DEVNULL
                                  :stdout log-file
                                  :stderr subprocess.STDOUT
                                  :cwd cwd
                                  :start-new-session True)))
  child.pid)


(defn _unix-line-request-io [socket-path payload timeout]
  (with [sock (socket.socket socket.AF-UNIX socket.SOCK-STREAM)]
    (when (is-not timeout None)
      (.settimeout sock timeout))
    (.connect sock socket-path)
    (.sendall sock (.encode payload "utf-8"))
    (with [reader (.makefile sock "r" :encoding "utf-8")]
      (setv line (.readline reader))))
  line)


(defn _unix-connect-probe-io [socket-path timeout]
  "繋がった = accepting / 相手不在の証明 = refused / それ以外 = unreachable。
   観測できなかったこと(backlog 満杯の timeout 等)を不在へ畳まない。"
  (setv probe (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (try
    (.settimeout probe timeout)
    (.connect probe socket-path)
    "accepting"
    (except [#(ConnectionRefusedError FileNotFoundError)] "refused")
    (except [OSError] "unreachable")
    (finally (.close probe))))


(defhandler driver-io-handler
  "driver 層の I/O 要求を実世界で果たす。判断は 1 つも持たない。"

  (WhichExecutable [name]
    (resume (shutil.which name)))

  (HomePath []
    (resume (str (Path.home))))

  (TempRoot []
    (resume (tempfile.gettempdir)))

  (ProcessId []
    (resume (os.getpid)))

  (EnvValue [name]
    (resume (.get os.environ name)))

  (PathExists [path]
    (resume (.exists (Path path))))

  (ReadText [path]
    (setv target (Path path))
    (resume (if (.exists target)
                (.read-text target :encoding "utf-8" :errors "replace")
                None)))

  (WriteText [path text mode]
    (resume (_write-text-io path text mode)))

  (AppendText [path text]
    (with [handle (open path "a" :encoding "utf-8")]
      (.write handle text))
    (resume None))

  (MakeDirs [path mode]
    (setv target (Path path))
    (if (is mode None)
        (.mkdir target :parents True :exist-ok True)
        (.mkdir target :mode mode :parents True :exist-ok True))
    (when (is-not mode None)
      (.chmod target mode))
    (resume None))

  (TouchFile [path mode]
    (resume (_touch-file-io path mode)))

  (CopyFile [source target]
    (if (.exists (Path source))
        (do (shutil.copy2 source target) (resume True))
        (resume False)))

  (ListDir [path pattern]
    (setv root (Path path))
    (resume (if (.is-dir root)
                (tuple (sorted (gfor entry (.glob root pattern) (str entry))))
                #())))

  (RunProcess [argv stdin timeout cwd]
    (resume (_run-process-io argv stdin timeout cwd)))

  (SpawnDetached [argv log-path cwd]
    (resume (_spawn-detached-io argv log-path cwd)))

  (UnixLineRequest [socket-path payload timeout]
    (resume (_unix-line-request-io socket-path payload timeout)))

  (UnixConnectProbe [socket-path timeout]
    (resume (_unix-connect-probe-io socket-path timeout)))

  (ExecutableAt [path]
    (resume (and (.exists (Path path)) (os.access path os.X-OK))))

  (MonotonicTime []
    (resume (time.monotonic)))

  (Sleep [seconds]
    (time.sleep seconds)
    (resume None)))


(defn run-driver-io [program]
  "composition root: driver 層の program を本番の I/O で回す。"
  (run (driver-io-handler program)))
