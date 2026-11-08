;;; memory-file-handler: the no-I/O responder for the generic file system effects (file_effects.hy). It keeps the store (MemoryFiles)
;;; as a session value and refuses in the same places as a real file system (the FileFailed detail also takes the form of an OSError message):
;;;   missing parent dir (No such file or directory); a file in the middle or at the end of a path (Not a directory); writing to / reading a dir (Is a directory); rename onto a
;;;   non-empty dir (Directory not empty); renaming a dir under itself (Invalid argument); removing a missing path.
;;; It holds symlinks (MakeSymlink): as with the real thing, reads, writes, listings, walks and locks follow a path's symlinks, while RenamePath,
;;; RemoveTree, LinkFile, MakeSymlink and the write-replace (replace = True) do not follow the last one and act on the link itself; StatPath with
;;; follow-symlinks = False answers SYMLINK. A link inside a dir shows up as SYMLINK in listings and walks, and a walk does not enter the link's target. The path in a refusal is
;;; the path the caller passed (not the followed target). When the number of hops exceeds 40 it refuses with ELOOP, as the real thing does. A lock waits until it can be taken, as the real thing does
;;; (it waits via the scheduler's CreatePromise / Wait only when the lock is held, so a concurrent scenario needs a scheduler outside it; taking the same lock twice in a single
;;; scenario with no scheduler is an error that hangs, just like the real thing waiting on itself).
;;; Paths must be absolute (a relative path is a caller error: ValueError). It knows nothing of the business domain: the caller passes the initial contents.
;;; ReadMemoryFiles reads the current contents (the entry point for tests and scenarios to peek at the store). The session-value store (the state of doeff_core_effects) is needed outside it.
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import posixpath)
(import collections.abc [Callable])
(import dataclasses [replace :as with-fields])
(import doeff_core_effects._scheduler [CreatePromise CompletePromise Wait])
(import doeff_core_effects._file_effects [PathKind FileFailed PathStat DirEntry EntryStat LockHeld MemoryFile MemoryLink MemoryFiles ReadMemoryFiles
                                         StatPath MakeSymlink Appended AppendBytes TruncateFile SyncDirectory
                                         ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree StatTree CopyFile
                                         CopyTree RenamePath RemoveTree AcquireLock ReleaseLock ReadDiskFree DiskUsage ReadDiskUsage
                                         MeasureTree LinkFile LinkFiles CompilePythonSources SourceNotCompiled])
(import doeff_core_effects._python_bytecode [compiled-pyc kept-pyc pyc-path])

;; The root of the store, and the refusal messages (the same form as OSError messages).
(val ROOT "/")
(val NO-ENTRY "[Errno 2] No such file or directory")
(val NOT-DIRECTORY "[Errno 20] Not a directory")
(val IS-DIRECTORY "[Errno 21] Is a directory")
(val EXISTS "[Errno 17] File exists")
(val NOT-EMPTY "[Errno 39] Directory not empty")
(val INVALID "[Errno 22] Invalid argument")
(val NOT-PERMITTED "[Errno 1] Operation not permitted")
(val LOOP "[Errno 40] Too many levels of symbolic links")
;; Upper bound on the number of symlinks followed for one path (the same as Linux's MAXSYMLINKS).
(val MAX-HOPS 40)
;; Union of the result types of the file effects (the as-asked that returns a refusal to the passed path takes it -- anything other than a refusal passes through unchanged).
(val ANSWER (| FileFailed PathStat LockHeld MemoryFiles Appended str bytes tuple int None))


(defk refused [reason path]
  {:pre [(: reason str) (: path str)] :post [(: % FileFailed)]}
  "To build a refusal value in the form of an OSError message."
  (FileFailed :path path :detail (.format "{}: {!r}" reason path)))


(defk refused-move [reason source target]
  {:pre [(: reason str) (: source str) (: target str)] :post [(: % FileFailed)]}
  "To build the refusal for a two-path operation (rename) in the form of an OSError message (the same as the os.replace message -- the path is the source side)."
  (FileFailed :path source :detail (.format "{}: {!r} -> {!r}" reason source target)))


(defk normal [path]
  {:pre [(: path str)] :post [(: % str)]}
  "To normalize a store path into a single spelling (only absolute paths are accepted)."
  (when (not (.startswith path "/"))
    (raise (ValueError (.format "the memory store accepts only absolute paths: {!r}" path))))
  (posixpath.normpath path))


(defk kind-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % PathKind)]}
  "To read the kind of a store path (a symlink is not followed and gives SYMLINK -- the same as os.lstat)."
  (cond
    (any (gfor f store.files (= f.path path))) PathKind.FILE
    (or (= path ROOT) (in path store.dirs)) PathKind.DIRECTORY
    (any (gfor l store.links (= l.path path))) PathKind.SYMLINK
    True PathKind.MISSING))


