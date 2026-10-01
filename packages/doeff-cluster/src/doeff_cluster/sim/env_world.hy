;;; 実行環境の準備の速い模擬の世界(2026-09-27)— 翻訳 env-translation(本番と同じ 1 つ)の下で、土台の偽物だけが答える:
;;;   子 process    doeff の scripted-process-handler に渡す台本 git・tar・cp・uv(この module の ScriptedCommand)
;;;   file system  doeff の memory-file-handler(置き場の中身と空き)
;;;   時計          呼び手が外側に置く仮想の時計(uv の sync と native の build は台本の中で眠る)
;;; 準備の判断(env_prepare の prepare-env)も翻訳も本物のまま走る。業務の要求に直に答える偽物は持たない。
;;;
;;; 世界の宣言 EnvWorld:
;;;   remotes     = url → commit → ツリーの中身(file の path と文字)。uv.lock の中身もツリーに入れる。
;;;   denied      = worker の許可表から外す url(設定 runtime-env.repo-keys に載せない — 断るのは prepare-env)。
;;;   unreachable = 初めから届かない url(git の clone と fetch が「Could not read from remote repository」で終わる — 走行の途中で
;;;                 変えるには set-unreachable)。
;;;   uv-failure  = uv の失敗を 1 つ起こす(UvFailure — uv の側の語で宣言する。sync の失敗は sync で・build の失敗は build で返る。
;;;                 業務の kind と一時かは翻訳 env-translation が出力と終わりから読み分ける — 世界は業務の語を持たない)。
;;;   disk-free   = 空き(byte)。child-protocol = root の中の子の入口の約束の版。
;;; 模擬の uv.lock の書き方: 1 行 1 package で `名==版`、第三者の package の最上位の import の名は ` top=a,b` で添える
;;; (処理ステージ 10 の名前の影を起こすため)。editable で入る package は ` editable=<project の dir からの相対 path>` を添える
;;; (sync が venv の site-packages に本物の uv と同じ形の .pth — 中身は dir の絶対 path 1 行 — を置く)。`#` で始まる行は読まない。
;;;
;;; 世界の移ろう物は全部 memory の置き場の /world の下の file に置く(台本は状態を持たない):
;;;   log.json        clone・fetch・展開・複製・sync・download・build・bytecode の回数(read-world-log で読む)
;;;   notes.log       準備の記録の行(設定 runtime-env.notes)
;;;   uv-cache.json   取りに行った package の名(同じ lock の 2 回目は download 0)
;;;   uv-failure.json 今の uv の失敗(set-uv-failure で差し替える)
;;;   unreachable.json 今届かない url の列(set-unreachable で差し替える)
;;; mirror の中身は mirror の dir の remote-url(URL)と fetched(取った commit の行)。
;;;
;;;   (<- handlers (env-world world)) (with-handlers handlers program) — env-world は handler の列を返す Program(外側が先): memory の置き場・台本の子 process・翻訳の設定・翻訳。
;;;   外側に状態の置き場(doeff_core_effects の state)と時計が要る。
(require doeff-hy.macros [defk deff defhandler <- val var])
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass asdict])
(import enum [StrEnum])
(import functools [partial])
(import hashlib)
(import json)
(import posixpath)
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.file_effects [PathKind FileFailed PathStat MemoryFile MemoryFiles ReadMemoryFiles StatPath ReadText WriteText
                                         AppendText MakeDirectory WalkTree CopyTree])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.runtime_env_model [CHILD-PROTOCOL])
(import doeff_cluster.worker.protocol.env_translation [env-translation])

