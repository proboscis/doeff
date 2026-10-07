;;; 汎用の file system の effect(file_effects.hy)の本物の答え手 os-file-handler(agora-redesign #802 便 1)。os・shutil・fcntl を呼んで値を
;;; 詰め替えるだけで、判断を持たない。失敗(OSError)は FileFailed の値で答える。
;;;
;;; 錠の取り(AcquireLock)は取れるまで待つ(fcntl.flock)。os-file-handler はその待ちを呼んだ thread で待つので、1 つの scheduler に task が
;;; 並ぶ process では、錠を持った task が待ち(子 process・HTTP)へ移った間に別の task が同じ錠を取りに来ると scheduler の thread ごと
;;; 塞がり、錠を持つ task が戻れず錠を返せない(agora-redesign #3051 — 着地の service の口を 1 つの scheduler の Program にする時に見つけた)。
;;; offloaded-lock-handler は錠の取りだけを呼び 1 つに thread 1 本(offloaded_call.hy の ThreadPerCall)で待ち、他の task を回し続ける —
;;; os-file-handler の内側に置き、外側に scheduled が要る。待つ task が取り消された後に取れた錠は、その場で返す(持ち主の無い錠を残さない)。
;;; offloaded-tree-handler は木の数え(MeasureTree)と消し(RemoveTree)を同じく呼び 1 つに thread 1 本で待つ(agora-redesign #3715 — worker の
;;; 掃除が数千の file の木を数え・消す間、詰まった disk の上でも他の task(worker の調整ループ)を回し続ける)。置き場は offloaded-lock-handler
;;; と同じ(os-file-handler の内側・外側に scheduled)。
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import fcntl)
(import collections.abc [Callable])
(import os)
(import shutil)
(import stat)
(import tempfile)
(import pathlib [Path])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld DiskUsage StatPath ReadText ReadBytes WriteText WriteBytes
                                         AppendText MakeDirectory ListDirectory WalkTree CopyFile CopyTree RenamePath RemoveTree
                                         AcquireLock ReleaseLock ReadDiskFree ReadDiskUsage MeasureTree LinkFile
                                         CompilePythonSources])
(import doeff_core_effects.python_bytecode [compile-python-sources])
(import doeff_core_effects.offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])

;; offloaded-lock-handler の thread(錠の取り 1 つに thread 1 本 — 同時の数の上限は錠を取りに来る task の数)。
(val LOCK-THREADS (ThreadPerCall))
;; offloaded-tree-handler の thread(木の数え・消し 1 つに thread 1 本)。
(val TREE-THREADS (ThreadPerCall))


(defk failed [path error]
  {:pre [(: path str) (: error OSError)] :post [(: % FileFailed)]}
  "OSError を FileFailed の値にするため。"
  (FileFailed :path path :detail (str error)))


(defk kind-of-mode [mode]
  {:pre [(: mode int)] :post [(: % PathKind)]}
  "stat の mode から種類を読むため。"
  (cond
    (stat.S-ISLNK mode) PathKind.SYMLINK
    (stat.S-ISDIR mode) PathKind.DIRECTORY
    (stat.S-ISREG mode) PathKind.FILE
    True PathKind.OTHER))