(defk link-at [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| MemoryLink None))]}
  "To read the symlink at a store path (None if there is none)."
  (next (gfor l store.links :if (= l.path path) l) None))


(defk resolved [store path follow-last]
  {:pre [(: store MemoryFiles) (: path str) (: follow-last bool)] :post [(: % (| str FileFailed))]}
  "To build the path with the symlinks along the way followed (with follow-last = True the last one is followed too -- reads and writes. False is for operations that act on the link itself).
   A relative target is read from the dir containing the link. When the number of hops exceeds MAX-HOPS it refuses with ELOOP, as the real thing does (the path in the refusal is the passed path)."
  (var rest (tuple (gfor part (.split path "/") :if part part)))
  (var done ROOT)
  (var hops 0)
  (while rest
    (val here (posixpath.join done (get rest 0)))
    (:= rest (cut rest 1 None))
    (<- link (| MemoryLink None) (link-at store here))
    (if (and (is-not link None) (or rest follow-last))
        (do (:= hops (+ hops 1))
            (when (> hops MAX-HOPS)
              (<- loop FileFailed (refused LOOP path))
              (return loop))
            (val pointed (posixpath.normpath (posixpath.join done link.target)))
            (:= rest (+ (tuple (gfor part (.split pointed "/") :if part part)) rest))
            (:= done ROOT))
        (:= done here)))
  done)


(defk as-asked [answer asked at]
  {:pre [(: answer ANSWER) (: asked str) (: at str)] :post [(: % ANSWER)]}
  "To turn a refusal built with the followed path at back into a refusal for the path asked that the caller passed (a real OSError names the passed path)."
  (if (and (isinstance answer FileFailed) (!= asked at) (= answer.path at))
      (FileFailed :path asked :detail (.replace answer.detail (repr at) (repr asked)))
      answer))


(defn under? [#^ str path #^ str root]  ; cannot be a defk: a predicate called inside a comprehension and a condition (returning a Program would not give a boolean)
  "To read whether path is under root (root itself is not included)."
  (.startswith path (if (= root ROOT) ROOT (+ root "/"))))


(defk parent-refusal [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| FileFailed None))]}
  "Before creating a path, to check that the parent is a dir (a file along the way gives Not a directory; a missing one gives No such file)."
  (val parent (posixpath.dirname path))
  (var current parent)
  (while True
    (<- kind PathKind (kind-in store current))
    (match kind
      PathKind.DIRECTORY (if (= current parent)
                             (return None)
                             (do (<- missing FileFailed (refused NO-ENTRY path)) (return missing)))
      PathKind.FILE (do (<- answer FileFailed (refused NOT-DIRECTORY path)) (return answer))
      _ (:= current (posixpath.dirname current)))))


