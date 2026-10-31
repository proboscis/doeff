;;; 汎用の file system の effect(file_effects.hy)の本物の答え手 os-file-handler。os・shutil・fcntl を呼んで値を
;;; 詰め替えるだけで、判断を持たない。失敗(OSError)は FileFailed の値で答える。
;;; 空きの読み(ReadDiskFree・ReadDiskUsage)は機体で読み手を選ぶ: Linux = statvfs・macOS = statvfs の空きを床にした volume の資源の key
;;; の空き(darwin_volume.hy — statvfs は macOS が自動で空ける領域を使えない空きとして引く)。
;;;
;;; 錠の取り(AcquireLock)は取れるまで待つ(fcntl.flock)。os-file-handler はその待ちを呼んだ thread で待つので、1 つの scheduler に task が
;;; 並ぶ process では、錠を持った task が待ち(子 process・HTTP)へ移った間に別の task が同じ錠を取りに来ると scheduler の thread ごと
;;; 塞がり、錠を持つ task が戻れず錠を返せない(着地の service の口を 1 つの scheduler の Program にする時に見つけた)。
;;; offloaded-lock-handler は錠の取りだけを呼び 1 つに thread 1 本(offloaded_call.hy の ThreadPerCall)で待ち、他の task を回し続ける —
;;; os-file-handler の内側に置き、外側に scheduled が要る。待つ task が取り消された後に取れた錠は、その場で返す(持ち主の無い錠を残さない)。
;;; offloaded-tree-handler は木の数え(MeasureTree)と消し(RemoveTree)と様子つきの歩き(StatTree)を同じく呼び 1 つに thread 1 本で待つ
;;; (worker の掃除が数千の file の木を数え・消す間、詰まった disk の上でも他の task(worker の調整ループ)を回し続ける。
;;; 掃除が 10 万の file の bytecode の保存先を歩く間も同じ)。置き場は offloaded-lock-handler と同じ(os-file-handler の
;;; 内側・外側に scheduled)。
;;; offloaded-file-handler は tick が触る同期の file の効果(WriteText・ReadText・StatPath・ListDirectory・MakeDirectory・RenamePath・
;;; ReadDiskFree)を同じく呼び 1 つに thread 1 本で待つ(本体の thread が disk の block 層の待ちで D 状態になると、同じ
;;; thread の背景の heartbeat も一緒に止まった。待つ間も本体は他の task を回す)。置き場は offloaded-tree-handler と同じ。
;;; 2026-10-18 に file 1 つと木の写しの効果(ReadBytes・WriteBytes・AppendText・CopyFile・CopyTree・WalkTree・LinkFile・MakeSymlink・
;;; ReadDiskUsage)も受けるようにした(agora の turn-host が scheduler の thread で会話の記録を読み・写し、待ち行列の
;;; 浅い詰まった disk の上で全部の手番が止まった)。追記の log の書き手の AppendBytes・TruncateFile・SyncDirectory も同じく受ける。受けないのは、錠の取り(offloaded-lock-handler — 取り消しの後の錠を返す後始末が要る)・
;;; 錠の返し(disk を待たない)・木の数えと消しと様子つきの歩き(offloaded-tree-handler — IO 優先度を下げる)・bytecode の作り(自分の
;;; process の組で回す)。
(require doeff-hy.macros [defhandler defk deff <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import ctypes)
(import errno)
(import fcntl)
(import collections.abc [Callable])
(import os)
(import platform)
(import shutil)
(import stat)
(import sys)
(import tempfile)
(import time)
(import pathlib [Path])
(import doeff_core_effects._file_effects [PathKind FileFailed PathStat DirEntry EntryStat LockHeld DiskUsage Appended StatPath ReadText ReadBytes
                                         WriteText WriteBytes AppendText AppendBytes TruncateFile SyncDirectory MakeDirectory ListDirectory
                                         WalkTree StatTree CopyFile CopyTree RenamePath RemoveTree
                                         AcquireLock ReleaseLock ReadDiskFree ReadDiskUsage MeasureTree LinkFile
                                         CompilePythonSources MakeSymlink])
(import doeff_core_effects._python_bytecode [compile-python-sources])
(import doeff_core_effects._darwin_volume [darwin-volume-free])
(import doeff_core_effects._offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])

