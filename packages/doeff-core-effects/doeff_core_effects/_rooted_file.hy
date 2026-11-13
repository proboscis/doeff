;;; rooted-file-handler: a handler that moves the generic file system effects (file_effects.hy) under a single dir (root) of the outer store and re-performs them.
;;; Same as a container volume mount: the / seen from inside is the outer root. It holds no store -- the one that answers is the outer
;;; handler (memory-file-handler or os-file-handler); this handler only remaps paths (it knows no business logic).
;;;
;;; Use: show one memory store as separate per-node, per-pod file systems (in a simulated world, the administrator's hands and a pod's reads touch the same store).
;;; Inner paths accept absolute paths only, and `..` stops at the inner / (it never escapes the root).
;;;
;;; Remapping:
;;;   Request  path, source and target become root + the inner path (ReleaseLock uses the lock's path).
;;;   Answer   the real-path of PathStat, the path of LockHeld, and the path of FileFailed plus any path inside its reason text are mapped back to the inner path (root is never shown inside).
;;;            The name of DirEntry and EntryStat is a name from the dir or a relative path, so it is left unchanged.
;;; Effects that carry no path (ReadMemoryFiles etc.) are passed to the outer handler untouched.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import posixpath)
(import dataclasses [replace])
(import doeff [EffectBase])
(import doeff_core_effects._file_effects [FileFailed PathStat LockHeld LockBusy Appended StatPath ReadText ReadBytes WriteText WriteBytes AppendText
                                         AppendBytes TruncateFile SyncDirectory
                                         MakeDirectory ListDirectory WalkTree StatTree CopyFile CopyTree RenamePath RemoveTree AcquireLock TryLock
                                         ReleaseLock ReadDiskFree DiskUsage ReadDiskUsage MeasureTree LinkFile
                                         LinkFiles CompilePythonSources MakeSymlink])

;; Effects with one path, and effects with two paths (copy source and copy target).
(val PATH-EFFECTS #(StatPath ReadText ReadBytes WriteText WriteBytes AppendText AppendBytes TruncateFile SyncDirectory MakeDirectory
                    ListDirectory WalkTree StatTree RemoveTree AcquireLock TryLock ReadDiskFree ReadDiskUsage MeasureTree))
(val MOVE-EFFECTS #(CopyFile LinkFile CopyTree RenamePath))
;; Sum of the answer types of the file effects (failure, status, lock, total and free space, append result, contents, listing, free-space and size bytes, a write with no answer).
(val ANSWER (| FileFailed PathStat LockHeld DiskUsage Appended str bytes tuple int None))


(defk outer-path [root path]
  {:pre [(: root str) (: path str)] :post [(: % str)]}
  "Map an inner absolute path to an outer path (under root). `..` stops at the inner / (normpath does not climb above the root)."
  (when (not (and (.startswith root "/") (= root (posixpath.normpath root))))
    (raise (ValueError (.format "rooted-file-handler root must be a normalized absolute path: {!r}" root))))
  (when (not (.startswith path "/"))
    (raise (ValueError (.format "rooted-file-handler accepts absolute paths only: {!r}" path))))
  (val inner (posixpath.normpath path))
  (cond (= inner "/") root
        (= root "/") inner
        True (+ root inner)))


(defk inner-path [root path]
  {:pre [(: root str) (: path str)] :post [(: % str)]}
  "Map an outer path back to an inner path (a path outside root is left as is -- the outer handler never answers outside root)."
  (cond (= root "/") path
        (= path root) "/"
        (.startswith path (+ root "/")) (cut path (len root) None)
        True path))


(defk inner-detail [root detail]
  {:pre [(: root str) (: detail str)] :post [(: % str)]}
  "Map the outer path inside a refusal text back to the inner path (the text is in OSError form -- the path appears as its repr)."
  (if (= root "/")
      detail
      (.replace (.replace detail (+ root "/") "/") (repr root) (repr "/"))))


(defk inner-answer [root answer]
  {:pre [(: root str) (: answer ANSWER)] :post [(: % ANSWER)]}
  "Map an outer answer back to the form seen from inside (an answer with no path is left as is)."
  (match answer
    (FileFailed :path path :detail detail)
    (do (<- inner str (inner-path root path))
        (<- told str (inner-detail root detail))
        (FileFailed :path inner :detail told))
    ;; The field real-path is not mangled into a match keyword spelling, so match by type alone and read the attribute.
    (PathStat)
    (do (<- inner str (inner-path root answer.real-path))
        (replace answer :real-path inner))
    (LockHeld :path held)
    (do (<- inner str (inner-path root held))
        (replace answer :path inner))
    (LockBusy :path busy)
    (do (<- inner str (inner-path root busy))
        (replace answer :path inner))
    _ answer))


(defhandler rooted-file-handler [#^ str root]
  "Move the file system effect's paths under ROOT and re-issue them to the outer handler (so the ROOT dir looks like /)."
  ;; Why it stays an argument: root is the shape of the scenario world (which dir looks like /), and differs per pod within the same set.
  ;; Clause order: clauses matched by type come first, and the EffectBase clause with :when comes last (an EffectBase clause whose :when is false passes to the outer handler without trying later clauses).
  (CopyFile [source target]
    (<- answer (moved root effect))
    (resume answer))
  (LinkFile [source target]
    (<- answer (moved root effect))
    (resume answer))
  (LinkFiles [pairs]
    ;; Both paths of every pair are moved under root; the refusal names the inner path (the same as LinkFile).
    (var outer #())
    (for [#(source target) pairs]
      (<- from str (outer-path root source))
      (<- to str (outer-path root target))
      (:= outer (+ outer #(#(from to)))))
    (<- answer (LinkFiles outer))
    (<- inner ANSWER (inner-answer root answer))
    (resume inner))
  (CompilePythonSources [tree items jobs roots]
    ;; The answer is a relative path inside the tree, so only the tree's path is mapped outward.
    (<- outer str (outer-path root tree))
    (<- answer (CompilePythonSources outer items jobs roots))
    (resume answer))
  (CopyTree [source target]
    (<- answer (moved root effect))
    (resume answer))
  (RenamePath [source target]
    (<- answer (moved root effect))
    (resume answer))
  (MakeSymlink [path target]
    ;; If the link target is an absolute path, it is read from the inner /, so move it outward (a relative target is read from the dir containing the link, so it stays as is).
    (<- outer str (outer-path root path))
    (<- pointed str (if (.startswith target "/") (outer-path root target) (unchanged target)))
    (<- answer (MakeSymlink outer pointed))
    (<- inner ANSWER (inner-answer root answer))
    (resume inner))
  (ReleaseLock [held]
    (<- path str (outer-path root held.path))
    (<- answer (ReleaseLock (replace held :path path)))
    (resume answer))
  (EffectBase [] :when (isinstance effect PATH-EFFECTS)
    (<- path str (outer-path root effect.path))
    (<- answer (replace effect :path path))
    (<- inner ANSWER (inner-answer root answer))
    (resume inner)))


(defk unchanged [target]
  {:pre [(: target str)] :post [(: % str)]}
  "Return a relative link target as is (to make both branches of the if a Program)."
  target)


(defk moved [root request]
  {:pre [(: root str) (: request MOVE-EFFECTS)] :post [(: % ANSWER)]}
  "Move an effect with two paths (copy source and copy target) under root and re-perform it (the answer is mapped back to the inner path)."
  (<- source str (outer-path root request.source))
  (<- target str (outer-path root request.target))
  (<- answer (replace request :source source :target target))
  (<- inner ANSWER (inner-answer root answer))
  inner)