(val SOURCE-SUFFIXES #(".py" ".hy"))
;; 展開の複製で持ち越さない dir(venv は元の root の絶対 path を持ち、.pyc は元の root の Hy で作った物)。
(val NOT-COPIED #("/.venv/" "/__pycache__/"))
;; 世界の置き場。
(val STATE-DIR "/state")
(val WORLD-DIR "/world")
(val LOG-PATH "/world/log.json")
(val NOTES-PATH "/world/notes.log")
(val CACHE-PATH "/world/uv-cache.json")
(val FAILURE-PATH "/world/uv-failure.json")
(val UNREACHABLE-PATH "/world/unreachable.json")
(val CODE-PREPARE "/tools/code_prepare.hy")
;; 本物の git・uv と同じ終わり(届かない・無い物 = 128・使い方の誤り = 129)と、失敗の出力の語(翻訳が本物と同じく読み分ける)。
(val GIT-FATAL 128)
(val BAD-USAGE 129)
(val UNREACHABLE-TEXT "fatal: Could not read from remote repository.\n")
(val LOG-FIELDS #("clones" "fetches" "archives" "copies" "syncs" "downloads" "builds" "compiles" "carried"))


;; --- 世界の宣言 -----------------------------------------------------------------------------

;; uv の失敗の種類(uv の側の語):
;;   LOCK-OUTDATED       sync が「lock を直す必要があるが --locked」で終わる
;;   NO-INTERPRETER      sync が「Python の interpreter が無い」で終わる
;;   INDEX-UNREACHABLE   sync が package の index に届かない
;;   SDIST-BUILD-ERROR   sync が source の配布物の build に失敗する
;;   BUILD-KILLED        build が signal で殺される(負の終わり)
;;   BUILD-ERROR         build が compiler の誤りで終わる(終わり 1)
(defenum UvFault LOCK-OUTDATED NO-INTERPRETER INDEX-UNREACHABLE SDIST-BUILD-ERROR BUILD-KILLED BUILD-ERROR)
(val SYNC-FAULTS #(UvFault.LOCK-OUTDATED UvFault.NO-INTERPRETER UvFault.INDEX-UNREACHABLE UvFault.SDIST-BUILD-ERROR))
(val BUILD-FAULTS #(UvFault.BUILD-KILLED UvFault.BUILD-ERROR))


(defrecord UvFailure
  "世界が起こす uv の失敗 1 つ(fault = 種類・detail = 出力に添える文)。"
  (#^ UvFault fault)
  (#^ str detail))


(defrecord WorldFile
  "ツリーの中の file 1 つ(repo の中の相対 path と中身)。"
  (#^ str path)
  (#^ str text))


(defrecord WorldCommit
  "commit 1 つのツリー。"
  (#^ str sha)
  (#^ tuple files))


(defrecord WorldRemote
  "remote の repo 1 つ(push 済みの commit の列)。"
  (#^ str url)
  (#^ tuple commits))


(defrecord EnvWorld
  "速い模擬の外の世界の宣言(頭の註)。"
  (#^ tuple remotes)
  (setv #^ frozenset denied (frozenset))
  (setv #^ frozenset unreachable (frozenset))
  (setv #^ (| UvFailure None) uv-failure None)
  (setv #^ int disk-free (** 2 40))
  (setv #^ int child-protocol CHILD-PROTOCOL)
  (setv #^ float cold-seconds 60.0)
  (setv #^ float warm-seconds 5.0))


(defrecord EnvWorldLog
  "世界に起きた事の回数(筋書きの確かめに使う)。compiled-trees = bytecode を焼いた木と、その木の中の import の根(#(木 根の tuple) の列・
   焼いた順)・entries = 最後の bytecode の焼く範囲の入口・notes = 準備の記録の行。"
  (setv #^ int clones 0)
  (setv #^ int fetches 0)
  (setv #^ int archives 0)
  (setv #^ int copies 0)
  (setv #^ int syncs 0)
  (setv #^ int downloads 0)
  (setv #^ int builds 0)
  (setv #^ int compiles 0)
  (setv #^ int carried 0)
  (setv #^ tuple entries #())
  (setv #^ tuple compiled-trees #())
  (setv #^ tuple notes #()))


;; --- 純粋な換算 ---------------------------------------------------------------------------

(defk lock-lines [text]
  {:pre [(: text str)] :post [(: % tuple)]}
  "模擬の uv.lock の行 → #(\"名==版\" 最上位の名の tuple) の列(download の数と名前の影を数えるため)。"
  (var out [])
  (for [line (.splitlines text)]
    (val body (.strip line))
    (when (and body (not (.startswith body "#")))
      (val parts (.split body))
      (val tops (lfor p (cut parts 1 None) :if (.startswith p "top=") t (.split (cut p 4 None) ",") :if t t))
      (.append out #((get parts 0) (tuple tops)))))
  (tuple out))


(defk lock-editables [text]
  {:pre [(: text str)] :post [(: % tuple)]}
  "模擬の uv.lock の行のうち editable で入る物 → #(package の名 project の dir からの相対 path) の列。"
  (var out [])
  (for [line (.splitlines text)]
    (val body (.strip line))
    (when (and body (not (.startswith body "#")))
      (val parts (.split body))
      (for [p (cut parts 1 None)]
        (when (.startswith p "editable=")
          (.append out #((get (.split (get parts 0) "==") 0) (cut p (len "editable=") None)))))))
  (tuple out))


(defk commit-of [world sha]
  {:pre [(: world EnvWorld) (: sha str)] :post [(: % (| WorldCommit None))]}
  "sha の commit(どの remote にも無ければ None)。"
  (next (gfor r world.remotes c r.commits :if (= c.sha sha) c) None))


(defk option-of [args flag]
  {:pre [(: args tuple) (: flag str)] :post [(: % (| str None))]}
  "命令の引数から flag の次の値を読むため(無ければ None)。"
  (if (in flag args) (get args (+ (.index args flag) 1)) None))


(defk required-option-of [args flag]
  {:pre [(: args tuple) (: flag str)] :post [(: % str)]}
  "命令の引数から flag の次の値を読むため(翻訳が必ず添える flag — 無ければ台本が知らない形なので名指しで落とす)。"
  (<- found (| str None) (option-of args flag))
  (match found
    None (raise (ValueError (.format "台本が知らない形: {} の値が無い: {}" flag args)))
    value value))


(defk options-of [args flag]
  {:pre [(: args tuple) (: flag str)] :post [(: % tuple)]}
  "命令の引数から繰り返す flag の値を全部読むため。"
  (tuple (gfor #(i a) (enumerate args) :if (and (= a flag) (< (+ i 1) (len args))) (get args (+ i 1)))))


;; --- 世界の file の読み書き(file system の effect だけ) --------------------------------------------------

(defk read-json [path default]
  {:pre [(: path str) (: default (| dict list None))] :post [(: % (| dict list None))]}
  "世界の JSON の file を読むため(無ければ default)。"
  (<- seen (| str FileFailed) (ReadText path))
  (if (isinstance seen FileFailed) default (json.loads seen)))


(defk write-json [path value]
  {:pre [(: path str) (: value (| dict list None))] :post [(: % None)]}
  "世界の JSON の file を書くため。"
  (<- (WriteText path (json.dumps value :ensure-ascii False :sort-keys True)))
  None)


(defk bump [changes]
  {:pre [(: changes dict)] :post [(: % None)]}
  "世界の log の回数を足し、列の欄(entries は置き換え・compiled-trees は後ろへ足す)を書くため。"
  (<- log dict (read-json LOG-PATH {}))
  (for [#(k v) (.items changes)]
    (cond
      (in k LOG-FIELDS) (setv (get log k) (+ (.get log k 0) v))
      (= k "compiled-trees") (setv (get log k) (+ (.get log k []) [v]))
      True (setv (get log k) v)))
  (<- (write-json LOG-PATH log))
  None)


(defk write-file [path text]
  {:pre [(: path str) (: text str)] :post [(: % None)]}
  "親の dir を作ってから file を書くため。"
  (<- (MakeDirectory (posixpath.dirname path)))
  (<- (WriteText path text))
  None)


(defk read-or-empty [path]
  {:pre [(: path str)] :post [(: % str)]}
  "file の中身(無ければ空)。"
  (<- seen (| str FileFailed) (ReadText path))
  (if (isinstance seen FileFailed) "" seen))


(defk sources-under [root]
  {:pre [(: root str)] :post [(: % tuple)]}
  "root の下の source(.py・.hy — venv と __pycache__ の下を除く)の絶対 path の列。"
  (<- listed (| tuple FileFailed) (WalkTree root))
  (if (isinstance listed FileFailed)
      #()
      (tuple (gfor e listed
                   :setv path (posixpath.join root e.name)
                   :if (and (= e.kind PathKind.FILE) (.endswith path SOURCE-SUFFIXES)
                            (not (any (gfor n NOT-COPIED (in n (+ "/" e.name))))))
                   path))))


(defk top-names [root]
  {:pre [(: root str)] :post [(: % frozenset)]}
  "root の dir の下の import の対象になる最上位の名(source を持つ dir と module)。"
  (<- sources tuple (sources-under root))
  (frozenset (gfor path sources
                   :setv rest (cut path (+ (len root) 1) None)
                   :setv head (get (.split rest "/") 0)
                   (if (in "/" rest) head (get (.rsplit head "." 1) 0)))))


;; --- git ----------------------------------------------------------------------------------

(defk unreachable-now []
  {:pre [] :post [(: % tuple)] :tags {:context "runtime-env" :role "foundation"}}
  "今届かない url の列(世界の file から — set-unreachable で走行の途中に変わる)。"
  (<- seen list (read-json UNREACHABLE-PATH []))
  (tuple seen))


(defk git-clone [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "git clone --bare <url> <tmp> に答えるため(届かない url は本物と同じ語で終わる・mirror の dir に URL と取った commit の file を置く)。"
  (val url (get args -2))
  (val tmp (get args -1))
  (<- unreachable tuple (unreachable-now))
  (if (in url unreachable)
      (ProcessOutcome :stdout "" :stderr UNREACHABLE-TEXT :exit-code GIT-FATAL)
      (do (<- (write-file (posixpath.join tmp "remote-url") url))
          (<- (write-file (posixpath.join tmp "fetched") ""))
          (<- (bump {"clones" 1}))
          (ProcessOutcome :stdout "" :stderr "" :exit-code 0))))


(defk git-cat-file [argv args]
  {:pre [(: argv tuple) (: args tuple)] :post [(: % ProcessOutcome)]}
  "git cat-file -e <sha>^{commit} に答えるため(mirror に取った commit なら 0)。"
  (<- mirror str (required-option-of argv "-C"))
  (<- fetched str (read-or-empty (posixpath.join mirror "fetched")))
  (val sha (.removesuffix (get args -1) "^{commit}"))
  (if (in sha (.splitlines fetched)) (ProcessOutcome :stdout "" :stderr "" :exit-code 0) (ProcessOutcome :stdout "" :stderr "fatal: Not a valid object name\n" :exit-code 1)))


(defk git-config [argv]
  {:pre [(: argv tuple)] :post [(: % ProcessOutcome)]}
  "git config --get remote.origin.url に答えるため。"
  (<- mirror str (required-option-of argv "-C"))
  (<- url str (read-or-empty (posixpath.join mirror "remote-url")))
  (ProcessOutcome :stdout (+ url "\n") :stderr "" :exit-code 0))


(defk git-fetch [world argv args]
  {:pre [(: world EnvWorld) (: argv tuple) (: args tuple)] :post [(: % ProcessOutcome)]}
  "git fetch origin <sha|+refs/heads/*…> に答えるため(remote に在る commit を mirror に取る・届かない url は本物と同じ語で終わる)。"
  (<- mirror str (required-option-of argv "-C"))
  (<- url str (read-or-empty (posixpath.join mirror "remote-url")))
  (val ref (get args -1))
  (<- commit (| WorldCommit None) (commit-of world ref))
  (<- fetched str (read-or-empty (posixpath.join mirror "fetched")))
  (<- unreachable tuple (unreachable-now))
  (cond
    (in url unreachable) (ProcessOutcome :stdout "" :stderr UNREACHABLE-TEXT :exit-code GIT-FATAL)
    (.startswith ref "+refs/") (ProcessOutcome :stdout "" :stderr "" :exit-code 0)
    (is commit None) (ProcessOutcome :stdout "" :stderr (.format "fatal: couldn't find remote ref {}\n" ref) :exit-code GIT-FATAL)
    (in ref (.splitlines fetched)) (ProcessOutcome :stdout "" :stderr "" :exit-code 0)
    True (do (<- (WriteText (posixpath.join mirror "fetched") (+ fetched ref "\n")))
             (<- (bump {"fetches" 1}))
             (ProcessOutcome :stdout "" :stderr "" :exit-code 0))))


(defk git-archive [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "git archive -o <archive> <sha> に答えるため(tar の台本が読む形 — path → 中身の JSON — で commit の木を書く)。"
  (<- archive str (required-option-of args "-o"))
  (<- commit (| WorldCommit None) (commit-of world (get args -1)))
  (if (is commit None)
      (ProcessOutcome :stdout "" :stderr "fatal: not a valid object name\n" :exit-code GIT-FATAL)
      (do (<- (write-json archive (dfor f commit.files f.path f.text)))
          (<- (bump {"archives" 1}))
          (ProcessOutcome :stdout "" :stderr "" :exit-code 0))))


(defk git-rev-parse [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "git rev-parse <sha>:<path> に答えるため(dir の下の file の path と中身で決まる hash — 本物の tree hash と同じく中身が同じなら同じ値)。"
  (val sha-path (.split (get args -1) ":" 1))
  (val sha (get sha-path 0))
  (val path (get sha-path 1))
  (<- commit (| WorldCommit None) (commit-of world sha))
  (if (is commit None)
      (ProcessOutcome :stdout "" :stderr "fatal: bad revision\n" :exit-code GIT-FATAL)
      (do (val prefix (+ path "/"))
          (val parts (lfor f (sorted commit.files :key (fn [f] f.path)) :if (.startswith f.path prefix) (+ f.path "\0" f.text)))
          (ProcessOutcome :stdout (+ (.hexdigest (hashlib.sha1 (.encode (.join "\n" parts)))) "\n") :stderr "" :exit-code 0))))


(defk git-script [world commands request]
  {:pre [(: world EnvWorld) (: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "翻訳が出す git の問い(clone・cat-file・config・fetch・archive・rev-parse)に世界から答える台本。"
  (val argv (tuple request.argv))
  (val args (if (in "-C" argv) (cut argv 3 None) (cut argv 1 None)))
  (<- answer ProcessOutcome
      (match (if args (get args 0) "")
        "clone" (git-clone world args)
        "cat-file" (git-cat-file argv args)
        "config" (git-config argv)
        "fetch" (git-fetch world argv args)
        "archive" (git-archive world args)
        "rev-parse" (git-rev-parse world args)
        _ (ProcessOutcome :stdout "" :stderr (.format "usage: git の台本が知らない形: {}\n" argv) :exit-code BAD-USAGE)))
  answer)


;; --- tar・cp ------------------------------------------------------------------------------

(defk tar-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "tar -xf <archive> -C <dest> に答える台本(git の台本が書いた archive の中身を dest の下へ置く)。"
  (val argv (tuple request.argv))
  (<- archive str (required-option-of argv "-xf"))
  (<- dest str (required-option-of argv "-C"))
  (<- tree dict (read-json archive {}))
  (for [#(path text) (.items tree)]
    (<- (write-file (posixpath.join dest path) text)))
  (ProcessOutcome :stdout "" :stderr "" :exit-code 0))


(defk cp-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "cp -al <source>/. <dest> に答える台本(置き場の中で木を写す)。"
  (val argv (tuple request.argv))
  (val source (.removesuffix (get argv -2) "/."))
  (<- copied (CopyTree source (get argv -1)))
  (if (isinstance copied FileFailed)
      (ProcessOutcome :stdout "" :stderr (.format "cp: {}\n" copied.detail) :exit-code 1)
      (do (<- (bump {"copies" 1}))
          (ProcessOutcome :stdout "" :stderr "" :exit-code 0))))


;; --- uv -----------------------------------------------------------------------------------

(defk uv-failure-now []
  {:pre [] :post [(: % (| UvFailure None))] :tags {:context "runtime-env" :role "foundation"}}
  "今の uv の失敗(世界の file から — set-uv-failure で走行の途中に変わる)。"
  ;; 失敗の file は {"fault" … "detail" …} の 1 つの object — list の JSON なら世界の file の形が壊れているので、型を書いた束縛で断る。
  (<- seen (| dict None) (read-json FAILURE-PATH None))
  (if (is seen None)
      None
      (UvFailure :fault (UvFault (get seen "fault")) :detail (get seen "detail"))))


(defk sync-failure-text [failure]
  {:pre [(: failure UvFailure)] :post [(: % str)] :tags {:context "runtime-env" :role "foundation"}}
  "uv sync の失敗を本物の uv と同じ語の出力にするため(業務の kind と一時かは翻訳が本物と同じくこの語から読み分ける)。"
  (match failure.fault
    UvFault.LOCK-OUTDATED "error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.\n"
    UvFault.NO-INTERPRETER "error: No interpreter found for Python\n"
    UvFault.INDEX-UNREACHABLE "error: Failed to fetch: `https://pypi.org/simple`\n"
    _ "error: Failed to build the source distribution\n"))


(defk uv-sync [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "uv sync --locked に答える: lock の package のうち cache に無い物を取りに行き(冷たい秒)、venv と editable の .pth を置く。"
  (<- pdir str (required-option-of args "--project"))
  (<- python str (required-option-of args "--python"))
  (<- no-install tuple (options-of args "--no-install-package"))
  (<- failure (| UvFailure None) (uv-failure-now))
  (if (and failure (in failure.fault SYNC-FAULTS))
      (do (<- text str (sync-failure-text failure))
          (ProcessOutcome :stdout "" :stderr (+ text failure.detail "\n") :exit-code 1))
      (do (<- lock str (read-or-empty (posixpath.join pdir "uv.lock")))
          (<- lines tuple (lock-lines lock))
          (<- cached list (read-json CACHE-PATH []))
          (val wanted (lfor #(name _) lines :if (not-in (get (.split name "==") 0) no-install) name))
          (val missing (lfor name wanted :if (not-in name cached) name))
          (<- (write-json CACHE-PATH (+ cached missing)))
          (<- (Delay (if missing world.cold-seconds world.warm-seconds)))
          (val venv (posixpath.join pdir ".venv"))
          (val site (posixpath.join venv "lib" (+ "python" python) "site-packages"))
          (<- (write-file (posixpath.join venv "pyvenv.cfg") (.format "python = {}\n" python)))
          (<- (write-file (posixpath.join venv "bin" "python") ""))
          (<- (MakeDirectory site))
          ;; editable の package は、本物の uv と同じく site-packages に dir の絶対 path 1 行の .pth を置く。
          (<- editables tuple (lock-editables lock))
          (for [#(name rel) editables]
            (<- (WriteText (posixpath.join site (.format "_editable_impl_{}.pth" (.replace name "-" "_")))
                           (posixpath.normpath (posixpath.join pdir rel)))))
          (<- (bump {"syncs" 1 "downloads" (len missing)}))
          (ProcessOutcome :stdout "" :stderr (.format "Prepared {} packages in 1ms\n" (len missing)) :exit-code 0))))


(defk uv-build [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "uv build --wheel --out-dir <dir> <source> に答える: native の build(冷たい秒)で dir に wheel を 1 つ置く。"
  (<- failure (| UvFailure None) (uv-failure-now))
  (if (and failure (in failure.fault BUILD-FAULTS))
      ;; signal での終了は負の終わり、compiler の誤りは 1 — 一時か恒久かは翻訳が本物と同じく終わりで読み分ける。
      (ProcessOutcome :stdout "" :stderr (+ failure.detail "\n") :exit-code (if (= failure.fault UvFault.BUILD-KILLED) -9 1))
      (do (<- (Delay world.cold-seconds))
          (<- out str (required-option-of args "--out-dir"))
          (<- (write-file (posixpath.join out (+ (posixpath.basename (get args -1)) ".whl")) ""))
          (<- (bump {"builds" 1}))
          (ProcessOutcome :stdout "" :stderr "" :exit-code 0))))


(defk uv-pip [args]
  {:pre [(: args tuple)] :post [(: % ProcessOutcome)]}
  "uv pip install --no-deps --python <venv の python> <wheel>… に答える: venv の wheels/ に入れた印を置く。"
  (<- python str (required-option-of args "--python"))
  (val venv (posixpath.dirname (posixpath.dirname python)))
  (val wheels (cut args (+ (.index args python) 1) None))
  (for [w wheels]
    (<- (write-file (posixpath.join venv "wheels" (posixpath.basename w)) w)))
  (ProcessOutcome :stdout "" :stderr "" :exit-code 0))


(defk uv-compile [args]
  {:pre [(: args tuple)] :post [(: % ProcessOutcome)]}
  "uv run … hy <code_prepare> <tree> --import-roots … に答える: 本物の道具と同じく、根の下に焼く source が 1 つも無い木は失敗で返す。"
  (val at (.index args "hy"))
  (val tree (get args (+ at 2)))
  (<- roots-text str (required-option-of args "--import-roots"))
  (val roots (tuple (.split roots-text ",")))
  (<- carry (| str None) (option-of args "--from"))
  (<- entries-text (| str None) (option-of args "--entries"))
  (val entries (if entries-text (tuple (.split entries-text ",")) #()))
  (<- sources tuple (sources-under tree))
  (val under-roots (lfor p sources
                         :if (any (gfor r roots (or (= r ".") (.startswith p (.format "{}/{}/" tree r)))))
                         p))
  (if (not under-roots)
      (do (<- (bump {"compiled-trees" [tree (list roots)]}))
          (ProcessOutcome :stdout "" :stderr "木に焼くべき source が 1 つも無い\n" :exit-code 1))
      (do (<- (bump {"compiles" 1 "entries" (list entries) "compiled-trees" [tree (list roots)]
                     "carried" (if (is carry None) 0 (len sources))}))
          (ProcessOutcome :stdout "" :stderr (.format "carried={} compiled={}\n" (if (is carry None) 0 (len sources)) (if (is carry None) (len sources) 0)) :exit-code 0))))


(defk uv-probe [world args]
  {:pre [(: world EnvWorld) (: args tuple)] :post [(: % ProcessOutcome)]}
  "uv run … hy -c <確かめ> <根>… に答える: 根の最上位の名のうち lock の第三者の package の名と重なる物を「根の外に解けた」とする。"
  (<- pdir str (required-option-of args "--project"))
  (val roots (cut args (+ (.index args "-c") 2) None))
  (<- lock str (read-or-empty (posixpath.join pdir "uv.lock")))
  (<- lines tuple (lock-lines lock))
  (val third (frozenset (gfor #(_ tops) lines t tops t)))
  (var misplaced [])
  (for [root roots]
    (<- names frozenset (top-names root))
    (.extend misplaced (sorted (& names third))))
  (ProcessOutcome :stdout (+ (json.dumps {"childProtocol" world.child-protocol "misplaced" misplaced}) "\n") :stderr "" :exit-code 0))


(defk uv-script [world commands request]
  {:pre [(: world EnvWorld) (: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "翻訳が出す uv の命令(sync・build・pip install・run)に世界から答える台本。"
  (val args (tuple request.argv))
  (val verb (if (> (len args) 1) (get args 1) ""))
  (<- answer ProcessOutcome
      (match verb
        "sync" (uv-sync world args)
        "build" (uv-build world args)
        "pip" (uv-pip args)
        "run" :if (in "-c" args) (uv-probe world args)
        "run" (uv-compile args)
        _ (ProcessOutcome :stdout "" :stderr (.format "uv の台本が知らない形: {}\n" args) :exit-code 2)))
  answer)


;; --- 組み立て -----------------------------------------------------------------------------

(defhandler world-settings-reader [#^ dict settings]
  ;; 引数に残す理由: 設定は世界の宣言ごとに違う値(模擬の世界そのもの)。
  ;; 翻訳の設定(runtime-env.*)の Ask にだけ答え、他の鍵は外へ通す — 同じ組の内側で走る task の Ask(実行先の reader が答える)を取らない。
  (Ask [key] :when (in key settings)
    (resume (get settings key))))



(defk world-settings [world]
  {:pre [(: world EnvWorld)] :post [(: % dict)] :tags {:context "runtime-env" :role "entry"}}
  "翻訳の設定(runtime-env.*): 許可表 = 世界の remote の url から denied を除いた物(鍵なし)。"
  {"runtime-env.state" STATE-DIR
   "runtime-env.repo-keys" (dfor r world.remotes :if (not-in r.url world.denied) r.url "")
   "runtime-env.code-prepare" CODE-PREPARE
   "runtime-env.uv" "uv"
   "runtime-env.progress" ""
   "runtime-env.notes" NOTES-PATH})


(defk world-files-of [world]
  {:pre [(: world EnvWorld)] :post [(: % MemoryFiles)] :tags {:context "runtime-env" :role "entry"}}
  "memory の置き場の初めの中身(state と world の dir・今の uv の失敗・今届かない url・空き)。"
  (val failure world.uv-failure)
  (MemoryFiles :dirs #(STATE-DIR WORLD-DIR)
               :files (+ (if (is failure None)
                             #()
                             ;; 世界の file の JSON は UvFailure の欄そのまま(fault は StrEnum なので文字で書かれる)。
                             #((MemoryFile :path FAILURE-PATH :content (.encode (json.dumps (asdict failure))))))
                         #((MemoryFile :path UNREACHABLE-PATH :content (.encode (json.dumps (sorted world.unreachable))))))
               :free world.disk-free))


(defk env-world [world]
  {:pre [(: world EnvWorld)] :post [(: % list)] :tags {:context "runtime-env" :role "entry"}}
  "世界の宣言から、memory の置き場・台本の子 process・翻訳の設定・翻訳の handler の列(外側が先)を作る(外側に状態の置き場と時計が要る)。"
  (val script (ProcessScript :commands #((ScriptedCommand :name "git" :run (partial git-script world))
                                         (ScriptedCommand :name "tar" :run tar-script)
                                         (ScriptedCommand :name "cp" :run cp-script)
                                         (ScriptedCommand :name "uv" :run (partial uv-script world)))))
  (<- files MemoryFiles (world-files-of world))
  (<- settings dict (world-settings world))
  [(memory-file-handler files) (scripted-process-handler script) (world-settings-reader settings) env-translation])


;; --- 観測(筋書きが世界を読む) --------------------------------------------------------------------

(defk read-world-log []
  {:pre [] :post [(: % EnvWorldLog)]}
  "ここまでに世界に起きた事の回数と、準備の記録の行を読む。"
  (<- log dict (read-json LOG-PATH {}))
  (<- notes str (read-or-empty NOTES-PATH))
  (EnvWorldLog #** (dfor k LOG-FIELDS (.replace k "-" "_") (.get log k 0))
               :entries (tuple (.get log "entries" []))
               :compiled-trees (tuple (gfor #(tree roots) (.get log "compiled-trees" []) #(tree (tuple roots))))
               :notes (tuple (gfor line (.splitlines notes) :if line (.removeprefix line "env: ")))))


(defk world-files [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]}
  "置き場の prefix の下の file(絶対 path と中身の WorldFile・path の順)。"
  (<- store MemoryFiles (ReadMemoryFiles))
  (tuple (gfor f (sorted store.files :key (fn [f] f.path)) :if (.startswith f.path prefix)
               (WorldFile :path f.path :text (.decode f.content "utf-8" "replace")))))


(defk set-uv-failure [failure]
  {:pre [(: failure (| UvFailure None))] :post [(: % None)] :tags {:context "runtime-env" :role "foundation"}}
  "筋書きが走行の途中で以後の uv の失敗を差し替えるため(None = 失敗させない)。"
  (<- (write-json FAILURE-PATH (if (is failure None) None (asdict failure))))
  None)


(defk set-unreachable [urls]
  {:pre [(: urls frozenset)] :post [(: % None)] :tags {:context "runtime-env" :role "foundation"}}
  "筋書きが走行の途中で届かない url の列を差し替えるため(mirror が在る状態で届かなくなる筋を起こす)。"
  (<- (write-json UNREACHABLE-PATH (sorted urls)))
  None)
