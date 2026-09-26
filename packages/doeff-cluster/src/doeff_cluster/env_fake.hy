;;; 実行環境の準備の速い模擬の handler(fake-env)— memory の git と uv と file と、仮想の時計の眠り(2026-09-26)。
;;;
;;; 外の世界に触る effect(git・uv・disk・file)だけを差し替える。準備の判断(env_prepare の prepare-env)は本物のまま走る。
;;; 世界の宣言 FakeEnvWorld:
;;;   remotes     = url → commit → ツリーの中身(file の path と文字)。uv.lock の中身もツリーに入れる。
;;;   denied / unreachable = 許可表に無い url / 届かない url。
;;;   uv-failure  = uv の失敗を 1 つ起こす(sync の失敗は sync で・native の build の失敗は build で返る)。
;;;   disk-free   = 空き(byte)。child-protocol = root の中の子の入口の約束の版。
;;; 模擬の uv.lock の書き方: 1 行 1 package で `名==版`、第三者の package の最上位の import の名は ` top=a,b` で添える
;;; (処理ステージ 10 の名前の影を起こすため)。`#` で始まる行は読まない。
;;;
;;; 観測: ReadFakeEnvLog(clone・fetch・展開・複製・sync・download・build・bytecode の回数)と ListFakeFiles(prefix の下の file)。
;;; 時間: cache に無い package を取りに行く sync と native の build は冷たい秒(既定 60)、それ以外の sync は温い秒(既定 5)だけ眠る。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import hashlib)
(import json)
(import doeff [EffectBase])
(import doeff_time [Delay])
(import .runtime_env_model [EnvFailure EnvFailureKind CHILD-PROTOCOL])
(import .env_prepare [DiskFree EnsureMirror FetchCommit MaterializeTree FileSha256 TreeHash EnsureNativeWheel SyncProject
                      InstallWheels WriteImportRoots CompileTree ProbeImports WriteEnvMarker env-marker->json
                      MirrorReady FetchState WheelReady SyncReport BytecodeReport ProbeReport ENV-MARKER ROOTS-PTH])

