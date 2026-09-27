;;; 汎用の file system の effect(file_effects.hy)の I/O なしの答え手 memory-file-handler(agora-redesign #802 便 1)。置き場(MemoryFiles)を
;;; session の値に持ち、本物の file system と同じ所で断る(FileFailed の detail も OSError の文の形):
;;;   親の dir が無い(No such file or directory)・途中や先が file(Not a directory)・dir へ書く / 読む(Is a directory)・中身の在る dir への
;;;   rename(Directory not empty)・dir を自分の下へ rename(Invalid argument)・無い path を消す。
;;; symlink は持たない(PathKind.SYMLINK は出ない — StatPath の follow-symlinks = False も True と同じ答え)。錠は待てない(同じ錠を 2 度取ると断る — 1 つの VM の中の筋書きで待つと止まるため)。
;;; path は絶対 path だけを受ける(相対 path は呼び手の誤り — ValueError)。業務を知らない: 初めの中身は呼び手が渡す。
;;; ReadMemoryFiles で今の中身を読める(検と筋書きが置き場を覗く口)。session の値の置き場(doeff_core_effects の state)は外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(import posixpath)
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath
                                         ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree CopyFile
                                         CopyTree RenamePath RemoveTree AcquireLock ReleaseLock])

;; 置き場の根と、断りの文(OSError の文と同じ形)。
(val ROOT "/")
(val NO-ENTRY "[Errno 2] No such file or directory")
(val NOT-DIRECTORY "[Errno 20] Not a directory")
(val IS-DIRECTORY "[Errno 21] Is a directory")
(val EXISTS "[Errno 17] File exists")
(val NOT-EMPTY "[Errno 39] Directory not empty")
(val INVALID "[Errno 22] Invalid argument")
(val LOCKED "[Errno 11] Resource temporarily unavailable")


(defk refused [reason path]
  {:pre [(: reason str) (: path str)] :post [(: % FileFailed)]}
  "断りの値を OSError の文の形で作るため。"
  (FileFailed :path path :detail (.format "{}: {!r}" reason path)))


(defk refused-move [reason source target]
  {:pre [(: reason str) (: source str) (: target str)] :post [(: % FileFailed)]}
  "2 つの path の操作(rename)の断りを OSError の文の形で作るため(os.replace の文と同じ — path は元の側)。"
  (FileFailed :path source :detail (.format "{}: {!r} -> {!r}" reason source target)))


(defk normal [path]
  {:pre [(: path str)] :post [(: % str)]}
  "置き場の path を 1 つの綴りにそろえるため(絶対 path だけを受ける)。"
  (when (not (.startswith path "/"))
    (raise (ValueError (.format "memory の置き場は絶対 path だけを受ける: {!r}" path))))
  (posixpath.normpath path))


(defk kind-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % PathKind)]}
  "置き場の path の種類を読むため。"
  (cond
    (any (gfor f store.files (= f.path path))) PathKind.FILE
    (or (= path ROOT) (in path store.dirs)) PathKind.DIRECTORY
    True PathKind.MISSING))


(defn under? [#^ str path #^ str root]  ; defk にできない: 内包表記と条件の中で呼ぶ述語(Program を返すと真偽にならない)
  "path が root の下(root 自身は含まない)かを読むため。"
  (.startswith path (if (= root ROOT) ROOT (+ root "/"))))


(defk parent-refusal [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| FileFailed None))]}
  "path を作る前に、親が dir であることを確かめるため(途中に file が在れば Not a directory・無ければ No such file)。"
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
  "dir を親ごと足した置き場を作るため(先が file なら File exists・途中が file なら Not a directory)。"
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
  (MemoryFiles :files store.files :dirs (+ store.dirs (tuple (reversed added))) :locks store.locks))


