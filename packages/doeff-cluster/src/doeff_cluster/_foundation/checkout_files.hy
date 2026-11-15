;;; checkout の file を git を起こさずに読む本物の答え手(宣言の組み立てが起動のたびに git の子 process を
;;; 17〜21 本起こしていた)。ReadCheckoutFiles と FindCheckoutRoot(doeff_cluster._shared.intent.checkout_model)に答える。読むのは git の
;;; 置き場の file(HEAD・ref・packed-refs・config・index)と、index の項の作業木の file の lstat だけで、書かない。
;;;
;;;   FindCheckoutRoot   path の実の path(symlink を解く)から上へ、HEAD を持つ .git の dir か「gitdir: 」で始まる .git の file を探す。
;;;                      .git の dir の中の path は None(git rev-parse --show-toplevel も作業木の外として断る)。
;;;   ReadCheckoutFiles  根の .git から git の dir(作業木ごと)と共通の dir(commondir)を解き、HEAD を ref の file か packed-refs で commit へ
;;;                      解き、index(版 2・3)を読んで、項ごとに作業木の file を lstat で照らす(settled)。照らし方は git の ie_match_stat と
;;;                      同じ向き: mtime と ctime(ns まで)・inode・size(下 32 bit)・file の種類と実行の bit が index の値と同じで、項の mtime が
;;;                      index の file の mtime より前(racy でない)なら、git もその file を読み直さない。git が見ない dev・uid・gid は照らさない。
;;;                      読めない形(reftable・index の版 4・split index(link)・sparse index(sdir)・checksum を書かない index・生まれていない
;;;                      branch・壊れた file)は None — 読み手が git へ倒す。
;;;
;;; 測り(load 約 90 の 16 core の機体・index 23,560 項の checkout): lstat を作業木の dir の fd からの相対の名で引いて CPU 約 0.13 秒(絶対
;;; path で引くと symlink を辿り直して 0.25 秒)。git status の CPU 約 0.13 秒と同じ桁で、子 process の起動と待ちが無い。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import hashlib)
(import os)
(import stat)
(import struct)
(import doeff_cluster._shared.intent.checkout_model [CheckoutFiles ReadCheckoutFiles FindCheckoutRoot])

