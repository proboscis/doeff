;;; 汎用の file system の effect(file_effects.hy)の I/O なしの答え手 memory-file-handler(agora-redesign #802 便 1)。置き場(MemoryFiles)を
;;; session の値に持ち、本物の file system と同じ所で断る(FileFailed の detail も OSError の文の形):
;;;   親の dir が無い(No such file or directory)・途中や先が file(Not a directory)・dir へ書く / 読む(Is a directory)・中身の在る dir への
;;;   rename(Directory not empty)・dir を自分の下へ rename(Invalid argument)・無い path を消す。
;;; symlink は持たない(PathKind.SYMLINK は出ない — StatPath の follow-symlinks = False も True と同じ答え)。錠は本物と同じく取れるまで待つ
;;; (取られている時だけ scheduler の CreatePromise / Wait で待つので、並行の筋書きは外側に scheduler が要る — scheduler の無い 1 本の
;;; 筋書きで同じ錠を 2 度取るのは、本物の自分待ちと同じく止まる誤り)。
;;; path は絶対 path だけを受ける(相対 path は呼び手の誤り — ValueError)。業務を知らない: 初めの中身は呼び手が渡す。
;;; ReadMemoryFiles で今の中身を読める(検と筋書きが置き場を覗く口)。session の値の置き場(doeff_core_effects の state)は外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import posixpath)
(import dataclasses [replace :as with-fields])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Wait])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryFiles ReadMemoryFiles StatPath
                                         ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree CopyFile
                                         CopyTree RenamePath RemoveTree AcquireLock ReleaseLock ReadDiskFree DiskUsage ReadDiskUsage
                                         MeasureTree LinkFile CompilePythonSources SourceNotCompiled])
(import doeff_core_effects.python_bytecode [compiled-pyc kept-pyc pyc-path])

;; 置き場の根と、断りの文(OSError の文と同じ形)。
(val ROOT "/")
(val NO-ENTRY "[Errno 2] No such file or directory")
(val NOT-DIRECTORY "[Errno 20] Not a directory")
(val IS-DIRECTORY "[Errno 21] Is a directory")
(val EXISTS "[Errno 17] File exists")
(val NOT-EMPTY "[Errno 39] Directory not empty")
(val INVALID "[Errno 22] Invalid argument")
(val NOT-PERMITTED "[Errno 1] Operation not permitted")


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
  (with-fields store :dirs (+ store.dirs (tuple (reversed added)))))


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
  (with-fields store :files (+ (tuple (gfor f store.files :if (!= f.path path) f)) #((MemoryFile :path path :content content :mode mode)))))


(defk link-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "source の file にもう 1 つの名 target を付けた置き場を作るため(os.link と同じ所で断る — 文は元と先の 2 つの path の形)。memory の
   中身は書き換えない値なので、名を付けるのは中身の写し(置き換えで書く使い手には本物のリンクと違いが出ない)。"
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
  "木の source を逐次に焼いて __pycache__ へ置いた置き場と、焼けなかった物の SourceNotCompiled の tuple を返すため(答え = #(置き場 失敗))。
   焼きの判断は本物と同じ kept-pyc(既に在る .pyc が今の source に合えば焼き直さない)と compiled-pyc。Hy の source は焼けない(doeff-hy は
   disk に在る file かで Hy の source を見分けるので、memory の source は Python として読まれ、SyntaxError の失敗になる)。"
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
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (get tuple #(DirEntry ...)))]}
  "dir の下の全部を相対 path の順に並べるため(呼び手が dir であることを確かめる)。"
  (val found (+ (lfor d store.dirs :if (and (!= d ROOT) (under? d path)) #(d PathKind.DIRECTORY))
                (lfor f store.files :if (under? f.path path) #(f.path PathKind.FILE))))
  (tuple (sorted (gfor #(full kind) found (DirEntry :name (posixpath.relpath full path) :kind kind)) :key (fn [e] e.name))))


(defk list-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の直下を名の順に並べるため。"
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  (tuple (gfor e below :if (not-in "/" e.name) e)))


(defk without-tree [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % MemoryFiles)]}
  "path とその下の全部を除いた置き場を作るため。"
  (with-fields store :files (tuple (gfor f store.files :if (not (or (= f.path path) (under? f.path path))) f))
                 :dirs (tuple (gfor d store.dirs :if (not (or (= d path) (under? d path))) d))))


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
  (with-fields cleared :files (tuple files) :dirs (tuple dirs)))


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
  ;; 錠を待つ手(#(path Promise) の列・待った順)。置き場の中身ではないので MemoryFiles には入れない。
  (session var waiters #())
  (StatPath [path follow-symlinks]
    (<- at str (normal path))
    ;; 名を answer にしない: 節は全部 1 つの関数に展開されるので、型を付けた answer は他の節の answer まで PathStat と宣言する。
    (<- stat PathStat (stat-in store at))
    (resume stat))
  (ReadText [path]
    (<- at str (normal path))
    (<- answer (content-of store at))
    (resume (if (isinstance answer bytes) (.decode answer "utf-8" "replace") answer)))
  (ReadBytes [path limit offset]
    (<- at str (normal path))
    (<- answer (content-of store at))
    (resume (if (isinstance answer bytes) (cut answer offset (if (is limit None) None (+ offset limit))) answer)))
  ;; 書きの sync は落とす先が無いので読まない(答えは本物と同じ — file_effects.hy の頭の註)。
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
  (CompilePythonSources [tree items jobs roots]
    (<- at str (normal tree))
    (<- compiled tuple (compile-in-store store at items))
    (:= store (get compiled 0))
    (resume (get compiled 1)))
  (LinkFile [source target]
    (<- from str (normal source))
    (<- to str (normal target))
    (<- answer (link-in store from to))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
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
        ;; 取られている錠は、本物の flock と同じく放されるまで待つ(scheduler の Promise で — 空いている錠は scheduler に触れずに即答)。
        ;; 放した側(ReleaseLock)が錠を locks に残したまま次の待ち手へ手渡すので、起きた待ち手はそのまま持ち主になる。
        (do (<- promise (CreatePromise))
            (:= waiters (+ waiters #(#(at promise))))
            (<- (Wait promise.future))
            (resume (LockHeld :path at :token (len store.locks))))
        (do (<- kind PathKind (kind-in store at))
            (<- made (if (= kind PathKind.MISSING) (with-file store at b"" None) (return-store store)))
            (if (isinstance made FileFailed)
                (resume made)
                (do (:= store (with-fields made :locks (+ made.locks #(at))))
                    (resume (LockHeld :path at :token (len store.locks))))))))
  (ReleaseLock [held]
    (val waiting (lfor #(p promise) waiters :if (= p held.path) promise))
    (if waiting
        ;; 待ち手が在れば、錠を locks に残したまま先に待った 1 人へ手渡す(横入りさせない)。
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
    (<- at str (normal path))
    (<- refusal (dir-refusal store at))
    (if (is-not refusal None)
        (resume refusal)
        (resume (sum (gfor f store.files :if (under? f.path at) (len f.content))))))
  (ReadMemoryFiles []
    (resume store)))


(defk return-store [store]
  {:pre [(: store MemoryFiles)] :post [(: % MemoryFiles)]}
  "置き場をそのまま返すため(if の両枝を Program にそろえる)。"
  store)