(defk stat-path [path follow-symlinks]
  {:pre [(: path str) (: follow-symlinks bool)] :post [(: % (| PathStat FileFailed))]}
  "path の様子を読むため(follow-symlinks = True は symlink を辿る・False は os.lstat・無ければ MISSING)。"
  (try
    (val found (if follow-symlinks (os.stat path) (os.lstat path)))
    (<- kind PathKind (kind-of-mode found.st-mode))
    (PathStat :kind kind :real-path (os.path.realpath path) :size found.st-size :modified found.st-mtime)
    (except [FileNotFoundError]
      (PathStat :kind PathKind.MISSING :real-path (os.path.abspath path) :size 0 :modified 0.0))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk write-content [path content mode replace sync]
  {:pre [(: path str) (: content (| str bytes)) (: mode (| int None)) (: replace bool) (: sync bool)] :post [(: % (| FileFailed None))]}
  "file を書くため(replace = 同じ dir の別名に書いてから置き換える・sync = 閉じる前に fsync)。"
  (val binary (isinstance content bytes))
  (try
    (if replace
        (do (val parent (os.path.dirname (os.path.abspath path)))
            (val staged (tempfile.NamedTemporaryFile :mode (if binary "wb" "w") :dir parent :delete False
                                                     #** (if binary {} {"encoding" "utf-8"})))
            (with [out staged] (.write out content) (when sync (_sync out)))
            (os.replace staged.name path))
        (with [out (open path (if binary "wb" "w") #** (if binary {} {"encoding" "utf-8"}))]
          (.write out content)
          (when sync (_sync out))))
    (when (is-not mode None)
      (os.chmod path mode))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk make-directory [path mode]
  {:pre [(: path str) (: mode (| int None))] :post [(: % (| FileFailed None))]}
  "dir を親ごと作るため(mode は新しく作った最後の dir にだけ与える)。"
  (try
    (val existed (os.path.isdir path))
    (os.makedirs path :exist-ok True)
    (when (and (is-not mode None) (not existed))
      (os.chmod path mode))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk entry-of [path name]
  {:pre [(: path str) (: name str)] :post [(: % DirEntry)]}
  "dir の中の 1 つを DirEntry にするため(symlink は辿らない)。"
  (<- kind PathKind (kind-of-mode (. (os.lstat path) st-mode)))
  (DirEntry :name name :kind kind))


(defk list-directory [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の直下を名の順に並べるため。"
  (try
    (val names (sorted (os.listdir path)))
    (val entries [])
    (for [name names]
      (<- entry DirEntry (entry-of (os.path.join path name) name))
      (.append entries entry))
    (tuple entries)
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk walk-tree [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の下の全部を相対 path の順に並べるため(symlink の dir へは入らない)。"
  (try
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (val entries [])
    (for [#(root dirs files) (os.walk path)]
      (for [name (+ dirs files)]
        (val full (os.path.join root name))
        (<- entry DirEntry (entry-of full (.replace (os.path.relpath full path) os.sep "/")))
        (.append entries entry)))
    (tuple (sorted entries :key (fn [e] e.name)))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk remove-tree [path]
  {:pre [(: path str)] :post [(: % (| FileFailed None))]}
  "file か dir を中身ごと消すため(symlink は symlink だけを消す)。"
  (try
    (if (and (os.path.isdir path) (not (os.path.islink path)))
        (shutil.rmtree path)
        (os.unlink path))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk acquire-lock [path]
  {:pre [(: path str)] :post [(: % (| LockHeld FileFailed))]}
  "錠の file を排他で取るため(取れるまで待つ・手札 = 開いた file の番号)。"
  (try
    (val fd (os.open path (| os.O-RDWR os.O-CREAT) 0o644))
    (fcntl.flock fd fcntl.LOCK-EX)
    (LockHeld :path path :token fd)
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk guarded [path action]
  {:pre [(: path str) (: action (get Callable #([] object)))] :post [(: % (| FileFailed None))]}
  "答えの無い操作 1 つを走らせ、OSError を FileFailed にするため。"
  (try
    (action)
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk read-file [path binary limit offset]
  {:pre [(: path str) (: binary bool) (: limit (| int None)) (: offset int)] :post [(: % (| str bytes FileFailed))]}
  "file の中身を読むため(text は UTF-8・読めない byte は置き換え・bytes は位置 offset から先の limit byte だけ — None = 終わりまで)。"
  (try
    (cond
      (not binary) (.read-text (Path path) :encoding "utf-8" :errors "replace")
      (and (is limit None) (= offset 0)) (.read-bytes (Path path))
      True (with [handle (open path "rb")]
             (.seek handle offset)
             (.read handle (if (is limit None) -1 limit))))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defn _sync [handle]  ; defk にできない: 書きの with の中から呼ぶ手続き(Program を返すと実行されない)
  (.flush handle)
  (os.fsync (.fileno handle)))


(defn _append [path text sync]  ; defk にできない: guarded に渡す callback
  (with [handle (open path "a" :encoding "utf-8")]
    (.write handle text)
    (when sync (_sync handle))))


(defn _release [held]  ; defk にできない: guarded に渡す callback
  (try (fcntl.flock held.token fcntl.LOCK-UN) (finally (os.close held.token))))


(defk disk-free [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "path を含む file system の空きを読むため(無い path は在る親で測る)。"
  (var probe (Path path))
  (while (not (.exists probe)) (:= probe probe.parent))
  (try (. (shutil.disk-usage probe) free)
       (except [error OSError] (FileFailed :path path :detail (str error)))))


(defk disk-usage [path]
  {:pre [(: path str)] :post [(: % (| DiskUsage FileFailed))]}
  "path を含む file system の総量と空きを読むため(無い path は在る親で測る)。"
  (var probe (Path path))
  (while (not (.exists probe)) (:= probe probe.parent))
  (try (let [usage (shutil.disk-usage probe)] (DiskUsage :total usage.total :free usage.free))
       (except [error OSError] (FileFailed :path path :detail (str error)))))


(defk measure-tree [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "dir の下の file の大きさの合計を測るため(symlink は辿らずリンク自身の大きさ・hardlink は重ねて数える・測る間に消えた file は数えない)。"
  (try
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (var total 0)
    (for [#(root _ files) (os.walk path)]
      (for [name files]
        (:= total (+ total (try (. (os.lstat (os.path.join root name)) st-size) (except [OSError] 0))))))
    total
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defhandler os-file-handler
  ;; 本物の file system(頭の註)。
  (StatPath [path follow-symlinks]
    (<- answer (stat-path path follow-symlinks))
    (resume answer))
  (ReadText [path]
    (<- answer (read-file path False None 0))
    (resume answer))
  (ReadBytes [path limit offset]
    (<- answer (read-file path True limit offset))
    (resume answer))
  (WriteText [path text mode replace sync]
    (<- answer (write-content path text mode replace sync))
    (resume answer))
  (WriteBytes [path content mode replace sync]
    (<- answer (write-content path content mode replace sync))
    (resume answer))
  (AppendText [path text sync]
    (<- answer (guarded path (fn [] (_append path text sync))))
    (resume answer))
  (MakeDirectory [path mode]
    (<- answer (make-directory path mode))
    (resume answer))
  (ListDirectory [path]
    (<- answer (list-directory path))
    (resume answer))
  (WalkTree [path]
    (<- answer (walk-tree path))
    (resume answer))
  (CopyFile [source target]
    (<- answer (guarded source (fn [] (shutil.copyfile source target))))
    (resume answer))
  (LinkFile [source target]
    (<- answer (guarded source (fn [] (os.link source target))))
    (resume answer))
  (CompilePythonSources [tree items jobs roots]
    (resume (compile-python-sources tree items jobs roots)))
  (CopyTree [source target]
    (<- answer (guarded source (fn [] (shutil.copytree source target :symlinks True :dirs-exist-ok True))))
    (resume answer))
  (RenamePath [source target]
    (<- answer (guarded source (fn [] (os.replace source target))))
    (resume answer))
  (RemoveTree [path]
    (<- answer (remove-tree path))
    (resume answer))
  (AcquireLock [path]
    (<- answer (acquire-lock path))
    (resume answer))
  (ReleaseLock [held]
    (<- answer (guarded held.path (fn [] (_release held))))
    (resume answer))
  (ReadDiskFree [path]
    (<- answer (disk-free path))
    (resume answer))
  (ReadDiskUsage [path]
    (<- answer (disk-usage path))
    (resume answer))
  (MeasureTree [path]
    (<- answer (measure-tree path))
    (resume answer)))


(deff release-abandoned [answer]  ; defk にできない: Executor の thread で回す後始末の入口(offloaded の abandon — VM の外)
  {:pre [(: answer (| LockHeld FileFailed))] :post [(: % None)]}
  "待つ task が取り消された後に取れた錠を返すため(取れなかった答えは何も持たない)。"
  (when (isinstance answer LockHeld)
    (_release answer))
  None)


(defhandler offloaded-lock-handler
  ;; 錠の取りだけを thread で待つ答え手(頭の註)。他の file の effect は外側の os-file-handler が答える。
  (AcquireLock [path]
    (<- answer (| LockHeld FileFailed) (offloaded LOCK-THREADS (fn [] (run-detached (acquire-lock path))) release-abandoned))
    (resume answer)))


(defhandler offloaded-tree-handler
  ;; 木の数え(MeasureTree)と消し(RemoveTree)だけを thread で待つ答え手(頭の註)。他の file の effect は外側の os-file-handler が答える。
  ;; 待つ task が取り消された後の答えは何も持たない(数えは数・消しは終わった印)— 後始末は要らない。
  (MeasureTree [path]
    (<- answer (| int FileFailed) (offloaded TREE-THREADS (fn [] (run-detached (measure-tree path))) keep-nothing))
    (resume answer))
  (RemoveTree [path]
    (<- answer (| FileFailed None) (offloaded TREE-THREADS (fn [] (run-detached (remove-tree path))) keep-nothing))
    (resume answer)))
