;;; General-purpose file system effects (consumers = #796 daily full verification, #795 webapp assembly). A foundation vocabulary with no business terms,
;;; at the same level as HttpRequest (http_effects.hy). The handler is swapped per mechanism:
;;;   os-file-handler      the real file system (os_file.hy)
;;;   memory-file-handler  No I/O -- a file store held in a session value (memory_file.hy). It refuses in the same places as the real one (missing parent dir, mixing up
;;;                        file and dir, rename onto a non-empty dir). It also holds symlinks (MakeSymlink).
;;;
;;; A failure is answered as a value (FileFailed -- path and reason), not raised (the caller reads it by type). The success answer per effect:
;;;   StatPath       The kind, real path, size, and mtime of a path. Answer = PathStat (a missing path is kind MISSING -- not a failure). follow-symlinks = False
;;;                  answers kind SYMLINK without following the symlink (os.lstat -- even if its target is broken)
;;;   ReadText / ReadBytes    The contents of a file (text is UTF-8, unreadable bytes are replaced). Answer = str / bytes. The offset of ReadBytes is the byte position to start
;;;                  reading at (default 0 -- for a reader of an appended-to file that reads only past where it read last time. Beyond the end of the file it is
;;;                  empty bytes); limit reads only limit bytes from offset (None = to the end -- for a reader that needs only the first line of a large file)
;;;   WriteText / WriteBytes  Write a file. replace = True writes under another name and then replaces (so a half-written file is never read). mode is applied after writing. Answer = None
;;;   AppendText     Append to the end of a file (create it if missing). Answer = None
;;;   AppendBytes    Append bytes to the end of a file (create it if missing). sync = True fsyncs after appending and then answers. Answer = Appended (the size in bytes of the file after
;;;                  appending, and the seconds the sync took -- 0.0 for sync = False. The writer of an append log uses them to decide the next consolidation and to measure fsync slowness)
;;;   TruncateFile   Truncate an existing file to size bytes (size is at most the current size). sync = True fsyncs after truncating. Answer = None.
;;;                  For a log reader that discards the last unreadable line (it keeps the earlier part as is, without recreating the file)
;;;   SyncDirectory  Flush changes to a dir (creation and renaming of files in it) to disk (open the dir and fsync). Answer = None. The replace of WriteBytes (replace = True,
;;;                  sync = True) only flushes the contents and then replaces, and whether the replaced name survives a machine stop is decided only after the dir is flushed --
;;;                  a writer that may proceed with another write after the replace (a place that stores a snapshot and then empties the log) fires this afterwards
;;;   (The three sync = True of the writes flush the contents to disk before answering (fsync -- before the replace in the case of replace). For a writer whose answered contents
;;;    must not be lost to a machine stop. The memory store has nowhere to flush to, so its answer does not depend on sync)
;;;   MakeDirectory  Create a dir together with its parents (it may already exist). mode is applied to the newly created last dir. Answer = None
;;;   ListDirectory  The direct children of a dir. Answer = a tuple of DirEntry (in name order; names that vanished after listing but before their kind was read are skipped -- a copy of the names present when listed)
;;;   WalkTree       Everything under a dir (recursive). Answer = a tuple of DirEntry (name = path relative to the dir, / separated, in listing order)
;;;   StatTree       Everything under a dir (recursive), with details. Answer = a tuple of EntryStat (name is the same as WalkTree; kind, size, and mtime do not follow
;;;                  symlinks -- os.lstat; an entry that vanished after the listing but before its details were read is kind MISSING, like a missing path of StatPath). For a writer that reads
;;;                  the per-file times of a tree in one call (issuing a StatPath per file after WalkTree would emit as many effects as there are files)
;;;   CopyFile       Copy one file (the destination is overwritten). Answer = None
;;;   CompilePythonSources  Bake the sources of a tree (a list of #(relative-path module-name)) into .pyc files of the kind import checks (PEP 552 checked hash) and
;;;                  put them in __pycache__ (the real one runs in parallel across jobs processes; roots = the import roots while baking -- #2463). Answer = a tuple of
;;;                  SourceNotCompiled for those that could not be baked
;;;   LinkFile       Give one file another name (a hard link -- refused if the destination exists, refused across file systems; #2462). Answer = None
;;;   CopyTree       Copy the contents of a dir over the target (the target may exist; same names are overwritten; symlinks stay symlinks). Answer = None
;;;   MakeSymlink    Create a symlink at path pointing to target (same as os.symlink -- refused if path exists; a relative target is read from the dir containing the link;
;;;                  it is created even if the target is missing). Answer = None. For a writer that switches the contents of a dir to another tree in one step (create a link under another name and replace it with RenamePath
;;;                  )
;;;   RenamePath     Rename path (same as os.replace -- an existing destination file is replaced; refused for a non-empty dir; a symlink is not followed: the link itself is
;;;                  moved, replacing). Answer = None
;;;   RemoveTree     Remove a file or dir with its contents (refused if missing; for a symlink only the link is removed). Answer = None
;;;   AcquireLock    Take a lock file exclusively (wait until it can be taken). Answer = LockHeld
;;;   ReleaseLock    Release a held lock. Answer = None
;;;   ReadDiskFree   The free space (bytes) of the file system containing path (a missing path is measured at its existing parent). Answer = int
;;;   ReadDiskUsage  The total and free space (bytes) of the file system containing path (a missing path is measured at its existing parent -- #2504). Answer = DiskUsage
;;;                  Free is the writable amount: Linux = the free space of statvfs; macOS = the free space of statvfs as a floor, plus part of the space macOS frees automatically, counted as the free space of the volume's
;;;                  resource key (darwin_volume.hy)
;;;   MeasureTree    The total size of the files under a dir (bytes; symlinks are not followed and count as the size of the link itself; hardlinks are counted repeatedly -- #2504). Answer = int
;;;
;;; Vocabulary of the memory store (absent from the real file system): MemoryFile / MemoryFiles = the initial shape and the current contents of the store; ReadMemoryFiles = an effect that reads the current contents
;;; (so that checks and scenarios can peek into the store -- only memory-file-handler answers it).
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])


(defenum PathKind FILE DIRECTORY SYMLINK MISSING OTHER)
;; defenum does not accept a docstring, so the explanation of the name exported from the entry point (tests/test_public_names_documented) is placed afterwards.
(setv PathKind.__doc__ "The kind of thing a path points to (file, dir, symlink, missing, other). The value of kind in PathStat and DirEntry.")


(defrecord FileFailed
  "A file system operation was refused or failed. path = the target, detail = the reason (the OSError message, or the equivalent message of the memory store)."
  (#^ str path)
  (#^ str detail))


(defrecord PathStat
  "The state of a path. kind = the kind (for a symlink, what it points to; MISSING if it does not exist), real-path = the absolute path after following, size = bytes, modified = mtime (epoch seconds)."
  (#^ PathKind kind)
  (#^ str real-path)
  (#^ int size)
  (#^ float modified))


(defrecord DirEntry
  "One entry in a dir. name = the name (in WalkTree, the path relative to the dir), kind = the kind (a symlink is not followed: SYMLINK)."
  (#^ str name)
  (#^ PathKind kind))


(defrecord EntryStat
  "The state of one entry under a dir (the answer of StatTree). name = the path relative to the dir (/ separated), kind = the kind (a symlink is not followed: SYMLINK; one that vanished after the listing is
   MISSING), size = bytes, modified = mtime (epoch seconds) -- size and mtime are also those of the link itself for a symlink."
  (#^ str name)
  (#^ PathKind kind)
  (#^ int size)
  (#^ float modified))


(defrecord DiskUsage
  "The total and free space of a file system (bytes -- the answer of ReadDiskUsage)."
  (#^ int total)
  (#^ int free))


(defrecord SourceNotCompiled
  "One source that could not be baked (path = the relative path in the tree, reason = the reason -- the type and message of the exception). The same error appears at import time, so it is not made a failure of baking."
  (#^ str path)
  (#^ str reason))


(defrecord LockHeld
  "A held lock (path = the lock file, token = the handle passed when releasing)."
  (#^ str path)
  (#^ int token))


(defrecord Appended
  "The answer of AppendBytes. size = the size of the file after appending (bytes), synced-seconds = the seconds fsync took (0.0 for sync = False -- the memory store has
   nowhere to flush to, so it is always 0.0)."
  (#^ int size)
  (#^ float synced-seconds))


(defclass [(dataclass :frozen True)] StatPath [(get EffectBase (| PathStat FileFailed))]
  "The state of a path (header note). follow-symlinks = False does not follow a symlink (even a symlink with a broken target is kind SYMLINK)."
  (#^ str path)
  (setv #^ bool follow-symlinks True))


(defclass [(dataclass :frozen True)] ReadText [(get EffectBase (| str FileFailed))]
  "Read text (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadBytes [(get EffectBase (| bytes FileFailed))]
  "Read bytes (header note). offset = the byte position to start reading at (0 or more), limit = only limit bytes from offset (None = to the end)."
  (#^ str path)
  (setv #^ (| int None) limit None)
  (setv #^ int offset 0)
  (defn #^ None __post-init__ [self]  ; cannot be a defk: a check that the dataclass calls at construction (a returned Program would not run)
    (when (or (not (isinstance self.offset int)) (isinstance self.offset bool) (< self.offset 0))
      (raise (ValueError f"ReadBytes.offset must be an int >= 0, got {self.offset !r}")))))


(defclass [(dataclass :frozen True)] WriteText [(get EffectBase (| FileFailed None))]
  "Write text (header note)."
  (#^ str path)
  (#^ str text)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] WriteBytes [(get EffectBase (| FileFailed None))]
  "Write bytes (header note)."
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] AppendText [(get EffectBase (| FileFailed None))]
  "Append text to the end (header note)."
  (#^ str path)
  (#^ str text)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] AppendBytes [(get EffectBase (| Appended FileFailed))]
  "Append bytes to the end (header note)."
  (#^ str path)
  (#^ bytes content)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] TruncateFile [(get EffectBase (| FileFailed None))]
  "Truncate an existing file to size bytes (header note). size is 0 or more and at most the current size."
  (#^ str path)
  (#^ int size)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] SyncDirectory [(get EffectBase (| FileFailed None))]
  "Flush changes to a dir to disk (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] MakeDirectory [(get EffectBase (| FileFailed None))]
  "Create a dir together with its parents (header note)."
  (#^ str path)
  (setv #^ (| int None) mode None))


(defclass [(dataclass :frozen True)] ListDirectory [(get EffectBase (| (get tuple #(DirEntry ...)) FileFailed))]
  "List the direct children of a dir (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] WalkTree [(get EffectBase (| (get tuple #(DirEntry ...)) FileFailed))]
  "List everything under a dir (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] StatTree [(get EffectBase (| (get tuple #(EntryStat ...)) FileFailed))]
  "List everything under a dir with details (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] CopyFile [(get EffectBase (| FileFailed None))]
  "Copy the contents of a file (header note)."
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] CompilePythonSources [(get EffectBase (get tuple #(SourceNotCompiled ...)))]
  "Bake the sources of a tree into .pyc (header note)."
  (#^ str tree)
  (#^ (get tuple #((get tuple #(str str)) ...)) items)
  (setv #^ int jobs 1)
  (setv #^ (get tuple #(str ...)) roots #(".")))


(defclass [(dataclass :frozen True)] LinkFile [(get EffectBase (| FileFailed None))]
  "Give one file another name (header note)."
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] CopyTree [(get EffectBase (| FileFailed None))]
  "Copy the contents of a dir over the target (header note)."
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] MakeSymlink [(get EffectBase (| FileFailed None))]
  "Create a symlink at path pointing to target (header note)."
  (#^ str path)
  (#^ str target))


(defclass [(dataclass :frozen True)] RenamePath [(get EffectBase (| FileFailed None))]
  "Rename path (header note)."
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] RemoveTree [(get EffectBase (| FileFailed None))]
  "Remove a file or dir with its contents (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] AcquireLock [(get EffectBase (| LockHeld FileFailed))]
  "Take a lock exclusively (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] ReleaseLock [(get EffectBase (| FileFailed None))]
  "Release a held lock (header note)."
  (#^ LockHeld held))


(defclass [(dataclass :frozen True)] ReadDiskFree [(get EffectBase (| int FileFailed))]
  "Read the free space of the file system containing path (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadDiskUsage [(get EffectBase (| DiskUsage FileFailed))]
  "Read the total and free space of the file system containing path (header note)."
  (#^ str path))


(defclass [(dataclass :frozen True)] MeasureTree [(get EffectBase (| int FileFailed))]
  "Measure the total size of the files under a dir (header note)."
  (#^ str path))


(defrecord MemoryFile
  "One file of the memory store (path = the absolute path, content = the bytes of the contents, mode = the mode given, or None)."
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None))


(defrecord MemoryLink
  "One symlink of the memory store (path = the absolute path of the link, target = the target as written -- if relative, read from the dir containing the link)."
  (#^ str path)
  (#^ str target))


(defrecord MemoryFiles
  "The contents of the memory store (files = a sequence of files, dirs = a sequence of absolute dir paths -- the root / is implicit, links = a sequence of symlinks, locks = the paths of locks currently held,
   free = the free bytes answered to ReadDiskFree, total = the total bytes answered to ReadDiskUsage)."
  (setv #^ (get tuple #(MemoryFile ...)) files #())
  (setv #^ (get tuple #(str ...)) dirs #())
  (setv #^ (get tuple #(MemoryLink ...)) links #())
  (setv #^ (get tuple #(str ...)) locks #())
  (setv #^ int free (** 2 40))
  (setv #^ int total (** 2 41)))


(defk file-done [request]
  {:tp [A] :pre [(: request (of EffectBase (| A FileFailed)))] :post [(: % A)] :tags {:context "file-system" :role "foundation"}}
  "Emits one file system effect and raises a refusal (FileFailed) as OSError, returning only the success answer (so that a writer or reader that cannot continue after a failure
   fails with the same exception type as when it called os directly)."
  (<- answer request)
  (when (isinstance answer FileFailed)
    (raise (OSError (.format "{}: {}" answer.path answer.detail))))
  answer)


(defclass [(dataclass :frozen True)] ReadMemoryFiles [(get EffectBase MemoryFiles)]
  "Read the current contents of the memory store (MemoryFiles) (header note).")
