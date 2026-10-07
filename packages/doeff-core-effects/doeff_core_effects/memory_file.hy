;;; 汎用の file system の effect(file_effects.hy)の I/O なしの答え手 memory-file-handler(agora-redesign #802 便 1)。置き場(MemoryFiles)を
;;; session の値に持ち、本物の file system と同じ所で断る(FileFailed の detail も OSError の文の形):
;;;   親の dir が無い(No such file or directory)・途中や先が file(Not a directory)・dir へ書く / 読む(Is a directory)・中身の在る dir への
;;;   rename(Directory not empty)・dir を自分の下へ rename(Invalid argument)・無い path を消す。
;;; symlink を持つ(MakeSymlink — agora-redesign #4036): 本物と同じく、読み・書き・一覧・走査・錠は path の symlink を辿り、RenamePath・
;;; RemoveTree・LinkFile・MakeSymlink と書きの置き換え(replace = True)は最後の 1 つを辿らずに link 自身を扱い、StatPath の
;;; follow-symlinks = False は SYMLINK と答える。dir の中の link は一覧と走査に SYMLINK で出て、走査は link の先へ入らない。断りの path は
;;; 呼び手が渡した path(辿った先ではない)。辿る数が 40 を越えたら本物と同じく ELOOP で断る。錠は本物と同じく取れるまで待つ
;;; (取られている時だけ scheduler の CreatePromise / Wait で待つので、並行の筋書きは外側に scheduler が要る — scheduler の無い 1 本の
;;; 筋書きで同じ錠を 2 度取るのは、本物の自分待ちと同じく止まる誤り)。
;;; path は絶対 path だけを受ける(相対 path は呼び手の誤り — ValueError)。業務を知らない: 初めの中身は呼び手が渡す。
;;; ReadMemoryFiles で今の中身を読める(検と筋書きが置き場を覗く口)。session の値の置き場(doeff_core_effects の state)は外側に要る。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import posixpath)
(import collections.abc [Callable])
(import dataclasses [replace :as with-fields])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Wait])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat DirEntry LockHeld MemoryFile MemoryLink MemoryFiles ReadMemoryFiles StatPath
                                         MakeSymlink
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
(val LOOP "[Errno 40] Too many levels of symbolic links")
;; 1 つの path を辿る symlink の数の上限(Linux の MAXSYMLINKS と同じ)。
(val MAX-HOPS 40)
;; file の effect の答えの型の和(断りを渡された path へ戻す as-asked が受ける — 断り以外はそのまま通す)。
(val ANSWER (| FileFailed PathStat LockHeld MemoryFiles str bytes tuple int None))


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
  "置き場の path の種類を読むため(symlink は辿らずに SYMLINK — os.lstat と同じ)。"
  (cond
    (any (gfor f store.files (= f.path path))) PathKind.FILE
    (or (= path ROOT) (in path store.dirs)) PathKind.DIRECTORY
    (any (gfor l store.links (= l.path path))) PathKind.SYMLINK
    True PathKind.MISSING))


(defk link-at [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| MemoryLink None))]}
  "置き場の path に在る symlink を読むため(無ければ None)。"
  (next (gfor l store.links :if (= l.path path) l) None))


(defk resolved [store path follow-last]
  {:pre [(: store MemoryFiles) (: path str) (: follow-last bool)] :post [(: % (| str FileFailed))]}
  "path の途中の symlink を辿った path を作るため(follow-last = True なら最後の 1 つも辿る — 読み・書き。False は link 自身を扱う操作)。
   相対の先は link の在る dir から読む。辿る数が MAX-HOPS を越えたら本物と同じく ELOOP で断る(断りの path は渡された path)。"
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
  "辿った先 at の path で作った断りを、呼び手が渡した path asked の断りに戻すため(本物の OSError は渡された path を名指す)。"
  (if (and (isinstance answer FileFailed) (!= asked at) (= answer.path at))
      (FileFailed :path asked :detail (.replace answer.detail (repr at) (repr asked)))
      answer))


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
  ;; 同じ名の link は置き換わる(書きの置き換え replace = True は link を辿らずに呼ばれる — 本物の os.replace と同じ)。
  (with-fields store :files (+ (tuple (gfor f store.files :if (!= f.path path) f)) #((MemoryFile :path path :content content :mode mode)))
                     :links (tuple (gfor l store.links :if (!= l.path path) l))))