;; index の頭(印・版・項の数)と、項の固定の頭(ctime 秒・ns・mtime 秒・ns・dev・ino・mode・uid・gid・size — 各 32 bit)。
(val INDEX-SIGNATURE b"DIRC")
(val INDEX-VERSIONS #(2 3))
(val ENTRY-STAT (struct.Struct ">10I"))
;; 項の flags の bit(git の read-cache.c): assume-valid・拡張の flags が続く・stage の番号。拡張の flags の skip-worktree と intent-to-add。
(val ASSUME-VALID 0x8000)
(val EXTENDED 0x4000)
(val STAGE-MASK 0x3000)
(val SKIP-WORKTREE 0x4000)
(val INTENT-TO-ADD 0x2000)
;; 作業木を読まないと判じられない index の拡張(split index・sparse index)。
(val UNSETTLED-EXTENSIONS #(b"link" b"sdir"))
;; index の mode の種類(git の S_IFGITLINK = 0160000)。
(val GITLINK 0o160000)
(val LOW-32 0xFFFFFFFF)
(val NS 1000000000)
;; commit の名の長さ(SHA-1 と SHA-256 の 16 進)。
(val HEX-LENGTHS #(40 64))


(defn #^ (| str None) _read-text [#^ str path]
  ;; file の中身を文字列で読む(無い・読めない file は None)。
  (try
    (with [f (open path "r" :encoding "utf-8")] (.read f))
    (except [OSError] None)
    (except [UnicodeDecodeError] None)))


(defn #^ (| str None) _commit-name [#^ str text]
  ;; 16 進の commit の名なら小文字で、そうでなければ None。
  (setv name (.strip text))
  (if (and (in (len name) HEX-LENGTHS) (all (gfor c name (in c "0123456789abcdef")))) name None))


(defn #^ (| str None) _checkout-root [#^ str path]
  ;; path から上へ checkout の根を探す(頭の註)。
  (setv here (os.path.realpath path))
  (when (in ".git" (.split here os.sep))
    (return None))
  (while True
    (setv dot-git (os.path.join here ".git"))
    (cond
      (os.path.isdir dot-git)
      (when (os.path.isfile (os.path.join dot-git "HEAD"))
        (return here))
      (os.path.isfile dot-git)
      (do (setv text (_read-text dot-git))
          (when (and (is-not text None) (.startswith text "gitdir: "))
            (return here))))
    (setv parent (os.path.dirname here))
    (when (= parent here)
      (return None))
    (setv here parent)))


(defn #^ (| tuple None) _git-dirs [#^ str root]
  ;; 根の .git から #(作業木の git の dir・共通の dir)を解く(.git の file は gitdir: の行、git の dir の commondir は共通の dir への path)。
  (setv dot-git (os.path.join root ".git"))
  (cond
    (os.path.isdir dot-git) (setv git-dir (os.path.realpath dot-git))
    (os.path.isfile dot-git)
    (do (setv text (_read-text dot-git))
        (when (or (is text None) (not (.startswith text "gitdir: ")))
          (return None))
        (setv git-dir (os.path.realpath (os.path.join root (.strip (cut text (len "gitdir: ") None))))))
    True (return None))
  (setv common (_read-text (os.path.join git-dir "commondir")))
  #(git-dir (if (is common None) git-dir (os.path.realpath (os.path.join git-dir (.strip common))))))


(defn #^ (| str None) _packed-commit [#^ str packed #^ str ref]
  ;; packed-refs の中身から ref の commit を読む(注釈の行と peel の行 ^ を飛ばす)。
  (next (gfor line (.splitlines packed)
              :setv parts (.split line " " 1)
              :if (and (not (.startswith line "#")) (not (.startswith line "^"))
                       (= (len parts) 2) (= (.strip (get parts 1)) ref))
              (_commit-name (get parts 0)))
        None))


(defn #^ (| str None) _ref-commit [#^ str common-dir #^ str ref]
  ;; ref(refs/heads/… など)を、共通の dir の loose の ref か packed-refs で commit へ解く(symbolic な ref の先は辿らない — None)。
  (setv loose (_read-text (os.path.join common-dir #* (.split ref "/"))))
  (setv packed (if (is loose None) (_read-text (os.path.join common-dir "packed-refs")) None))
  (cond
    (is-not loose None) (_commit-name loose)
    (is-not packed None) (_packed-commit packed ref)
    True None))


(defn #^ (| str None) _head-commit [#^ str git-dir #^ str common-dir]
  ;; HEAD を commit へ解く(切り離した HEAD はそのまま・ref は _ref-commit)。
  (setv text (_read-text (os.path.join git-dir "HEAD")))
  (cond
    (is text None) None
    (.startswith text "ref: ") (_ref-commit common-dir (.strip (cut text (len "ref: ") None)))
    True (_commit-name text)))


(defn #^ (| str None) _config-digest [#^ str git-dir #^ str common-dir]
  ;; remote の URL を決める設定の file(共通の config と、在れば作業木の config.worktree)の中身の sha256。
  (setv worktree-config (os.path.join git-dir "config.worktree"))
  (try
    (with [f (open (os.path.join common-dir "config") "rb")] (setv common (.read f)))
    (setv own (if (os.path.isfile worktree-config)
                  (with [f (open worktree-config "rb")] (+ b"\0config.worktree\0" (.read f)))
                  b""))
    (except [OSError] (return None)))
  (.hexdigest (hashlib.sha256 (+ common own))))


(defn #^ bool _entry-matches [#^ os.stat-result seen #^ tuple want]
  ;; 作業木の file の lstat seen が index の項の値 want(ctime ns・mtime ns・ino・mode・size)と同じか(頭の註の照らし方)。
  (setv #(ctime mtime ino mode size) want)
  (and (= seen.st-mtime-ns mtime)
       (= seen.st-ctime-ns ctime)
       (= (& seen.st-ino LOW-32) ino)
       (= (& seen.st-size LOW-32) size)
       (if (= (stat.S-IFMT mode) stat.S-IFLNK)
           (stat.S-ISLNK seen.st-mode)
           (and (stat.S-ISREG seen.st-mode) (= (& seen.st-mode 0o100) (& mode 0o100))))))


(defn _index-entries [#^ bytes data #^ int count #^ int hash-size #^ int index-mtime]
  ;; index の項を頭から順に読む generator。1 項ごとに #(次の項の offset・名・照らす値・作業木を見ずに判じられるか)を出す
  ;; (壊れた index は ValueError)。
  (setv offset 12)
  (setv flags-at (+ ENTRY-STAT.size hash-size))
  (for [_ (range count)]
    (setv #(ctime-s ctime-n mtime-s mtime-n _dev ino mode _uid _gid size) (.unpack-from ENTRY-STAT data offset))
    (setv #(flags) (struct.unpack-from ">H" data (+ offset flags-at)))
    (setv extended (if (& flags EXTENDED) (get (struct.unpack-from ">H" data (+ offset flags-at 2)) 0) 0))
    (setv name-at (+ offset flags-at 2 (if (& flags EXTENDED) 2 0)))
    (setv name-end (.find data b"\0" name-at))
    (when (< name-end 0)
      (raise (ValueError "index の項の名が終わらない")))
    (setv mtime (+ (* mtime-s NS) mtime-n))
    (setv plain (not (or (& flags (| ASSUME-VALID STAGE-MASK))
                         (& extended (| SKIP-WORKTREE INTENT-TO-ADD))
                         (= (stat.S-IFMT mode) GITLINK)
                         (stat.S-ISDIR mode)
                         (>= mtime index-mtime))))
    ;; 項の長さは 8 byte の倍へ NUL で詰める(版 2・3)。
    (setv offset (+ offset (* (+ (// (- name-end offset) 8) 1) 8)))
    (yield #(offset (cut data name-at name-end) #((+ (* ctime-s NS) ctime-n) mtime ino mode size) plain))))


(defn #^ (| tuple None) _extension-names [#^ bytes data #^ int offset #^ int end]
  ;; 項の後の拡張(4 byte の印・4 byte の長さ・中身)の印の列(壊れていれば None)。
  (cond
    (= offset end) #()
    (> (+ offset 8) end) None
    True (do (setv #(signature size) (struct.unpack-from ">4sI" data offset))
             (setv rest (_extension-names data (+ offset 8 size) end))
             (if (is rest None) None (+ #(signature) rest)))))


(defn #^ (| os.stat-result None) _lstat-at [#^ int fd #^ bytes name]
  ;; dir の fd からの相対の名で lstat する(無い・引けない file は None)。
  (try
    (os.lstat name :dir-fd fd)
    (except [OSError] None)))


(defn #^ bool _worktree-matches [#^ str root #^ tuple entries]
  ;; index の項ごとに作業木の file を lstat で照らす(根の dir の fd からの相対の名で引く — 頭の註の測り)。1 つでも違えば False。
  (try
    (setv fd (os.open root (| os.O-RDONLY os.O-DIRECTORY)))
    (except [OSError] (return False)))
  (try
    (all (gfor #(_ name want _) entries
               :setv seen (_lstat-at fd name)
               (and (is-not seen None) (_entry-matches seen want))))
    (finally (os.close fd))))


(defn #^ (| tuple None) _index-reading [#^ str root #^ str git-dir #^ int hash-size]
  ;; index を読み #(末尾の checksum の 16 進・settled)を返す(読めない形は None — 頭の註)。
  (setv index-path (os.path.join git-dir "index"))
  (try
    (setv index-stat (os.stat index-path))
    (with [f (open index-path "rb")] (setv data (.read f)))
    (except [OSError] (return None)))
  (when (< (len data) (+ 12 hash-size))
    (return None))
  (setv #(signature version count) (struct.unpack-from ">4sII" data 0))
  (setv checksum (cut data (- (len data) hash-size) None))
  (when (or (!= signature INDEX-SIGNATURE) (not-in version INDEX-VERSIONS) (= checksum (bytes hash-size)))
    (return None))
  (try
    (setv entries (tuple (_index-entries data count hash-size index-stat.st-mtime-ns)))
    (except [#(ValueError struct.error)] (return None)))
  (setv extensions (_extension-names data (if entries (get (get entries -1) 0) 12) (- (len data) hash-size)))
  (when (or (is extensions None) (any (gfor name extensions (in name UNSETTLED-EXTENSIONS))))
    (return None))
  #((.hex checksum) (and (all (gfor e entries (get e 3))) (_worktree-matches root entries))))


(defn #^ (| CheckoutFiles None) read-checkout-files [#^ str path]
  "path の checkout の file を git を起こさずに読む(頭の註 — 読めない形は None)。"
  (setv root (_checkout-root path))
  (setv dirs (if (is root None) None (_git-dirs root)))
  (when (is dirs None)
    (return None))
  (setv #(git-dir common-dir) dirs)
  (setv head (_head-commit git-dir common-dir))
  (setv config (_config-digest git-dir common-dir))
  (setv index (if (is head None) None (_index-reading root git-dir (// (len head) 2))))
  (when (or (is config None) (is index None))
    (return None))
  (setv #(index-digest settled) index)
  (CheckoutFiles :root root :git-dir git-dir :head head :index-digest index-digest :config-digest config :settled settled))


(defhandler checkout-files-reader []
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; checkout の file を git を起こさずに読む実 I/O の答え手(頭の註 — doeff-linter の目録 world_handlers.json の file)。
  (ReadCheckoutFiles [path]
    (resume (read-checkout-files path)))
  (FindCheckoutRoot [path]
    (resume (_checkout-root path))))
