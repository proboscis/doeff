(require doeff-hy.macros [defk <- val])

;;; rooted-file-handler(doeff_core_effects._rooted_file):
;;;   * Show one memory store separated per root: the same inner path points to a different file per root, and writes land under the root
;;;   * Answer paths (real-path of PathStat, path and message of FileFailed, path of LockHeld) are returned as inner paths (the root is not shown)
;;;   * Counterexample: trying to read outside the root (the neighboring root) with `..` stops at the inner / ; a relative path is refused as the caller's error

(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [FileFailed PathKind PathStat LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath ReadText WriteText MakeDirectory RenamePath AcquireLock ReleaseLock MakeSymlink])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [rooted-file-handler])

;; The stores of 2 nodes (the same inner path /etc/x has different content per node).
(val INITIAL (MemoryFiles :files #((MemoryFile :path "/nodes/a/etc/x" :content b"a")
                                   (MemoryFile :path "/nodes/b/etc/x" :content b"b"))
                          :dirs #("/nodes" "/nodes/a" "/nodes/a/etc" "/nodes/b" "/nodes/b/etc")))


(defn rooted [root program]
  "Run on top of one store with one root view overlaid (outer first -- state, store, root)."
  (run (with_handlers [(state) (memory-file-handler INITIAL) (rooted-file-handler root)] program)))


(defk read-x []
  {:pre [] :post [(: % str)]}
  (<- text str (ReadText "/etc/x"))
  text)


(defn test-each-root-sees-its-own-file []
  (assert (= (rooted "/nodes/a" (read-x)) "a"))
  (assert (= (rooted "/nodes/b" (read-x)) "b")))


(defk write-then-look []
  {:pre [] :post [(: % tuple)]}
  (<- made (MakeDirectory "/var/run"))
  (<- wrote (WriteText "/var/run/token" "a-token"))
  (<- renamed (RenamePath "/var/run/token" "/var/run/token2"))
  (<- stat PathStat (StatPath "/var/run/token2"))
  (<- store MemoryFiles (ReadMemoryFiles))
  #(#(made wrote renamed) stat (tuple (sorted (gfor f store.files f.path)))))


(defn test-writes-land-under-the-root-and-answers-show-inner-paths []
  (setv #(writes stat paths) (rooted "/nodes/a" (write-then-look)))
  ;; All write answers are None (the refusal value FileFailed is not silently discarded).
  (assert (= writes #(None None None)) writes)
  (assert (= stat.kind PathKind.FILE))
  (assert (= stat.real-path "/var/run/token2") stat)
  (assert (= paths #("/nodes/a/etc/x" "/nodes/a/var/run/token2" "/nodes/b/etc/x")) paths))


(defk link-then-read []
  {:pre [] :post [(: % tuple)]}
  (<- absolute (MakeSymlink "/etc/abs" "/etc"))
  (<- relative (MakeSymlink "/etc/rel" "."))
  (<- through-absolute str (ReadText "/etc/abs/x"))
  (<- through-relative str (ReadText "/etc/rel/x"))
  (<- store MemoryFiles (ReadMemoryFiles))
  #(absolute relative through-absolute through-relative (tuple (sorted (gfor l store.links #(l.path l.target))))))


(defn #^ None test-a-symlink-points-inside-the-root []
  ;; The absolute target of a link is a target read from the inner /, so move it under the root (if not moved, it would point to another file under the store's /) --
  ;; a relative target is read from the dir containing the link, so it stays as is.
  (setv #(absolute relative through-absolute through-relative links) (rooted "/nodes/a" (link-then-read)))
  (assert (= #(absolute relative) #(None None)))
  (assert (= #(through-absolute through-relative) #("a" "a")) #(through-absolute through-relative))
  (assert (= links #(#("/nodes/a/etc/abs" "/nodes/a/etc") #("/nodes/a/etc/rel" "."))) links))


(defk escape []
  {:pre [] :post [(: % (| str FileFailed))]}
  (<- answer (ReadText "/../b/etc/x"))
  answer)


(defn test-dot-dot-stops-at-the-inner-root []
  ;; Counterexample: even trying to read the neighboring root (/nodes/b) with .., it stops at the inner / and reads /nodes/a/b/etc/x (nonexistent).
  (setv answer (rooted "/nodes/a" (escape)))
  (assert (isinstance answer FileFailed) answer)
  (assert (= answer.path "/b/etc/x") answer)
  (assert (not-in "/nodes" answer.detail) answer.detail))


(defk locked []
  {:pre [] :post [(: % tuple)]}
  (<- held LockHeld (AcquireLock "/lock"))
  (<- (ReleaseLock held))
  (<- store MemoryFiles (ReadMemoryFiles))
  #(held.path store.locks))


(defn test-a-lock-round-trips-through-the-root []
  (assert (= (rooted "/nodes/a" (locked)) #("/lock" #()))))


(defk relative []
  {:pre [] :post [(: % (| str FileFailed))]}
  (<- answer (ReadText "etc/x"))
  answer)


(defn test-a-relative-path-is-refused []
  (with [(pytest.raises Exception :match "absolute path")]
    (rooted "/nodes/a" (relative))))