(defk symlink-in [store path target]
  {:pre [(: store MemoryFiles) (: path str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "path に target を指す symlink を足した置き場を作るため(os.symlink と同じ所で断る — 文は先と link の 2 つの path の形・path が在れば
   File exists・親が無ければ No such file。先は在らなくてよい)。"
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
  "dir の下の全部を相対 path の順に並べるため(呼び手が dir であることを確かめる)。link は SYMLINK で出し、先へは入らない(link の先の
   中身は先の path に在るので、ここには出ない)。"
  (val found (+ (lfor d store.dirs :if (and (!= d ROOT) (under? d path)) #(d PathKind.DIRECTORY))
                (lfor f store.files :if (under? f.path path) #(f.path PathKind.FILE))
                (lfor l store.links :if (under? l.path path) #(l.path PathKind.SYMLINK))))
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
                 :dirs (tuple (gfor d store.dirs :if (not (or (= d path) (under? d path))) d))
                 :links (tuple (gfor l store.links :if (not (or (= l.path path) (under? l.path path))) l))))


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
    ;; link は link のまま写す(本物の shutil.copytree の symlinks = True と同じ)。
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
  "source の下の path を target の下へ付け替えるため。"
  (if (= path source) target (+ target (cut path (len source) None))))


(defk rename-in [store source target]
  {:pre [(: store MemoryFiles) (: source str) (: target str)] :post [(: % (| MemoryFiles FileFailed))]}
  "path の名を変えた置き場を作るため(os.replace と同じ所で断る — link は辿らずに link 自身を動かし、置き換える)。"
  (<- from-kind PathKind (kind-in store source))
  (<- to-kind PathKind (kind-in store target))
  (<- parent (parent-refusal store target))
  ;; link は file と同じ扱い(dir を指していても、名の変更では中身を持たない 1 つの名)。
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
  ;; link は名だけを付け替える(先の綴りは書いたまま — 本物の rename と同じ)。
  (val links (tuple (gfor l cleared.links
                          (MemoryLink :path (if (inside l.path) (+ target (cut l.path (len source) None)) l.path) :target l.target))))
  (with-fields cleared :files (tuple files) :dirs (tuple dirs) :links links))


(defk return-path [path]
  {:pre [(: path str)] :post [(: % str)]}
  "付け替えない path をそのまま返すため(if の両枝を Program にそろえる)。"
  path)


(defk stat-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % PathStat)]}
  "path の様子を読むため(mtime は持たないので 0・link の大きさは本物の lstat と同じく先の綴りの byte 数)。"
  (<- kind PathKind (kind-in store path))
  (val size (match kind
              PathKind.FILE (len (next (gfor f store.files :if (= f.path path) f.content)))
              PathKind.SYMLINK (len (.encode (next (gfor l store.links :if (= l.path path) l.target)) "utf-8"))
              _ 0))
  (PathStat :kind kind :real-path path :size size :modified 0.0))


(defhandler memory-file-handler [#^ MemoryFiles initial]
  ;; 引数に残す理由: 初めの中身は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (session var store initial)
  ;; 錠を待つ手(#(path Promise) の列・待った順)。置き場の中身ではないので MemoryFiles には入れない。
  (session var waiters #())
  ;; path の symlink を辿るかは effect ごと(頭の註): 読み・書き・一覧・走査・錠は最後まで辿り(on-path の True)、link 自身を扱う操作は
  ;; 途中だけを辿る(False)。断りは渡された path で答える。
  (StatPath [path follow-symlinks]
    ;; 名を answer にしない: 節は全部 1 つの関数に展開されるので、型を付けた answer は他の節の answer まで PathStat と宣言する。
    (<- stat (| PathStat FileFailed) (on-path store path follow-symlinks stat-in))
    (resume stat))
  (ReadText [path]
    (<- answer (on-path store path True content-of))
    (resume (if (isinstance answer bytes) (.decode answer "utf-8" "replace") answer)))
  (ReadBytes [path limit offset]
    (<- answer (on-path store path True content-of))
    (resume (if (isinstance answer bytes) (cut answer offset (if (is limit None) None (+ offset limit))) answer)))
  ;; 書きの sync は落とす先が無いので読まない(答えは本物と同じ — file_effects.hy の頭の註)。置き換えの書き(replace = True)は本物と同じく
  ;; 別名に書いて os.replace するので、最後の link を辿らずに link を file で置き換える。
  (WriteText [path text mode replace]
    (<- answer (on-path store path (not replace) (fn [s at] (with-file s at (.encode text "utf-8") mode))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (WriteBytes [path content mode replace]
    (<- answer (on-path store path (not replace) (fn [s at] (with-file s at content mode))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (AppendText [path text]
    (<- answer (on-path store path True (fn [s at] (appended-in s at text))))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (MakeDirectory [path mode]
    (<- answer (on-path store path True with-dirs))
    (if (isinstance answer FileFailed) (resume answer) (do (:= store answer) (resume None))))
  (ListDirectory [path]
    (<- answer (on-path store path True list-in))
    (resume answer))
  (WalkTree [path]
    (<- answer (on-path store path True walk-in))
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
      ;; 取られている錠は、本物の flock と同じく放されるまで待つ(scheduler の Promise で — 空いている錠は scheduler に触れずに即答)。
      ;; 放した側(ReleaseLock)が錠を locks に残したまま次の待ち手へ手渡すので、起きた待ち手はそのまま持ち主になる。
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
    (<- answer (on-path store path True measured-in))
    (resume answer))
  (ReadMemoryFiles []
    (resume store)))


(defk on-path [store path follow-last op]
  {:pre [(: store MemoryFiles) (: path str) (: follow-last bool) (: op Callable)] :post [(: % ANSWER)]}
  "path の symlink を辿った先で置き場の操作 op(置き場と辿った先の path を受ける)を 1 つ行い、断りを渡された path へ戻すため(頭の註 —
   本物の OSError は渡された path を名指す)。辿れなければ(ELOOP)その断り。"
  (<- asked str (normal path))
  (<- at (| str FileFailed) (resolved store asked follow-last))
  (when (isinstance at FileFailed)
    (return at))
  (<- answer (op store at))
  (<- told (as-asked answer asked at))
  told)


(defk on-pair [store source target follow-last op]
  {:pre [(: store MemoryFiles) (: source str) (: target str) (: follow-last bool) (: op Callable)] :post [(: % ANSWER)]}
  "2 つの path(元と先)の symlink を辿った先で置き場の操作 op を 1 つ行うため(断りは元の側の path へ戻す — os.replace の文と同じ)。"
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
  {:pre [(: store MemoryFiles) (: path str) (: text str)] :post [(: % (| MemoryFiles FileFailed))]}
  "file の末尾に text を足した置き場を作るため(無ければ作る)。"
  (<- before (content-of store path))
  (<- answer (with-file store path (+ (if (isinstance before bytes) before b"") (.encode text "utf-8")) None))
  answer)


(defk walk-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の下の全部を相対 path の順に並べるため(dir でなければ断る)。"
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (<- below tuple (entries-below store path))
  below)


(defk measured-in [store path]
  {:pre [(: store MemoryFiles) (: path str)] :post [(: % (| int FileFailed))]}
  "dir の下の file の大きさの合計を測るため(link は辿らず先の綴りの byte 数 — 本物の lstat と同じ)。"
  (<- refusal (dir-refusal store path))
  (when (is-not refusal None)
    (return refusal))
  (+ (sum (gfor f store.files :if (under? f.path path) (len f.content)))
     (sum (gfor l store.links :if (under? l.path path) (len (.encode l.target "utf-8"))))))


(defk return-store [store]
  {:pre [(: store MemoryFiles)] :post [(: % MemoryFiles)]}
  "置き場をそのまま返すため(if の両枝を Program にそろえる)。"
  store)
