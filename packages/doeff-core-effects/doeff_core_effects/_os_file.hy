;;; os-file-handler, the real answerer of the general file system effects (file_effects.hy). It only calls os, shutil, and fcntl and repacks the values;
;;; it holds no judgment. A failure (OSError) is answered as a FileFailed value.
;;; Reading free space (ReadDiskFree, ReadDiskUsage) picks a reader per platform: Linux = statvfs; macOS = the free space of the volume resource key with the statvfs free space as the floor
;;; (darwin_volume.hy -- statvfs counts the area macOS frees automatically as unusable space).
;;;
;;; Acquiring a lock (AcquireLock) waits until it can be taken (fcntl.flock). os-file-handler waits in the calling thread, so in a process where tasks are lined up on one scheduler,
;;; if another task comes for the same lock while the lock-holding task has moved on to a wait (child process, HTTP), the thread of the scheduler itself
;;; is blocked, and the lock-holding task cannot return to release the lock (found when making the entry of the landing service a Program of one scheduler).
;;; offloaded-lock-handler calls only the lock acquisition and waits with one thread per call (ThreadPerCall in offloaded_call.hy), keeping other tasks running --
;;; it sits inside os-file-handler and needs scheduled outside. A lock acquired after the waiting task was cancelled is released on the spot (no ownerless lock is left).
;;; offloaded-tree-handler likewise calls the tree measuring (MeasureTree), removal (RemoveTree), and stat-carrying walk (StatTree) and waits with one thread per call
;;; (while the worker cleanup measures and removes a tree of thousands of files, other tasks (the worker coordination loop) keep running even on a clogged disk.
;;; The same while the cleanup walks the bytecode store of 100 thousand files). The placement is the same as offloaded-lock-handler (inside os-file-handler,
;;; scheduled outside).
;;; offloaded-file-handler likewise calls the synchronous file effects the tick touches (WriteText, ReadText, StatPath, ListDirectory, MakeDirectory, RenamePath,
;;; ReadDiskFree) and waits with one thread per call (when the main thread went into D state waiting on the disk block layer, the background heartbeat of the same
;;; thread stopped with it. The main body keeps running other tasks during the wait). The placement is the same as offloaded-tree-handler.
;;; On 2026-10-18 it also began to accept the single-file and tree-copy effects (ReadBytes, WriteBytes, AppendText, CopyFile, CopyTree, WalkTree, LinkFile, MakeSymlink,
;;; ReadDiskUsage) (the turn-host of agora read and copied conversation records in the scheduler thread, and all turns stopped on a disk with a shallow
;;; clogged queue). The append-log writers AppendBytes, TruncateFile, and SyncDirectory are accepted likewise. What it does not accept: lock acquisition (offloaded-lock-handler -- needs cleanup that releases the lock after cancellation),
;;; lock release (does not wait on disk), tree measuring, removal, and stat-carrying walk (offloaded-tree-handler -- lowers IO priority), and bytecode building (runs in its own
;;; process group).
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import ctypes)
(import errno)
(import fcntl)
(import collections.abc [Callable])
(import os)
(import platform)
(import shutil)
(import stat)
(import sys)
(import tempfile)
(import time)
(import pathlib [Path])
(import doeff_core_effects._file_effects [PathKind FileFailed PathStat DirEntry EntryStat LockHeld DiskUsage Appended StatPath ReadText ReadBytes
                                         WriteText WriteBytes AppendText AppendBytes TruncateFile SyncDirectory MakeDirectory ListDirectory
                                         WalkTree StatTree CopyFile CopyTree RenamePath RemoveTree
                                         AcquireLock ReleaseLock ReadDiskFree ReadDiskUsage MeasureTree LinkFile
                                         CompilePythonSources MakeSymlink])
(import doeff_core_effects._python_bytecode [compile-python-sources])
(import doeff_core_effects._darwin_volume [darwin-volume-free])
(import doeff_core_effects._offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])