;; offloaded-lock-handler の thread(錠の取り 1 つに thread 1 本 — 同時の数の上限は錠を取りに来る task の数)。
(val LOCK-THREADS (ThreadPerCall))
;; ioprio_set の IO 優先度の組み立て(linux/ioprio.h): 優先度 = (種類 << 13) | 段。IOPRIO_WHO_PROCESS で who = 0 は呼んだ thread 自身。
(val IOPRIO-CLASS-SHIFT 13)
(val IOPRIO-CLASS-IDLE 3)
(val IOPRIO-WHO-PROCESS 1)
;; ioprio_set の syscall 番号(機体の種類 → 番号。表に無い機体では何もしない)。
(val IOPRIO-SET-SYSCALL {"x86_64" 251 "aarch64" 30})


(deff lower-io-priority []  ; defk にできない: thread の始めに呼ばれる入口(ThreadPerCall の on-start — VM の外)
  {:pre [] :post [(: % bool)]}
  "呼んだ thread の IO 優先度を IDLE にして、本体の小さい IO を先に通すため(zeus の scheduler の mq-deadline は優先度の種類で順を付ける — 段は
   見ないので BE の最低では効かない)。Linux 以外・表に無い機体・syscall の失敗では何もせず False を返す(掃除は優先度が下がらなくても進む)。"
  (if (and (= sys.platform "linux") (in (platform.machine) IOPRIO-SET-SYSCALL))
      (try
        (setv libc (ctypes.CDLL None :use-errno True))
        (= 0 (.syscall libc (get IOPRIO-SET-SYSCALL (platform.machine)) IOPRIO-WHO-PROCESS 0
                       (<< IOPRIO-CLASS-IDLE IOPRIO-CLASS-SHIFT)))
        (except [OSError] False))
      False))


;; offloaded-tree-handler の thread(木の数え・消し・様子つきの歩き 1 つに thread 1 本 — 本体の IO を先に通すため IO 優先度を IDLE に下げる)。
(val TREE-THREADS (ThreadPerCall :on-start lower-io-priority))
;; offloaded-file-handler の thread(file 1 つの読み・書き・追記・写し・様子・一覧・dir 作り・改名・張り・空きの読みと、木の写しと歩き
;; 1 つに thread 1 本)。
(val FILE-THREADS (ThreadPerCall))


(defk failed [path error]
  {:pre [(: path str) (: error OSError)] :post [(: % FileFailed)]}
  "OSError を FileFailed の値にするため。"
  (FileFailed :path path :detail (str error)))


;; stat の mode の種類の印(stat.S_IFMT の値)→ 種類(kind-of-mode と stat-tree が読む — 表に無い印は OTHER)。
(val KIND-OF-FORMAT {stat.S-IFLNK PathKind.SYMLINK stat.S-IFDIR PathKind.DIRECTORY stat.S-IFREG PathKind.FILE})


(defk kind-of-mode [mode]
  {:pre [(: mode int)] :post [(: % PathKind)]}
  "stat の mode から種類を読むため。"
  (.get KIND-OF-FORMAT (stat.S-IFMT mode) PathKind.OTHER))