(defk with-file [store path content mode]
  {:pre [(: store MemoryFiles) (: path str) (: content bytes) (: mode (| int None))] :post [(: % (| MemoryFiles FileFailed))]}
  "file を書いた(置き換えた)置き場を作るため。"
  (<- parent (parent-refusal store path))
  (when (is-not parent None)
    (return parent))
  (<- kind PathKind (kind-in store path))
  (when (= kind PathKind.DIRECTORY)
    (<- answer FileFailed (refused IS-DIRECTORY path))
    (return answer))
  (MemoryFiles :files (+ (tuple (gfor f store.files :if (!= f.path path) f)) #((MemoryFile :path path :content content :mode mode)))
               :dirs store.dirs :locks store.locks))


(defk content-of [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| bytes FileFailed))]}
  "file の中身を読むため。"
  (<- kind PathKind (kind-in store path))
  (match kind
    PathKind.FILE (next (gfor f store.files :if (= f.path path) f.content))
    PathKind.DIRECTORY (do (<- answer FileFailed (refused IS-DIRECTORY path)) answer)
    _ (do (<- parent (parent-refusal store path))
          (<- missing FileFailed (refused NO-ENTRY path))
          (if (is-not parent None) parent missing))))


(defk dir-refusal [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| FileFailed None))]}
  "path が dir であることを確かめるため。"
  (<- kind PathKind (kind-in store path))
  (match kind
    PathKind.DIRECTORY None
    PathKind.FILE (do (<- answer FileFailed (refused NOT-DIRECTORY path)) answer)
    _ (do (<- answer FileFailed (refused NO-ENTRY path)) answer)))


(defk entries-below [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % tuple)]}
  "dir の下の全部を相対 path の順に並べるため(呼び手が dir であることを確かめる)。"
  (val found (+ (lfor d store.dirs :if (and (!= d ROOT) (under? d path)) #(d PathKind.DIRECTORY))
                (lfor f store.files :if (under? f.path path) #(f.path PathKind.FILE))))
  (tuple (sorted (gfor #(full kind) found (DirEntry :name (posixpath.relpath full path) :kind kind)) :key (fn [e] e.name))))


(defk list-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| tuple FileFailed))]}
  "dir の直下を名の順に並べるため。"
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  (tuple (gfor e below :if (not-in "/" e.name) e)))


(defk without-tree [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % MemoryFiles)]}
  "path とその下の全部を除いた置き場を作るため。"
  (MemoryFiles :files (tuple (gfor f store.files :if (not (or (= f.path path) (under? f.path path))) f))
               :dirs (tuple (gfor d store.dirs :if (not (or (= d path) (under? d path))) d))
               :locks store.locks))


(defk remove-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| MemoryFiles FileFailed))]}
  "file か dir を中身ごと消した置き場を作るため(無ければ断る)。"
  (<- kind PathKind (kind-in store path))
  (when (= kind PathKind.MISSING)
    (<- answer FileFailed (refused NO-ENTRY path))
    (return answer))
  (<- removed MemoryFiles (without-tree store path))
  removed)


(defk copy-tree-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "dir の中身を target の下へ重ねて写した置き場を作るため(同じ名は上書き)。"
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
    (if (= entry.kind PathKind.DIRECTORY)
        (do (<- grown-dir (with-dirs current dest))
            (:= step grown-dir))
        (do (<- content (content-of store (posixpath.join source entry.name)))
            (<- written (with-file current dest content None))
            (:= step written)))
    (when (isinstance step FileFailed)
      (return step))
    (:= current step))
  current)


(defk moved [path source target]
  {:pre [(: path str) (: source str) (: target str)] :post [(: % str)]}
  "source の下の path を target の下へ付け替えるため。"
  (if (= path source) target (+ target (cut path (len source) None))))