;; Threads of offloaded-lock-handler (one thread per lock acquisition -- the upper limit of concurrency is the number of tasks coming for a lock).
(val LOCK-THREADS (ThreadPerCall))
;; Assembly of the IO priority for ioprio_set (linux/ioprio.h): priority = (class << 13) | level. With IOPRIO_WHO_PROCESS, who = 0 is the calling thread itself.
(val IOPRIO-CLASS-SHIFT 13)
(val IOPRIO-CLASS-IDLE 3)
(val IOPRIO-WHO-PROCESS 1)
;; The syscall number of ioprio_set (platform kind -> number. On a platform not in the table nothing is done).
(val IOPRIO-SET-SYSCALL {"x86_64" 251 "aarch64" 30})


(deff lower-io-priority []  ; cannot be a defk: an entry called at the start of a thread (on-start of ThreadPerCall -- outside the VM)
  {:pre [] :post [(: % bool)]}
  "To set the IO priority of the calling thread to IDLE so the main body's small IO goes through first (mq-deadline of the zeus scheduler orders by priority class -- it does not look at the level,
   so the lowest BE is not effective). Does nothing and returns False on non-Linux, a platform not in the table, or a syscall failure (the cleanup proceeds even if the priority is not lowered)."
  (if (and (= sys.platform "linux") (in (platform.machine) IOPRIO-SET-SYSCALL))
      (try
        (setv libc (ctypes.CDLL None :use-errno True))
        (= 0 (.syscall libc (get IOPRIO-SET-SYSCALL (platform.machine)) IOPRIO-WHO-PROCESS 0
                       (<< IOPRIO-CLASS-IDLE IOPRIO-CLASS-SHIFT)))
        (except [OSError] False))
      False))