(val SOURCE-SUFFIXES #(".py" ".hy"))
;; 展開の複製で持ち越さない物(venv は元の root の絶対 path を持ち、.pyc は元の root の Hy で作った物)。
(val NOT-COPIED #("/.venv/" "/__pycache__/"))


;; --- 世界の宣言 -----------------------------------------------------------------------------

(defrecord FakeFile
  "ツリーの中の file 1 つ(repo の中の相対 path と中身)。"
  (#^ str path)
  (#^ str text))


(defrecord FakeCommit
  "commit 1 つのツリー。"
  (#^ str sha)
  (#^ tuple files))


(defrecord FakeRemote
  "remote の repo 1 つ(push 済みの commit の列)。"
  (#^ str url)
  (#^ tuple commits))


(defrecord FakeEnvWorld
  "速い模擬の外の世界の宣言。"
  (#^ tuple remotes)
  (setv #^ frozenset denied (frozenset))
  (setv #^ frozenset unreachable (frozenset))
  (setv #^ (| EnvFailure None) uv-failure None)
  (setv #^ int disk-free (** 2 40))
  (setv #^ int child-protocol CHILD-PROTOCOL)
  (setv #^ float cold-seconds 60.0)
  (setv #^ float warm-seconds 5.0))


(defrecord FakeEnvLog
  "fake の外の世界に起きた事の回数(筋書きの確かめに使う)。"
  (setv #^ int clones 0)
  (setv #^ int fetches 0)
  (setv #^ int archives 0)
  (setv #^ int copies 0)
  (setv #^ int syncs 0)
  (setv #^ int downloads 0)
  (setv #^ int builds 0)
  (setv #^ int compiles 0)
  (setv #^ int carried 0))


;; --- 観測の effect(fake だけが答える) ----------------------------------------------------

(defclass [(dataclass :frozen True)] ReadFakeEnvLog [EffectBase]
  "答え = FakeEnvLog(ここまでの回数)。")


(defclass [(dataclass :frozen True)] ListFakeFiles [EffectBase]
  "答え = prefix の下の FakeFile の tuple(絶対 path・path の順)。"
  (#^ str prefix))


(defclass [(dataclass :frozen True)] SetFakeUvFailure [EffectBase]
  "以後の uv の失敗を差し替える(None = 失敗させない)。答え = None。"
  (#^ (| EnvFailure None) failure))


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


(defk top-names [files root]
  {:pre [(: files dict) (: root str)] :post [(: % frozenset)]}
  "root の dir の下の import の対象になる最上位の名(source を持つ dir と module)。"
  (val prefix (+ root "/"))
  (var names (set))
  (for [path files]
    (when (and (.startswith path prefix) (.endswith path SOURCE-SUFFIXES) (not (any (gfor n NOT-COPIED (in n path)))))
      (val rest (cut path (len prefix) None))
      (val head (get (.split rest "/") 0))
      (.add names (if (in "/" rest) head (get (.rsplit head "." 1) 0)))))
  (frozenset names))


(defk text-sha256 [text]
  {:pre [(: text str)] :post [(: % str)]}
  "模擬の file の中身の sha256(本物の FileSha256 と同じ値にするため)。"
  (.hexdigest (hashlib.sha256 (.encode text "utf-8"))))


;; --- handler ------------------------------------------------------------------------------

(defhandler fake-env [world]
  ;; 引数に残す理由: world(FakeEnvWorld)は模擬する外の世界そのもので、検の筋書きごとに違う(設定ではない)。
  ;; memory の file の表・mirror の path → url・取りに行った commit・uv の cache・wheel の表をセッションで持つ。
  (session val files (dict))
  (session val mirror-urls (dict))
  (session val fetched (set))
  (session val cache (set))
  (session val wheels (dict))
  (session var log (FakeEnvLog))
  (session var uv-failure world.uv-failure)

  (ReadFakeEnvLog []
    (resume log))
  (ListFakeFiles [prefix]
    (resume (tuple (gfor p (sorted files) :if (.startswith p prefix) (FakeFile :path p :text (get files p))))))
  (SetFakeUvFailure [failure]
    (:= uv-failure failure)
    (resume None))

  (DiskFree [path]
    (resume world.disk-free))
  (EnsureMirror [url]
    (cond
      (in url world.denied)
      (resume (EnvFailure :kind EnvFailureKind.REPO-DENIED :detail (.format "許可表に無い url: {}" url) :retryable False))
      (in url world.unreachable)
      (resume (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :detail (.format "届かない: {}" url) :retryable True))
      True
      (do (val path (.format "/fake/mirrors/{}.git" (cut (.hexdigest (hashlib.sha256 (.encode url))) 0 16)))
          (when (not-in path mirror-urls)
            (setv (get mirror-urls path) url)
            (:= log (replace log :clones (+ log.clones 1))))
          (resume (MirrorReady :path path)))))
  (FetchCommit [mirror commit]
    (val url (get mirror-urls mirror))
    (val remote (next (gfor r world.remotes :if (= r.url url) r) None))
    (val known (and remote (any (gfor c remote.commits (= c.sha commit)))))
    (cond
      (not known) (resume FetchState.MISSING)
      (in #(url commit) fetched) (resume FetchState.PRESENT)
      True (do (.add fetched #(url commit))
               (:= log (replace log :fetches (+ log.fetches 1)))
               (resume FetchState.FETCHED))))
  (MaterializeTree [mirror commit dest reuse]
    (if (is-not reuse None)
        (do (val source (+ reuse "/"))
            (for [p (list files)]
              (when (and (.startswith p source)
                         (not (any (gfor n NOT-COPIED (in n (cut p (- (len source) 1) None)))))
                         (not (.endswith p (+ "/" ENV-MARKER))))
                (setv (get files (+ dest "/" (cut p (len source) None))) (get files p))))
            (:= log (replace log :copies (+ log.copies 1))))
        (do (val tree (next (gfor r world.remotes c r.commits :if (= c.sha commit) c)))
            (for [f tree.files]
              (setv (get files (+ dest "/" f.path)) f.text))
            (:= log (replace log :archives (+ log.archives 1)))))
    (resume None))
  (FileSha256 [path]
    (if (in path files)
        (do (<- digest str (text-sha256 (get files path)))
            (resume digest))
        (resume None)))
  (TreeHash [mirror commit path]
    (val tree (next (gfor r world.remotes c r.commits :if (= c.sha commit) c)))
    (val prefix (+ path "/"))
    (val parts (lfor f (sorted tree.files :key (fn [f] f.path)) :if (.startswith f.path prefix) (+ f.path "\0" f.text)))
    (resume (.hexdigest (hashlib.sha1 (.encode (.join "\n" parts))))))
  (EnsureNativeWheel [key package project-dir]
    (cond
      (in key wheels) (resume (WheelReady :path (get wheels key) :built False))
      (and uv-failure (= uv-failure.kind EnvFailureKind.NATIVE-BUILD-FAILED)) (resume uv-failure)
      True (do (<- (Delay world.cold-seconds))
               (val path (.format "/fake/wheels/{}-{}/{}.whl" package key package))
               (setv (get wheels key) path)
               (:= log (replace log :builds (+ log.builds 1)))
               (resume (WheelReady :path path :built True)))))
  (SyncProject [project-dir python groups no-install]
    (if (and uv-failure (in uv-failure.kind #(EnvFailureKind.LOCK-STALE EnvFailureKind.SYNC-FAILED
                                               EnvFailureKind.PYTHON-UNAVAILABLE)))
        (resume uv-failure)
        (do (<- lines tuple (lock-lines (.get files (+ project-dir "/uv.lock") "")))
            (val wanted (lfor #(name _) lines :if (not-in (get (.split name "==") 0) no-install) name))
            (val missing (lfor name wanted :if (not-in name cache) name))
            (.update cache missing)
            (<- (Delay (if missing world.cold-seconds world.warm-seconds)))
            (setv (get files (+ project-dir "/.venv/pyvenv.cfg")) (.format "python = {}\n" python))
            (:= log (replace log :syncs (+ log.syncs 1) :downloads (+ log.downloads (len missing))))
            (resume (SyncReport :downloaded (len missing))))))
  (InstallWheels [project-dir wheels]
    (for [w wheels]
      (setv (get files (.format "{}/.venv/wheels/{}" project-dir (get (.rsplit w "/" 1) -1))) w))
    (resume None))
  (WriteImportRoots [project-dir roots]
    (setv (get files (.format "{}/.venv/{}" project-dir ROOTS-PTH)) (.join "\n" roots))
    (resume None))
  (CompileTree [project-dir tree roots carry-from]
    (val prefix (+ tree "/"))
    (val sources (lfor p files :if (and (.startswith p prefix) (.endswith p SOURCE-SUFFIXES)
                                        (not (any (gfor n NOT-COPIED (in n p)))))
                       p))
    (:= log (replace log :compiles (+ log.compiles 1)
                         :carried (+ log.carried (if (is carry-from None) 0 (len sources)))))
    (resume (BytecodeReport :interpreter (.format "{}/.venv/bin/python" project-dir)
                            :compiled (if (is carry-from None) (len sources) 0)
                            :carried (if (is carry-from None) 0 (len sources)))))
  (ProbeImports [project-dir roots]
    (<- lines tuple (lock-lines (.get files (+ project-dir "/uv.lock") "")))
    (val third (frozenset (gfor #(_ tops) lines t tops t)))
    (var misplaced [])
    (for [root roots]
      (<- names frozenset (top-names files root))
      (.extend misplaced (sorted (& names third))))
    (resume (ProbeReport :child-protocol world.child-protocol :misplaced (tuple misplaced))))
  (WriteEnvMarker [root marker]
    (<- content dict (env-marker->json marker))
    (setv (get files (.format "{}/{}" root ENV-MARKER)) (json.dumps content :ensure-ascii False :sort-keys True))
    (resume None)))