(defk with-dirs [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with a dir added along with its parents (File exists if the end is a file; Not a directory if a file is along the way)."
  (val added [])
  (var current path)
  (while (!= current ROOT)
    (<- kind PathKind (kind-in store current))
    (when (= kind PathKind.FILE)
      (<- answer FileFailed (refused (if (= current path) EXISTS NOT-DIRECTORY) path))
      (return answer))
    (when (= kind PathKind.MISSING)
      (.append added current))
    (:= current (posixpath.dirname current)))
  (with-fields store :dirs (+ store.dirs (tuple (reversed added)))))


(defk with-file [store path content mode]
  {:pre [(: store MemoryFiles) (: path str) (: content bytes) (: mode (| int None))] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with a file written (replaced)."
  (<- parent (parent-refusal store path))
  (when (is-not parent None)
    (return parent))
  (<- kind PathKind (kind-in store path))
  (when (= kind PathKind.DIRECTORY)
    (<- answer FileFailed (refused IS-DIRECTORY path))
    (return answer))
  ;; A link with the same name is replaced (the write-replace replace = True is called without following the link -- the same as the real os.replace).
  (with-fields store :files (+ (tuple (gfor f store.files :if (!= f.path path) f)) #((MemoryFile :path path :content content :mode mode)))
                     :links (tuple (gfor l store.links :if (!= l.path path) l))))


(defk symlink-in [store path target]
  {:pre [(: store MemoryFiles) (: path str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with a symlink pointing to target added at path (refusing in the same places as os.symlink -- the message has the form of two paths, the target and the link; File exists if path exists;
   No such file if the parent is missing. The target need not exist)."
  (<- parent (parent-refusal store path))
  (<- kind PathKind (kind-in store path))
  (val reason (cond (is-not parent None) (if (in NOT-DIRECTORY parent.detail) NOT-DIRECTORY NO-ENTRY)
                    (!= kind PathKind.MISSING) EXISTS
                    True None))
  (if (is-not reason None)
      (FileFailed :path path :detail (.format "{}: {!r} -> {!r}" reason target path))
      (with-fields store :links (+ store.links #((MemoryLink :path path :target target))))))


(defk link-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with another name target given to the file source (refusing in the same places as os.link -- the message has the form of two paths, the source and the destination). Memory
   contents are immutable values, so giving a name is a copy of the contents (a user who writes by replacement sees no difference from a real link)."
  (<- kind PathKind (kind-in store source))
  (<- target-kind PathKind (kind-in store target))
  (<- parent (parent-refusal store target))
  (var reason None)
  (cond
    (= kind PathKind.MISSING) (:= reason NO-ENTRY)
    (is-not parent None) (:= reason (if (in NOT-DIRECTORY parent.detail) NOT-DIRECTORY NO-ENTRY))
    (!= target-kind PathKind.MISSING) (:= reason EXISTS)
    (= kind PathKind.DIRECTORY) (:= reason NOT-PERMITTED))
  (if (is-not reason None)
      (do (<- answer FileFailed (refused-move reason source target)) answer)
      (do (<- content (content-of store source))
          (<- linked (with-file store target content None))
          linked)))


(defk compile-in-store [store tree items]
  {:pre [(: store MemoryFiles) (: tree str) (: items (get tuple #((get tuple #(str str)) ...)))]
   :post [(: % (get tuple #(MemoryFiles (get tuple #(SourceNotCompiled ...)))))]}
  "To bake the tree source sequentially and put the results in __pycache__, returning the store and a tuple of SourceNotCompiled for what could not be baked (answer = #(store failures)).
   The baking decision is the same as the real thing: kept-pyc (an existing .pyc that matches the current source is not rebaked) and compiled-pyc. Hy source cannot be baked (doeff-hy tells Hy source apart by whether it is a
   file on disk, so memory source is read as Python and becomes a SyntaxError failure)."
  (var current store)
  (var failures #())
  (for [#(rel name) items]
    (val source (posixpath.join tree rel))
    (<- content (content-of current source))
    (<- existing (content-of current (pyc-path source)))
    (var kept False)
    (when (and (isinstance content bytes) (isinstance existing bytes))
      (<- current-pyc bool (kept-pyc source content existing))
      (:= kept current-pyc))
    (if (isinstance content FileFailed)
        (:= failures (+ failures #((SourceNotCompiled :path rel :reason content.detail))))
        (when (not kept)
          (<- compiled (compiled-pyc rel name source content))
          (if (isinstance compiled SourceNotCompiled)
              (:= failures (+ failures #(compiled)))
              (do (val cache (pyc-path source))
                  (<- dirs (with-dirs current (posixpath.dirname cache)))
                  (if (isinstance dirs FileFailed)
                      (:= failures (+ failures #((SourceNotCompiled :path rel :reason dirs.detail))))
                      (do (<- written (with-file dirs cache compiled None))
                          (if (isinstance written FileFailed)
                              (:= failures (+ failures #((SourceNotCompiled :path rel :reason written.detail))))
                              (:= current written)))))))))
  #(current failures))


(defk content-of [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| bytes FileFailed))]}
  "To read the contents of a file."
  (<- kind PathKind (kind-in store path))
  (match kind
    PathKind.FILE (next (gfor f store.files :if (= f.path path) f.content))
    PathKind.DIRECTORY (do (<- answer FileFailed (refused IS-DIRECTORY path)) answer)
    _ (do (<- parent (parent-refusal store path))
          (<- missing FileFailed (refused NO-ENTRY path))
          (if (is-not parent None) parent missing))))


(defk dir-refusal [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| FileFailed None))]}
  "To check that path is a dir."
  (<- kind PathKind (kind-in store path))
  (match kind
    PathKind.DIRECTORY None
    PathKind.FILE (do (<- answer FileFailed (refused NOT-DIRECTORY path)) answer)
    _ (do (<- answer FileFailed (refused NO-ENTRY path)) answer)))


(defk entries-below [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (get tuple #(DirEntry ...)))]}
  "To list everything under a dir in relative-path order (the caller checks that it is a dir). A link is shown as SYMLINK and its target is not entered (the contents of the link target
   are at the target path, so they do not appear here)."
  (val found (+ (lfor d store.dirs :if (and (!= d ROOT) (under? d path)) #(d PathKind.DIRECTORY))
                (lfor f store.files :if (under? f.path path) #(f.path PathKind.FILE))
                (lfor l store.links :if (under? l.path path) #(l.path PathKind.SYMLINK))))
  (tuple (sorted (gfor #(full kind) found (DirEntry :name (posixpath.relpath full path) :kind kind)) :key (fn [e] e.name))))


(defk list-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "To list the direct children of a dir in name order."
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  (tuple (gfor e below :if (not-in "/" e.name) e)))


(defk without-tree [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % MemoryFiles)]}
  "To build the store with path and everything under it removed."
  (with-fields store :files (tuple (gfor f store.files :if (not (or (= f.path path) (under? f.path path))) f))
                 :dirs (tuple (gfor d store.dirs :if (not (or (= d path) (under? d path))) d))
                 :links (tuple (gfor l store.links :if (not (or (= l.path path) (under? l.path path))) l))))


(defk remove-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with a file or dir removed along with its contents (refusing if it does not exist)."
  (<- kind PathKind (kind-in store path))
  (when (= kind PathKind.MISSING)
    (<- answer FileFailed (refused NO-ENTRY path))
    (return answer))
  (<- removed MemoryFiles (without-tree store path))
  removed)


(defk copy-tree-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with the contents of a dir copied over under target (the same names are overwritten)."
  (<- refusal (dir-refusal store source))
  (when (is-not refusal None)
    (return refusal))
  (<- grown (with-dirs store target))
  (when (isinstance grown FileFailed)
    (return grown))
  (var current grown)
  (<- below tuple (entries-below store source))
  (for [entry below]
    (val dest (posixpath.join target entry.name))
    (var step None)
    ;; A link is copied as a link (the same as the real shutil.copytree with symlinks = True).
    (match entry.kind
      PathKind.DIRECTORY (do (<- grown-dir (with-dirs current dest))
                             (:= step grown-dir))
      PathKind.SYMLINK (do (<- link (| MemoryLink None) (link-at store (posixpath.join source entry.name)))
                           (<- copied (symlink-in current dest link.target))
                           (:= step copied))
      _ (do (<- content (content-of store (posixpath.join source entry.name)))
            (<- written (with-file current dest content None))
            (:= step written)))
    (when (isinstance step FileFailed)
      (return step))
    (:= current step))
  current)


(defk moved [path source target]
  {:pre [(: path str) (: source str) (: target str)] :post [(: % str)]}
  "To relocate the paths under source to under target."
  (if (= path source) target (+ target (cut path (len source) None))))


(defk rename-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with the name of path changed (refusing in the same places as os.replace -- a link is not followed: the link itself is moved, and replaces)."
  (<- from-kind PathKind (kind-in store source))
  (<- to-kind PathKind (kind-in store target))
  (<- parent (parent-refusal store target))
  ;; A link is treated like a file (even if it points to a dir, in a rename it is a single name that holds no contents).
  (val single #{PathKind.FILE PathKind.SYMLINK})
  (cond
    (= from-kind PathKind.MISSING) (do (<- answer FileFailed (refused-move NO-ENTRY source target)) (return answer))
    (is-not parent None) (do (<- answer FileFailed (refused-move NO-ENTRY source target)) (return answer))
    (= source target) (return store)
    (and (in from-kind single) (= to-kind PathKind.DIRECTORY)) (do (<- answer FileFailed (refused-move IS-DIRECTORY source target)) (return answer))
    (and (= from-kind PathKind.DIRECTORY) (in to-kind single)) (do (<- answer FileFailed (refused-move NOT-DIRECTORY source target)) (return answer))
    (and (= from-kind PathKind.DIRECTORY) (under? target source)) (do (<- answer FileFailed (refused-move INVALID source target)) (return answer)))
  (when (= to-kind PathKind.DIRECTORY)
    (<- occupied tuple (entries-below store target))
    (when occupied
      (<- answer FileFailed (refused-move NOT-EMPTY source target))
      (return answer)))
  (<- cleared MemoryFiles (without-tree store target))
  (val inside (fn [p] (or (= p source) (under? p source))))
  (val files [])
  (for [f cleared.files]
    (<- new-path str (if (inside f.path) (moved f.path source target) (return-path f.path)))
    (.append files (MemoryFile :path new-path :content f.content :mode f.mode)))
  (val dirs [])
  (for [d cleared.dirs]
    (<- new-dir str (if (inside d) (moved d source target) (return-path d)))
    (.append dirs new-dir))
  ;; A link only has its name relocated (the target spelling stays as written -- the same as the real rename).
  (val links (tuple (gfor l cleared.links
                          (MemoryLink :path (if (inside l.path) (+ target (cut l.path (len source) None)) l.path) :target l.target))))
  (with-fields cleared :files (tuple files) :dirs (tuple dirs) :links links))


(defk return-path [path]
  {:pre [(: path str)] :post [(: % str)]}
  "To return the path that is not relocated as it is (to align both branches of the if to a Program)."
  path)


(defk stat-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % PathStat)]}
  "To read the state of a path (no mtime is held so it is 0; the size of a link is the byte count of the target spelling, the same as the real lstat)."
  (<- kind PathKind (kind-in store path))
  (val size (match kind
              PathKind.FILE (len (next (gfor f store.files :if (= f.path path) f.content)))
              PathKind.SYMLINK (len (.encode (next (gfor l store.links :if (= l.path path) l.target)) "utf-8"))
              _ 0))
  (PathStat :kind kind :real-path path :size size :modified 0.0))


(defhandler memory-file-handler [#^ MemoryFiles initial]
  "A mock responder that answers the file system effects with a memory store (initial contents INITIAL). The current contents can be read with ReadMemoryFiles."
  ;; Reason it stays an argument: the initial contents are a value that differs per scenario (not configuration but the mock world itself).
  (session var store initial)
  ;; The waiters for locks (a list of #(path Promise), in waiting order). Not part of the store contents, so it is not put in MemoryFiles.
  (session var waiters #())
  ;; Whether to follow a path's symlinks depends on the effect (see the header comment): reads, writes, listings, walks and locks follow to the end (on-path True), while operations that act on the link itself
  ;; follow only the part along the way (False). Refusals are answered with the passed path.
  (StatPath [path follow-symlinks]
    ;; Do not name it answer: all clauses expand into a single function, so a typed answer would declare even the answers of other clauses as PathStat.
    (<- stat (| PathStat FileFailed) (on-path store path follow-symlinks stat-in))
    (resume stat))
  (ReadText [path]
    (<- answer (on-path store path True content-of))
    (resume (if (isinstance answer bytes) (.decode answer "utf-8" "replace") answer)))
  (ReadBytes [path limit offset]
    (<- answer (on-path store path True content-of))
    (resume (if (isinstance answer bytes) (cut answer offset (if (is limit None) None (+ offset limit))) answer)))
  ;; A write sync has nowhere to flush to, so it is not read (the answer is the same as the real thing -- see the header comment of file_effects.hy). A replacing write (replace = True) writes to a temporary name and does os.replace, as the real thing does,
  ;; so it replaces a link with a file without following the last link.
  (WriteText [path text mode replace]
    (<- answer (on-path store path (not replace) (fn [s at] (with-file s at (.encode text "utf-8") mode))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (WriteBytes [path content mode replace]
    (<- answer (on-path store path (not replace) (fn [s at] (with-file s at content mode))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (AppendText [path text]
    (<- answer (on-path store path True (fn [s at] (appended-in s at text))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  ;; The sync of appended bytes also has nowhere to flush to, so it is not read, and the seconds spent in fsync are always 0.0 (header comment). The answered size is the size of the file after the append.
  (AppendBytes [path content]
    (<- answer (on-path store path True (fn [s at] (appended-in s at content))))
    (if (isinstance answer FileFailed)
        (resume answer)
        (do (:= store answer)
            (<- stat (| PathStat FileFailed) (on-path store path True stat-in))
            (resume (Appended :size stat.size :synced-seconds 0.0)))))
  (TruncateFile [path size]
    (<- answer (on-path store path True (fn [s at] (truncated-in s at size))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  ;; A dir change has nowhere to be flushed to, so the answer is only whether the dir exists (if it is not a dir it refuses, as the real O_DIRECTORY open does).
  (SyncDirectory [path]
    (<- refusal (on-path store path True dir-refusal))
    (resume refusal))
  (MakeDirectory [path mode]
    (<- answer (on-path store path True with-dirs))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (ListDirectory [path]
    (<- answer (on-path store path True list-in))
    (resume answer))
  (WalkTree [path]
    (<- answer (on-path store path True walk-in))
    (resume answer))
  (StatTree [path]
    (<- answer (on-path store path True stats-in))
    (resume answer))
  (CopyFile [source target]
    (<- content (on-path store source True content-of))
    (if (isinstance content FileFailed)
        (resume content)
        (do (<- answer (on-path store target True (fn [s at] (with-file s at content None))))
            (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))))
  (CompilePythonSources [tree items jobs roots]
    (<- at str (normal tree))
    (<- compiled tuple (compile-in-store store at items))
    (:= store (get compiled 0))
    (resume (get compiled 1)))
  (MakeSymlink [path target]
    (<- answer (on-path store path False (fn [s at] (symlink-in s at target))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (LinkFile [source target]
    (<- answer (on-pair store source target False link-in))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (LinkFiles [pairs]
    ;; The same steps as MakeDirectory of the parent and LinkFile, in order; the links made before the first refusal stay (the same as the real one).
    (var refusal None)
    (for [#(source target) pairs]
      (when (is refusal None)
        (val parent (posixpath.dirname target))
        (var grown store)
        (when parent
          (<- made (on-path store parent True with-dirs))
          (:= grown made))
        (if (isinstance grown FileFailed)
            (:= refusal grown)
            (do (:= store grown)
                (<- linked (on-pair store source target False link-in))
                (if (isinstance linked FileFailed) (:= refusal linked) (:= store linked))))))
    (resume refusal))
  (CopyTree [source target]
    (<- answer (on-pair store source target True copy-tree-in))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (RenamePath [source target]
    (<- answer (on-pair store source target False rename-in))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (RemoveTree [path]
    (<- answer (on-path store path False remove-in))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (AcquireLock [path]
    (<- asked str (normal path))
    (<- at (| str FileFailed) (resolved store asked True))
    (cond
      (isinstance at FileFailed) (resume at)
      (in at store.locks)
      ;; A held lock waits until released, as the real flock does (via the scheduler's Promise -- a free lock answers immediately without touching the scheduler).
      ;; The releasing side (ReleaseLock) hands it to the next waiter while leaving the lock in locks, so the woken waiter becomes the owner as is.
      (do (<- promise (CreatePromise))
          (:= waiters (+ waiters #(#(at promise))))
          (<- (Wait promise.future))
          (resume (LockHeld :path at :token (len store.locks))))
      True
      (do (<- kind PathKind (kind-in store at))
          (<- made (if (= kind PathKind.MISSING) (with-file store at b"" None) (return-store store)))
          (if (isinstance made FileFailed)
              (resume made)
              (do (:= store (with-fields made :locks (+ made.locks #(at))))
                  (resume (LockHeld :path at :token (len store.locks))))))))
  (ReleaseLock [held]
    (val waiting (lfor #(p promise) waiters :if (= p held.path) promise))
    (if waiting
        ;; If there is a waiter, hand the lock to the one who waited first while leaving it in locks (no cutting in line).
        (do (val handed (get waiting 0))
            (:= waiters (tuple (gfor w waiters :if (is-not (get w 1) handed) w)))
            (<- (CompletePromise handed None))
            (resume None))
        (do (:= store (with-fields store :locks (tuple (gfor p store.locks :if (!= p held.path) p))))
            (resume None))))
  (ReadDiskFree [path]
    (resume store.free))
  (ReadDiskUsage [path]
    (resume (DiskUsage :total store.total :free store.free)))
  (MeasureTree [path]
    (<- answer (on-path store path True measured-in))
    (resume answer))
  (ReadMemoryFiles []
    (resume store)))


(defk on-path [store path follow-last op]
  {:pre [(: store MemoryFiles) (: path str) (: follow-last bool) (: op Callable)] :post [(: % ANSWER)]}
  "To perform one store operation op (receiving the store and the followed path) on the result of following the symlinks of path, and return the refusal to the passed path (header comment --
   a real OSError names the passed path). If it cannot be followed (ELOOP), that refusal."
  (<- asked str (normal path))
  (<- at (| str FileFailed) (resolved store asked follow-last))
  (when (isinstance at FileFailed)
    (return at))
  (<- answer (op store at))
  (<- told (as-asked answer asked at))
  told)


(defk on-pair [store source target follow-last op]
  {:pre [(: store MemoryFiles) (: source str) (: target str) (: follow-last bool) (: op Callable)] :post [(: % ANSWER)]}
  "To perform one store operation op on the result of following the symlinks of two paths (source and destination) (the refusal is returned to the source-side path -- the same as the os.replace message)."
  (<- from-asked str (normal source))
  (<- to-asked str (normal target))
  (<- from (| str FileFailed) (resolved store from-asked follow-last))
  (when (isinstance from FileFailed)
    (return from))
  (<- to (| str FileFailed) (resolved store to-asked follow-last))
  (when (isinstance to FileFailed)
    (return to))
  (<- answer (op store from to))
  (<- told (as-asked answer from-asked from))
  told)


(defk appended-in [store path text]
  {:pre [(: store MemoryFiles) (: path str) (: text (| str bytes))] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with text (a str turned into UTF-8 bytes) appended to the end of a file (created if missing)."
  (<- before (content-of store path))
  (val added (if (isinstance text bytes) text (.encode text "utf-8")))
  (<- answer (with-file store path (+ (if (isinstance before bytes) before b"") added) None))
  answer)


(defk truncated-in [store path size]
  {:pre [(: store MemoryFiles) (: path str) (: size int)] :post [(: % (| MemoryFiles FileFailed))]}
  "To build the store with an existing file truncated to size bytes (a missing path or a dir is refused in the same places as content-of; a size larger than the current size is not extended, and
   refuses with Invalid argument to match the refusing side of the real ftruncate extension; the mode is kept)."
  (<- before (content-of store path))
  (when (isinstance before FileFailed)
    (return before))
  (when (or (< size 0) (> size (len before)))
    (<- answer FileFailed (refused INVALID path))
    (return answer))
  (val mode (next (gfor f store.files :if (= f.path path) f.mode)))
  (<- answer (with-file store path (cut before 0 size) mode))
  answer)


(defk walk-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "To list everything under a dir in relative-path order (refusing if it is not a dir)."
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  below)


(defk stats-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| (get tuple #(EntryStat ...)) FileFailed))]}
  "To list everything under a dir with its state, in relative-path order (refusing if it is not a dir; the state is the same as stat-in -- no mtime is held so it is 0; a link is not followed and
   it is the byte count of the target spelling)."
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  (var stats #())
  (for [entry below]
    (<- seen PathStat (stat-in store (posixpath.join path entry.name)))
    (:= stats (+ stats #((EntryStat :name entry.name :kind entry.kind :size seen.size :modified seen.modified)))))
  stats)


(defk measured-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| int FileFailed))]}
  "To measure the total size of files under a dir (a link is not followed and counts the byte count of the target spelling -- the same as the real lstat)."
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (+ (sum (gfor f store.files :if (under? f.path path) (len f.content)))
     (sum (gfor l store.links :if (under? l.path path) (len (.encode l.target "utf-8"))))))


(defk return-store [store]
  {:pre [(: store MemoryFiles)] :post [(: % MemoryFiles)]}
  "To return the store as it is (to align both branches of the if to a Program)."
  store)
