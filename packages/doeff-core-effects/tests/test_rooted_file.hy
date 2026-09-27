(require doeff-hy.macros [defk <- val])

;;; rooted-file-handler(doeff_core_effects.rooted_file — agora-redesign #823 項目 1):
;;;   * 1 つの memory の置き場を root ごとに分けて見せる: 同じ内側の path が root ごとに別の file を指し、書きは root の下に着く
;;;   * 答えの path(PathStat の real-path・FileFailed の path と文・LockHeld の path)は内側の path で返る(root を見せない)
;;;   * 反例: `..` で root の外(隣の root)を読もうとしても内側の / で止まる・相対 path は呼び手の誤りとして断る

(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [FileFailed PathKind PathStat LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath ReadText
                                         WriteText MakeDirectory RenamePath AcquireLock ReleaseLock])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.rooted_file [rooted-file-handler])

;; 2 つの node の置き場(同じ内側の path /etc/x が node ごとに違う中身)。
(val INITIAL (MemoryFiles :files #((MemoryFile :path "/nodes/a/etc/x" :content b"a")
                                   (MemoryFile :path "/nodes/b/etc/x" :content b"b"))
                          :dirs #("/nodes" "/nodes/a" "/nodes/a/etc" "/nodes/b" "/nodes/b/etc")))


(defn rooted [root program]
  "置き場 1 つの上に root の見え方を 1 つ被せて走らせる(外側が先 — state・置き場・root)。"
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
  ;; 書きの答えはどれも None(断りの値 FileFailed を黙って捨てない)。
  (assert (= writes #(None None None)) writes)
  (assert (= stat.kind PathKind.FILE))
  (assert (= stat.real-path "/var/run/token2") stat)
  (assert (= paths #("/nodes/a/etc/x" "/nodes/a/var/run/token2" "/nodes/b/etc/x")) paths))


(defk escape []
  {:pre [] :post [(: % (| str FileFailed))]}
  (<- answer (ReadText "/../b/etc/x"))
  answer)


(defn test-dot-dot-stops-at-the-inner-root []
  ;; 反例: 隣の root(/nodes/b)を .. で読もうとしても、内側の / で止まって /nodes/a/b/etc/x(無い)を読む。
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
  (with [(pytest.raises Exception :match "絶対 path")]
    (rooted "/nodes/a" (relative))))