(defk stat-path [path follow-symlinks]
  {:pre [(: path str) (: follow-symlinks bool)] :post [(: % (| PathStat FileFailed))]}
  "path の様子を読むため(follow-symlinks = True は symlink を辿る・False は os.lstat・無ければ MISSING)。"
  (try
    (val found (if follow-symlinks (os.stat path) (os.lstat path)))
    (<- kind PathKind (kind-of-mode found.st-mode))
    (PathStat :kind kind :real-path (os.path.realpath path) :size found.st-size :modified found.st-mtime)
    (except [FileNotFoundError]
      (PathStat :kind PathKind.MISSING :real-path (os.path.abspath path) :size 0 :modified 0.0))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk write-content [path content mode replace sync]
  {:pre [(: path str) (: content (| str bytes)) (: mode (| int None)) (: replace bool) (: sync bool)] :post [(: % (| FileFailed None))]}
  "file を書くため(replace = 同じ dir の別名に書いてから置き換える・sync = 閉じる前に fsync)。"
  (val binary (isinstance content bytes))
  (try
    (if replace
        (do (val parent (os.path.dirname (os.path.abspath path)))
            (val staged (tempfile.NamedTemporaryFile :mode (if binary "wb" "w") :dir parent :delete False
                                                     #** (if binary {} {"encoding" "utf-8"})))
            (with [out staged] (.write out content) (when sync (_sync out)))
            (os.replace staged.name path))
        (with [out (open path (if binary "wb" "w") #** (if binary {} {"encoding" "utf-8"}))]
          (.write out content)
          (when sync (_sync out))))
    (when (is-not mode None)
      (os.chmod path mode))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk make-directory [path mode]
  {:pre [(: path str) (: mode (| int None))] :post [(: % (| FileFailed None))]}
  "dir を親ごと作るため(mode は新しく作った最後の dir にだけ与える)。"
  (try
    (val existed (os.path.isdir path))
    (os.makedirs path :exist-ok True)
    (when (and (is-not mode None) (not existed))
      (os.chmod path mode))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk entry-of [path name]
  {:pre [(: path str) (: name str)] :post [(: % DirEntry)]}
  "dir の中の 1 つを DirEntry にするため(symlink は辿らない)。"
  (<- kind PathKind (kind-of-mode (. (os.lstat path) st-mode)))
  (DirEntry :name name :kind kind))


(defk list-directory [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の直下を名の順に並べるため。並べた後で種類を読む前に消えた名は飛ばす(並べた時点で在った名の写し — /proc のように並べる間に
   名が消える dir で、消えた 1 つが並べ全体を FileFailed にしない)。"
  (try
    (tuple (lfor name (sorted (os.listdir path))
                 :setv seen (try (os.lstat (os.path.join path name)) (except [FileNotFoundError] None))
                 :if (is-not seen None)
                 (DirEntry :name name :kind (.get KIND-OF-FORMAT (stat.S-IFMT seen.st-mode) PathKind.OTHER))))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk walk-tree [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(DirEntry ...)) FileFailed))]}
  "dir の下の全部を相対 path の順に並べるため(symlink の dir へは入らない)。"
  (try
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (val entries [])
    (for [#(root dirs files) (os.walk path)]
      (for [name (+ dirs files)]
        (val full (os.path.join root name))
        (<- entry DirEntry (entry-of full (.replace (os.path.relpath full path) os.sep "/")))
        (.append entries entry)))
    (tuple (sorted entries :key (fn [e] e.name)))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk stat-tree [path]
  {:pre [(: path str)] :post [(: % (| (get tuple #(EntryStat ...)) FileFailed))]}
  "dir の下の全部を様子(os.lstat — symlink は辿らない)つきで相対 path の順に並べるため(symlink の dir へは入らない・一覧の後で様子を
   読む前に消えた物は StatPath の無い path と同じく kind MISSING・大きさ 0・mtime 0)。相対 path は dir ごとに 1 度求める(file が 10 万の
   木でも file ごとに relpath を呼ばない)。無い path は無いと、dir でない path は dir でないと断る(memory の置き場と同じ断り)。"
  (try
    (when (not (os.path.exists path))
      (raise (FileNotFoundError 2 "No such file or directory" path)))
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (val found (lfor #(root dirs files) (os.walk path)
                     :setv base (.replace (os.path.relpath root path) os.sep "/")
                     name (+ dirs files)
                     :setv seen (try (os.lstat (os.path.join root name)) (except [FileNotFoundError] None))
                     (EntryStat :name (if (= base ".") name (+ base "/" name))
                                :kind (if (is seen None) PathKind.MISSING (.get KIND-OF-FORMAT (stat.S-IFMT seen.st-mode) PathKind.OTHER))
                                :size (if (is seen None) 0 seen.st-size)
                                :modified (if (is seen None) 0.0 seen.st-mtime))))
    (tuple (sorted found :key (fn [e] e.name)))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk remove-tree [path]
  {:pre [(: path str)] :post [(: % (| FileFailed None))]}
  "file か dir を中身ごと消すため(symlink は symlink だけを消す)。"
  (try
    (if (and (os.path.isdir path) (not (os.path.islink path)))
        (shutil.rmtree path)
        (os.unlink path))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk acquire-lock [path]
  {:pre [(: path str)] :post [(: % (| LockHeld FileFailed))]}
  "錠の file を排他で取るため(取れるまで待つ・手札 = 開いた file の番号)。"
  (try
    (val fd (os.open path (| os.O-RDWR os.O-CREAT) 0o644))
    (fcntl.flock fd fcntl.LOCK-EX)
    (LockHeld :path path :token fd)
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk guarded [path action]
  {:pre [(: path str) (: action (get Callable #([] object)))] :post [(: % (| FileFailed None))]}
  "答えの無い操作 1 つを走らせ、OSError を FileFailed にするため。"
  (try
    (action)
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk read-file [path binary limit offset]
  {:pre [(: path str) (: binary bool) (: limit (| int None)) (: offset int)] :post [(: % (| str bytes FileFailed))]}
  "file の中身を読むため(text は UTF-8・読めない byte は置き換え・bytes は位置 offset から先の limit byte だけ — None = 終わりまで)。"
  (try
    (cond
      (not binary) (.read-text (Path path) :encoding "utf-8" :errors "replace")
      (and (is limit None) (= offset 0)) (.read-bytes (Path path))
      True (with [handle (open path "rb")]
             (.seek handle offset)
             (.read handle (if (is limit None) -1 limit))))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defn _sync [handle]  ; defk にできない: 書きの with の中から呼ぶ手続き(Program を返すと実行されない)
  (.flush handle)
  (os.fsync (.fileno handle)))


(defn #^ float _timed-sync [handle sync]  ; defk にできない: 書きの with の中から呼ぶ手続き(Program を返すと実行されない)
  "handle の中身を flush し、sync なら fsync して、その秒を返す(sync でなければ 0.0)。"
  (.flush handle)
  (if sync
      (do (setv started (time.monotonic))
          (os.fsync (.fileno handle))
          (- (time.monotonic) started))
      0.0))


(defk append-bytes [path content sync]
  {:pre [(: path str) (: content bytes) (: sync bool)] :post [(: % (| Appended FileFailed))]}
  "file の末尾に bytes を足すため(無ければ作る・sync = 足した後に fsync して、その秒を答えに載せる)。"
  (try
    (with [out (open path "ab")]
      (.write out content)
      (val seconds (_timed-sync out sync))
      (Appended :size (.tell out) :synced-seconds seconds))
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk truncate-file [path size sync]
  {:pre [(: path str) (: size int) (: sync bool)] :post [(: % (| FileFailed None))]}
  "在る file を size byte に切り詰めるため(今の大きさより大きい size は延ばさず Invalid argument で断る・sync = 切り詰めた後に fsync)。"
  (try
    (with [handle (open path "r+b")]
      (when (or (< size 0) (> size (. (os.fstat (.fileno handle)) st-size)))
        (raise (OSError errno.EINVAL (os.strerror errno.EINVAL) path)))
      (.truncate handle size)
      (_timed-sync handle sync))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defk sync-directory [path]
  {:pre [(: path str)] :post [(: % (| FileFailed None))]}
  "dir の変更(中の file の作成・改名)を disk へ落とすため(dir を開いて fsync — dir でない path は Not a directory で断る)。"
  (try
    (val fd (os.open path (| os.O-RDONLY os.O-DIRECTORY)))
    (try
      (os.fsync fd)
      (finally (os.close fd)))
    None
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defn _append [path text sync]  ; defk にできない: guarded に渡す callback
  (with [handle (open path "a" :encoding "utf-8")]
    (.write handle text)
    (when sync (_sync handle))))


(defn _release [held]  ; defk にできない: guarded に渡す callback
  (try (fcntl.flock held.token fcntl.LOCK-UN) (finally (os.close held.token))))


(defk free-reader-for [system]
  {:pre [(: system str)] :post [(: % (| Callable None))]}
  "機体(platform.system の名)の空きの読み直し手を選ぶため: macOS(Darwin)は statvfs が引く macOS の自動で空ける
   領域の一部も数える darwin-volume-free(darwin_volume.hy の頭の註)・他の機体(Linux)は None = statvfs の空きのまま。"
  (if (= system "Darwin") darwin-volume-free None))


(defk reread-free [probe floor reader]
  {:pre [(: probe str) (: floor int) (: reader (| Callable None))] :post [(: % int)]}
  "statvfs の空き floor を機体の読み直し手で読み直すため。答えが無い時と floor より小さい答えは floor のまま — statvfs の空きは何も消さず
   に今すぐ書ける量なので、読み直しで今より小さく測らない。"
  (if (is reader None)
      floor
      (do (<- answered (| int None) (reader probe))
          (if (is answered None) floor (max floor answered)))))


(defk disk-usage [path]
  {:pre [(: path str)] :post [(: % (| DiskUsage FileFailed))]}
  "path を含む file system の総量と空きを読むため(無い path は在る親で測る・空きは機体の読み直し手で読み直す — reread-free)。"
  (var probe (Path path))
  (while (not (.exists probe)) (:= probe probe.parent))
  (val usage (try (shutil.disk-usage probe) (except [error OSError] error)))
  (when (isinstance usage OSError)
    (return (FileFailed :path path :detail (str usage))))
  (<- reader (| Callable None) (free-reader-for (platform.system)))
  (<- free int (reread-free (str probe) usage.free reader))
  (DiskUsage :total usage.total :free free))


(defk disk-free [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "path を含む file system の空きを読むため(disk-usage の空き — 無い path は在る親で測る)。"
  (<- usage (| DiskUsage FileFailed) (disk-usage path))
  (if (isinstance usage FileFailed) usage usage.free))


(defk measure-tree [path]
  {:pre [(: path str)] :post [(: % (| int FileFailed))]}
  "dir の下の file の大きさの合計を測るため(symlink は辿らずリンク自身の大きさ・hardlink は重ねて数える・測る間に消えた file は数えない)。"
  (try
    (when (not (os.path.isdir path))
      (raise (NotADirectoryError 20 "Not a directory" path)))
    (var total 0)
    (for [#(root _ files) (os.walk path)]
      (for [name files]
        (:= total (+ total (try (. (os.lstat (os.path.join root name)) st-size) (except [OSError] 0))))))
    total
    (except [error OSError]
      (<- answer FileFailed (failed path error))
      answer)))


(defhandler os-file-handler
  "file system の effect に、本物の file system(os・shutil・tempfile・fcntl)をその場で呼んで答える。"
  ;; 本物の file system(頭の註)。
  (StatPath [path follow-symlinks]
    (<- answer (stat-path path follow-symlinks))
    (resume answer))
  (ReadText [path]
    (<- answer (read-file path False None 0))
    (resume answer))
  (ReadBytes [path limit offset]
    (<- answer (read-file path True limit offset))
    (resume answer))
  (WriteText [path text mode replace sync]
    (<- answer (write-content path text mode replace sync))
    (resume answer))
  (WriteBytes [path content mode replace sync]
    (<- answer (write-content path content mode replace sync))
    (resume answer))
  (AppendText [path text sync]
    (<- answer (guarded path (fn [] (_append path text sync))))
    (resume answer))
  (AppendBytes [path content sync]
    (<- answer (append-bytes path content sync))
    (resume answer))
  (TruncateFile [path size sync]
    (<- answer (truncate-file path size sync))
    (resume answer))
  (SyncDirectory [path]
    (<- answer (sync-directory path))
    (resume answer))
  (MakeDirectory [path mode]
    (<- answer (make-directory path mode))
    (resume answer))
  (ListDirectory [path]
    (<- answer (list-directory path))
    (resume answer))
  (WalkTree [path]
    (<- answer (walk-tree path))
    (resume answer))
  (StatTree [path]
    (<- answer (stat-tree path))
    (resume answer))
  (CopyFile [source target]
    (<- answer (guarded source (fn [] (shutil.copyfile source target))))
    (resume answer))
  (LinkFile [source target]
    (<- answer (guarded source (fn [] (os.link source target))))
    (resume answer))
  (CompilePythonSources [tree items jobs roots]
    (resume (compile-python-sources tree items jobs roots)))
  (CopyTree [source target]
    (<- answer (guarded source (fn [] (shutil.copytree source target :symlinks True :dirs-exist-ok True))))
    (resume answer))
  (MakeSymlink [path target]
    (<- answer (guarded path (fn [] (os.symlink target path))))
    (resume answer))
  (RenamePath [source target]
    (<- answer (guarded source (fn [] (os.replace source target))))
    (resume answer))
  (RemoveTree [path]
    (<- answer (remove-tree path))
    (resume answer))
  (AcquireLock [path]
    (<- answer (acquire-lock path))
    (resume answer))
  (ReleaseLock [held]
    (<- answer (guarded held.path (fn [] (_release held))))
    (resume answer))
  (ReadDiskFree [path]
    (<- answer (disk-free path))
    (resume answer))
  (ReadDiskUsage [path]
    (<- answer (disk-usage path))
    (resume answer))
  (MeasureTree [path]
    (<- answer (measure-tree path))
    (resume answer)))


(deff release-abandoned [answer]  ; defk にできない: Executor の thread で回す後始末の入口(offloaded の abandon — VM の外)
  {:pre [(: answer (| LockHeld FileFailed))] :post [(: % None)]}
  "待つ task が取り消された後に取れた錠を返すため(取れなかった答えは何も持たない)。"
  (when (isinstance answer LockHeld)
    (_release answer))
  None)


(defhandler offloaded-lock-handler
  "AcquireLock に、錠の取りを別の thread で待って答える(他の file の effect は外側の答え手へ通す)。"
  ;; 錠の取りだけを thread で待つ答え手(頭の註)。他の file の effect は外側の os-file-handler が答える。
  (AcquireLock [path]
    (<- answer (| LockHeld FileFailed) (offloaded LOCK-THREADS (fn [] (run-detached (acquire-lock path))) release-abandoned))
    (resume answer)))


(defhandler offloaded-tree-handler
  "MeasureTree・RemoveTree・StatTree に、木の数え・消し・様子つきの歩きを別の thread で待って答える(他の file の effect は外側の答え手へ通す)。"
  ;; 木の数え(MeasureTree)と消し(RemoveTree)と様子つきの歩き(StatTree)だけを thread で待つ答え手(頭の註)。他の file の effect は外側の
  ;; os-file-handler が答える。待つ task が取り消された後の答えは何も持たない(数えは数・消しは終わった印・歩きは様子の列)— 後始末は要らない。
  (MeasureTree [path]
    (<- answer (| int FileFailed) (offloaded TREE-THREADS (fn [] (run-detached (measure-tree path))) keep-nothing))
    (resume answer))
  (RemoveTree [path]
    (<- answer (| FileFailed None) (offloaded TREE-THREADS (fn [] (run-detached (remove-tree path))) keep-nothing))
    (resume answer))
  (StatTree [path]
    (<- stats (| tuple FileFailed) (offloaded TREE-THREADS (fn [] (run-detached (stat-tree path))) keep-nothing))
    (resume stats)))


(defhandler offloaded-file-handler
  "同期の file の effect(読み・書き・追記・写し・改名・張り・dir 作りなど)に、別の thread で待って答える(錠と木の effect は外側の答え手へ通す)。"
  ;; 同期の file の効果(頭の註の一覧 — 錠と木の数え・消し・様子つきの歩きと bytecode の作りの外の全部)を thread で待つ答え手。他の file の
  ;; effect は外側の答え手(offloaded-lock-handler・offloaded-tree-handler・os-file-handler)が答える。待つ task が取り消された後の答えは
  ;; 何も持たない(書き・追記・写し・改名・張り・dir 作りは終わった印、読みは値)— 後始末は要らない。走り出した書きと写しは走り切る。
  (WriteText [path text mode replace sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (write-content path text mode replace sync))) keep-nothing))
    (resume answer))
  (ReadText [path]
    (<- answer (| str FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (read-file path False None 0))) keep-nothing))
    (resume answer))
  (StatPath [path follow-symlinks]
    (<- answer (| PathStat FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (stat-path path follow-symlinks))) keep-nothing))
    (resume answer))
  (ListDirectory [path]
    (<- answer (| tuple FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (list-directory path))) keep-nothing))
    (resume answer))
  (MakeDirectory [path mode]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (make-directory path mode))) keep-nothing))
    (resume answer))
  (RenamePath [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (os.replace source target))))) keep-nothing))
    (resume answer))
  (ReadDiskFree [path]
    (<- answer (| int FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (disk-free path))) keep-nothing))
    (resume answer))
  (ReadDiskUsage [path]
    (<- answer (| DiskUsage FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (disk-usage path))) keep-nothing))
    (resume answer))
  (ReadBytes [path limit offset]
    (<- answer (| bytes FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (read-file path True limit offset))) keep-nothing))
    (resume answer))
  (WriteBytes [path content mode replace sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (write-content path content mode replace sync))) keep-nothing))
    (resume answer))
  (AppendText [path text sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded path (fn [] (_append path text sync))))) keep-nothing))
    (resume answer))
  (AppendBytes [path content sync]
    (<- answer (| Appended FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (append-bytes path content sync))) keep-nothing))
    (resume answer))
  (TruncateFile [path size sync]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (truncate-file path size sync))) keep-nothing))
    (resume answer))
  (SyncDirectory [path]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (sync-directory path))) keep-nothing))
    (resume answer))
  (CopyFile [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (shutil.copyfile source target)))))
                                              keep-nothing))
    (resume answer))
  (CopyTree [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS
                                              (fn [] (run-detached (guarded source (fn [] (shutil.copytree source target :symlinks True
                                                                                                            :dirs-exist-ok True)))))
                                              keep-nothing))
    (resume answer))
  (WalkTree [path]
    (<- answer (| tuple FileFailed) (offloaded FILE-THREADS (fn [] (run-detached (walk-tree path))) keep-nothing))
    (resume answer))
  (LinkFile [source target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded source (fn [] (os.link source target))))) keep-nothing))
    (resume answer))
  (MakeSymlink [path target]
    (<- answer (| FileFailed None) (offloaded FILE-THREADS (fn [] (run-detached (guarded path (fn [] (os.symlink target path))))) keep-nothing))
    (resume answer)))