(defk rename-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "path の名を変えた置き場を作るため(os.replace と同じ所で断る)。"
  (<- from-kind PathKind (kind-in store source))
  (<- to-kind PathKind (kind-in store target))
  (<- parent (parent-refusal store target))
  (cond
    (= from-kind PathKind.MISSING) (do (<- answer FileFailed (refused-move NO-ENTRY source target)) (return answer))
    (is-not parent None) (do (<- answer FileFailed (refused-move NO-ENTRY source target)) (return answer))
    (= source target) (return store)
    (and (= from-kind PathKind.FILE) (= to-kind PathKind.DIRECTORY)) (do (<- answer FileFailed (refused-move IS-DIRECTORY source target)) (return answer))
    (and (= from-kind PathKind.DIRECTORY) (= to-kind PathKind.FILE)) (do (<- answer FileFailed (refused-move NOT-DIRECTORY source target)) (return answer))
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
  (MemoryFiles :files (tuple files) :dirs (tuple dirs) :locks cleared.locks))


(defk return-path [path]
  {:pre [(: path str)] :post [(: % str)]}
  "付け替えない path をそのまま返すため(if の両枝を Program にそろえる)。"
  path)


(defk stat-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % PathStat)]}
  "path の様子を読むため(mtime は持たないので 0)。"
  (<- kind PathKind (kind-in store path))
  (val size (if (= kind PathKind.FILE) (len (next (gfor f store.files :if (= f.path path) f.content))) 0))
  (PathStat :kind kind :real-path path :size size :modified 0.0))


(defhandler memory-file-handler [#^ MemoryFiles initial]
  ;; 引数に残す理由: 初めの中身は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (session var store initial)
  (StatPath [path follow-symlinks]
    (<- at str (normal path))
    (<- answer PathStat (stat-in store at))
    (resume answer))
  (ReadText [path]
    (<- at str (normal path))
    (<- answer (content-of store at))
    (resume (if (isinstance answer bytes) (.decode answer "utf-8" "replace") answer)))
  (ReadBytes [path]
    (<- at str (normal path))
    (<- answer (content-of store at))
    (resume answer))
  (WriteText [path text mode replace]
    (<- at str (normal path))
    (<- answer (with-file store at (.encode text "utf-8") mode))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (WriteBytes [path content mode replace]
    (<- at str (normal path))
    (<- answer (with-file store at content mode))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (AppendText [path text]
    (<- at str (normal path))
    (<- before (content-of store at))
    (<- answer (with-file store at (+ (if (isinstance before bytes) before b"") (.encode text "utf-8")) None))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (MakeDirectory [path mode]
    (<- at str (normal path))
    (<- answer (with-dirs store at))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (ListDirectory [path]
    (<- at str (normal path))
    (<- answer (list-in store at))
    (resume answer))
  (WalkTree [path]
    (<- at str (normal path))
    (<- refusal (dir-refusal store at))
    (if (is-not refusal None)
        (resume refusal)
        (do (<- below tuple (entries-below store at))
            (resume below))))
  (CopyFile [source target]
    (<- from str (normal source))
    (<- to str (normal target))
    (<- content (content-of store from))
    (if (isinstance content FileFailed)
        (resume content)
        (do (<- answer (with-file store to content None))
            (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))))
  (CopyTree [source target]
    (<- from str (normal source))
    (<- to str (normal target))
    (<- answer (copy-tree-in store from to))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (RenamePath [source target]
    (<- from str (normal source))
    (<- to str (normal target))
    (<- answer (rename-in store from to))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (RemoveTree [path]
    (<- at str (normal path))
    (<- answer (remove-in store at))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (AcquireLock [path]
    (<- at str (normal path))
    (if (in at store.locks)
        (do (<- answer FileFailed (refused LOCKED at))
            (resume answer))
        (do (<- kind PathKind (kind-in store at))
            (<- made (if (= kind PathKind.MISSING) (with-file store at b"" None) (return-store store)))
            (if (isinstance made FileFailed)
                (resume made)
                (do (:= store (MemoryFiles :files made.files :dirs made.dirs :locks (+ made.locks #(at))))
                    (resume (LockHeld :path at :token (len store.locks))))))))
  (ReleaseLock [held]
    (:= store (MemoryFiles :files store.files :dirs store.dirs :locks (tuple (gfor p store.locks :if (!= p held.path) p))))
    (resume None))
  (ReadMemoryFiles []
    (resume store)))


(defk return-store [store]
  {:pre [(: store MemoryFiles)] :post [(: % MemoryFiles)]}
  "置き場をそのまま返すため(if の両枝を Program にそろえる)。"
  store)