;; Threads of offloaded-tree-handler (one thread per tree measure, removal, or stat-carrying walk -- the IO priority is lowered to IDLE so the main body's IO goes through first).
(val TREE-THREADS (ThreadPerCall :on-start lower-io-priority))
;; Threads of offloaded-file-handler (one thread per file read, write, append, copy, stat, listing, dir creation, rename, link, or free-space read, and per tree copy or walk
;; ).
(val FILE-THREADS (ThreadPerCall))


(defk failed [path error]
  {:pre [(: path str) (: error OSError)] :post [(: % FileFailed)]}
  "To turn an OSError into a FileFailed value."
  (FileFailed :path path :detail (str error)))


;; Stat mode type marks (values of stat.S_IFMT) -> kind (read by kind-of-mode and stat-tree -- a mark not in the table is OTHER).
(val KIND-OF-FORMAT {stat.S-IFLNK PathKind.SYMLINK stat.S-IFDIR PathKind.DIRECTORY stat.S-IFREG PathKind.FILE})


(defk kind-of-mode [mode]
  {:pre [(: mode int)] :post [(: % PathKind)]}
  "To read the kind from a stat mode."
  (.get KIND-OF-FORMAT (stat.S-IFMT mode) PathKind.OTHER))


(defk stat-path [path follow-symlinks]
  {:pre [(: path str) (: follow-symlinks bool)] :post [(: % (| PathStat FileFailed))]}
  "To read the stat of a path (follow-symlinks = True follows symlinks; False is os.lstat; MISSING if absent)."
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
  "To write a file (replace = write to another name in the same dir and then replace; sync = fsync before closing)."
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
  "To create a dir with its parents (mode is given only to the last newly created dir)."
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
  "To turn one entry in a dir into a DirEntry (symlinks are not followed)."
  (<- kind PathKind (kind-of-mode (. (os.lstat path) st-mode)))
  (DirEntry :name name :kind kind))


(defk list-directory [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "To list the direct children of a dir in name order. Names that vanished after listing and before the kind is read are skipped (a copy of the names present at listing time -- in a dir like /proc where
   names vanish while listing, one vanished name does not make the whole listing a FileFailed)."
  (try
    (tuple (lfor name (sorted (os.listdir path))
                 :setv seen (try (os.lstat (os.path.join path name)) (except [FileNotFoundError] None))
                 :if (is-not seen None)
                 (DirEntry :name name :kind (.get KIND-OF-FORMAT (stat.S-IFMT seen.st-mode) PathKind.OTHER))))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk walk-tree [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "To list everything under a dir in relative-path order (does not enter symlinked dirs)."
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


(defk stat-tree [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(EntryStat ...)) FileFailed))]}
  "To list everything under a dir in relative-path order with stat (os.lstat -- symlinks are not followed) (does not enter symlinked dirs; what vanished after the listing and before the stat is read
   is, like a path with no StatPath, kind MISSING, size 0, mtime 0). The relative path is computed once per dir (relpath is not called per file even
   for a tree of 100 thousand files). A nonexistent path is refused as nonexistent, and a non-dir path as not a dir (the same refusals as the memory store)."
  (try
    (when (not (os.path.exists path))
      (raise (FileNotFoundError 2 "No such file or directory" path)))
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (val found (lfor #(root dirs files) (os.walk path)
                     :setv base (.replace (os.path.relpath root path) os.sep "/")
                     name (+ dirs files)
                     :setv seen (try (os.lstat (os.path.join root name)) (except [FileNotFoundError] None))
                     (EntryStat :name (if (= base ".") name (+ base "/" name))
                                :kind (if (is seen None) PathKind.MISSING (.get KIND-OF-FORMAT (stat.S-IFMT seen.st-mode) PathKind.OTHER))
                                :size (if (is seen None) 0 seen.st-size)
                                :modified (if (is seen None) 0.0 seen.st-mtime))))
    (tuple (sorted found :key (fn [e] e.name)))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk remove-tree [path]
  {:pre [(: path str)] :post [(: % (| FileFailed None))]}
  "To remove a file or dir with its contents (a symlink removes only the symlink)."
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
  "To take a lock file exclusively (waits until it can be taken; handle = the number of the opened file)."
  (try
    (val fd (os.open path (| os.O-RDWR os.O-CREAT) 0o644))
    (fcntl.flock fd fcntl.LOCK-EX)
    (LockHeld :path path :token fd)
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk guarded [path action]
  {:pre [(: path str) (: action (get Callable #([] object)))] :post [(: % (| FileFailed None))]}
  "To run one operation with no answer and turn an OSError into a FileFailed."
  (try
    (action)
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk read-file [path binary limit offset]
  {:pre [(: path str) (: binary bool) (: limit (| int None)) (: offset int)] :post [(: % (| str bytes FileFailed))]}
  "To read the contents of a file (text is UTF-8 with unreadable bytes replaced; bytes is only limit bytes from position offset -- None = to the end)."
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


(defn _sync [handle]  ; cannot be a defk: a procedure called from inside the write's with (returning a Program would not run it)
  (.flush handle)
  (os.fsync (.fileno handle)))


(defn #^ float _timed-sync [handle sync]  ; cannot be a defk: a procedure called from inside the write's with (returning a Program would not run it)
  "Flush the contents of handle, fsync if sync, and return the seconds taken (0.0 if not sync)."
  (.flush handle)
  (if sync
      (do (setv started (time.monotonic))
          (os.fsync (.fileno handle))
          (- (time.monotonic) started))
      0.0))


(defk append-bytes [path content sync]
  {:pre [(: path str) (: content bytes) (: sync bool)] :post [(: % (| Appended FileFailed))]}
  "To append bytes to the end of a file (created if absent; sync = fsync after appending and put the seconds in the answer)."
  (try
    (with [out (open path "ab")]
      (.write out content)
      (val seconds (_timed-sync out sync))
      (Appended :size (.tell out) :synced-seconds seconds))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk truncate-file [path size sync]
  {:pre [(: path str) (: size int) (: sync bool)] :post [(: % (| FileFailed None))]}
  "To truncate an existing file to size bytes (a size larger than the current size is not extended but refused with Invalid argument; sync = fsync after truncating)."
  (try
    (with [handle (open path "r+b")]
      (when (or (< size 0) (> size (. (os.fstat (.fileno handle)) st-size)))
        (raise (OSError errno.EINVAL (os.strerror errno.EINVAL) path)))
      (.truncate handle size)
      (_timed-sync handle sync))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk sync-directory [path]
  {:pre [(: path str)] :post [(: % (| FileFailed None))]}
  "To flush dir changes (creation and rename of files inside) to disk (open the dir and fsync -- a non-dir path is refused with Not a directory)."
  (try
    (val fd (os.open path (| os.O-RDONLY os.O-DIRECTORY)))
    (try
      (os.fsync fd)
      (finally (os.close fd)))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defn _append [path text sync]  ; cannot be a defk: a callback passed to guarded
  (with [handle (open path "a" :encoding "utf-8")]
    (.write handle text)
    (when sync (_sync handle))))


(defn _release [held]  ; cannot be a defk: a callback passed to guarded
  (try (fcntl.flock held.token fcntl.LOCK-UN) (finally (os.close held.token))))


(defk free-reader-for [system]
  {:pre [(: system str)] :post [(: % (| Callable None))]}
  "To pick the re-reader of free space for the platform (name from platform.system): macOS (Darwin) uses darwin-volume-free, which also counts part of the area macOS
   frees automatically that statvfs reports (see the header comment of darwin_volume.hy); other platforms (Linux) get None = the statvfs free space as is."
  (if (= system "Darwin") darwin-volume-free None))


(defk reread-free [probe floor reader]
  {:pre [(: probe str) (: floor int) (: reader (| Callable None))] :post [(: % int)]}
  "To re-read the statvfs free space floor with the platform's re-reader. When there is no answer, or the answer is smaller than floor, floor stays -- the statvfs free space is the amount that can be written right now without deleting anything,
   so the re-read must not measure less than now."
  (if (is reader None)
      floor
      (do (<- answered (| int None) (reader probe))
          (if (is answered None) floor (max floor answered)))))


(defk disk-usage [path]
  {:pre [(: path str)] :post [(: % (| DiskUsage FileFailed))]}
  "To read the total and free space of the file system containing path (a nonexistent path is measured at the existing parent; the free space is re-read by the platform's re-reader -- reread-free)."
  (var probe (Path path))
  (while (not (.exists probe)) (:= probe probe.parent))
  (val usage (try (shutil.disk-usage probe) (except [error OSError] error)))
  (when (isinstance usage OSError)
    (return (FileFailed :path path :detail (str usage))))
  (<- reader (| Callable None) (free-reader-for (platform.system)))
  (<- free int (reread-free (str probe) usage.free reader))
  (DiskUsage :total usage.total :free free))


(defk disk-free [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "To read the free space of the file system containing path (the free of disk-usage -- a nonexistent path is measured at the existing parent)."
  (<- usage (| DiskUsage FileFailed) (disk-usage path))
  (if (isinstance usage FileFailed) usage usage.free))


(defk measure-tree [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "To measure the total size of the files under a dir (symlinks are not followed and the link itself counts; hardlinks are counted repeatedly; files that vanished during measuring are not counted)."
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
  "Answers the file system effects by calling the real file system (os, shutil, tempfile, fcntl) on the spot."
  ;; The real file system (header comment).
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
  (AppendBytes [path content sync]
    (<- answer (append-bytes path content sync))
    (resume answer))
  (TruncateFile [path size sync]
    (<- answer (truncate-file path size sync))
    (resume answer))
  (SyncDirectory [path]
    (<- answer (sync-directory path))
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
  (StatTree [path]
    (<- answer (stat-tree path))
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
  (MakeSymlink [path target]
    (<- answer (guarded path (fn [] (os.symlink target path))))
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


(deff release-abandoned [answer]  ; cannot be a defk: the entry of cleanup run in an Executor thread (abandon of offloaded -- outside the VM)
  {:pre [(: answer (| LockHeld FileFailed))] :post [(: % None)]}
  "To release a lock acquired after the waiting task was cancelled (an answer that failed to acquire holds nothing)."
  (when (isinstance answer LockHeld)
    (_release answer))
  None)


(defhandler offloaded-lock-handler
  "Answers AcquireLock by waiting for the lock acquisition in another thread (other file effects pass to the outer answerer)."
  ;; An answerer that waits only on lock acquisition in a thread (header comment). Other file effects are answered by the outer os-file-handler.
  (AcquireLock [path]
    (<- answer (| LockHeld FileFailed) (offloaded LOCK-THREADS (fn [] (run-detached (acquire-lock path))) release-abandoned))
    (resume answer)))


(defhandler offloaded-tree-handler
  "Answers MeasureTree, RemoveTree, and StatTree by waiting for the tree measuring, removal, and stat-carrying walk in another thread (other file effects pass to the outer answerer)."
  ;; An answerer that waits only on the tree measuring (MeasureTree), removal (RemoveTree), and stat-carrying walk (StatTree) in a thread (header comment). Other file effects are answered by the outer
  ;; os-file-handler. The answer after the waiting task was cancelled holds nothing (count is a number, removal is a done mark, walk is a list of stats) -- no cleanup is needed.
  (MeasureTree [path]
    (<- answer (| int FileFailed) (offloaded TREE-THREADS (fn [] (run-detached (measure-tree path))) keep-nothing))
    (resume answer))
  (RemoveTree [path]
    (<- answer (| FileFailed None) (offloaded TREE-THREADS (fn [] (run-detached (remove-tree path))) keep-nothing))
    (resume answer))
  (StatTree [path]
    (<- stats (| tuple FileFailed) (offloaded TREE-THREADS (fn [] (run-detached (stat-tree path))) keep-nothing))
    (resume stats)))


(defhandler offloaded-file-handler
  "Answers the synchronous file effects (read, write, append, copy, rename, link, dir creation, etc.) by waiting in another thread (lock and tree effects pass to the outer answerer)."
  ;; An answerer that waits in a thread on the synchronous file effects (the list in the header comment -- everything except lock, tree measuring, removal, stat-carrying walk, and bytecode building). Other file
  ;; effects are answered by the outer answerer (offloaded-lock-handler, offloaded-tree-handler, os-file-handler). The answer after the waiting task was cancelled
  ;; holds nothing (write, append, copy, rename, link, dir creation are a done mark; read is a value) -- no cleanup is needed. Writes and copies that have started run to completion.
  (WriteText [path text mode replace sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (write-content path text mode replace sync))) keep-nothing))
    (resume answer))
  (ReadText [path]
    (<- answer (| str FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (read-file path False None 0))) keep-nothing))
    (resume answer))
  (StatPath [path follow-symlinks]
    (<- answer (| PathStat FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (stat-path path follow-symlinks))) keep-nothing))
    (resume answer))
  (ListDirectory [path]
    (<- answer (| tuple FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (list-directory path))) keep-nothing))
    (resume answer))
  (MakeDirectory [path mode]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (make-directory path mode))) keep-nothing))
    (resume answer))
  (RenamePath [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (os.replace source target))))) keep-nothing))
    (resume answer))
  (ReadDiskFree [path]
    (<- answer (| int FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (disk-free path))) keep-nothing))
    (resume answer))
  (ReadDiskUsage [path]
    (<- answer (| DiskUsage FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (disk-usage path))) keep-nothing))
    (resume answer))
  (ReadBytes [path limit offset]
    (<- answer (| bytes FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (read-file path True limit offset))) keep-nothing))
    (resume answer))
  (WriteBytes [path content mode replace sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (write-content path content mode replace sync))) keep-nothing))
    (resume answer))
  (AppendText [path text sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded path (fn [] (_append path text sync))))) keep-nothing))
    (resume answer))
  (AppendBytes [path content sync]
    (<- answer (| Appended FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (append-bytes path content sync))) keep-nothing))
    (resume answer))
  (TruncateFile [path size sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (truncate-file path size sync))) keep-nothing))
    (resume answer))
  (SyncDirectory [path]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (sync-directory path))) keep-nothing))
    (resume answer))
  (CopyFile [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (shutil.copyfile source target)))))
                                              keep-nothing))
    (resume answer))
  (CopyTree [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS
                                              (fn [] (run-detached (guarded source (fn [] (shutil.copytree source target :symlinks True
                                                                                                            :dirs-exist-ok True)))))
                                              keep-nothing))
    (resume answer))
  (WalkTree [path]
    (<- answer (| tuple FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (walk-tree path))) keep-nothing))
    (resume answer))
  (LinkFile [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (os.link source target))))) keep-nothing))
    (resume answer))
  (MakeSymlink [path target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded path (fn [] (os.symlink target path))))) keep-nothing))
    (resume answer)))
