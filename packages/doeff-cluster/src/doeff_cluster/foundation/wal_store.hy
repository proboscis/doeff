;;; coordinator の耐久の置き場の file の I/O: 追記の log(wal.jsonl)+ まとめ直した写し(snapshot.json)の byte を書き・読み・切り詰める。
;;; 行と写しの形(checksum つきの JSON の綴りと検め・読み直しの当て方)は coordinator/protocol/wal_format.hy、両方を組んで Persist に
;;; 答えるのは coordinator/protocol/store.hy の置き場の口(#2785 — この module は形を持たず、層 protocol の module を読まない)。
;;;
;;; - Persist 1 回 = log へ綴った 1 行を追記して fsync 1 回(append-line)。調停ループはこれが戻ってから返事をする(group commit:
;;;   fsync の間に届いた要求は次のまとまりに入り、まとめて 1 回の fsync で済む)。
;;; - log が max-log-bytes を超えたら、置き場の口が全部のキーを写しに綴り直し、この module が一時 file → fsync → rename → dir の fsync で
;;;   置いてから log を空にする(write-snapshot)。写しは「どのまとまりまで含むか」(seq)を持つので、空にする前に落ちても読み直しで
;;;   二重に当てない。
;;; - 読み直し: 写しと log の byte を返し(check-place・read-snapshot-bytes・read-log-lines)、置き場の口が検めた後で、最後の読めない 1 行だけをその手前で切り詰める
;;;   (drop-tail)。途中の破損では何も書き換えない(置き場の口が起動を断る)。
;;; I/O はこの module の中だけ(調停ループの Program は Persist の effect しか知らない)。scheduler の外の thread は作らない。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import os)
(import sys)
(import time)
(import collections.abc [Callable])
(import typing [BinaryIO])
(import pathlib [Path])

(setv MAX-LOG-BYTES (* 32 1024 1024))
(setv SLOW-FSYNC-SECONDS 0.5)
(setv MOVED-MARK "MOVED")


(defn #^ None write-durably [#^ Path target #^ bytes data]
  "一時 file に書いて fsync し、rename してから dir を fsync する。"
  (setv tmp (Path (+ (str target) ".tmp")))
  (with [handle (open tmp "wb")]
    (.write handle data)
    (.flush handle)
    (os.fsync (.fileno handle)))
  (os.replace tmp target)
  (fsync-dir target.parent))


(defn #^ None fsync-dir [#^ Path d]
  (setv fd (os.open (str d) os.O_RDONLY))
  (try (os.fsync fd) (finally (os.close fd))))


;; --- 置き場(I/O)---------------------------------------------------------------------------------

(defclass WalStore []
  "dir の中の snapshot.json と wal.jsonl の byte の I/O。kv / seq = いま耐久になっている全部のキーと最後のまとまりの番号(まとめ直しの
   材料 — 置き場の口が読み直しと書きの後に進める)。recovered = 読み直しで最後の読めない行を捨てた時の記録(捨てた byte 数・理由・
   残した byte 数 — coordinator が起動の行と計器に出す)。fsync-seconds = 直近の fsync の時間(秒)の記録(log の遅さを測る)。"
  (defn #^ None __init__ [self #^ str directory #^ int [max-log-bytes MAX-LOG-BYTES] #^ Callable [fsync os.fsync]]
    (setv self.dir (Path directory) self.max-log-bytes max-log-bytes self.fsync fsync
          self.snapshot (/ self.dir "snapshot.json") self.log (/ self.dir "wal.jsonl")
          self.kv {} self.seq 0 self.handle None self.fsync-seconds []
          self.recovered None))

  (defn #^ bool exists [self] (or (.exists self.snapshot) (.exists self.log)))

  (defn #^ None check-place [self]
    "読み直しの前に置き場の dir を作り、移した後の古い置き場なら起動を断る。中身は書き換えない。"
    (.mkdir self.dir :parents True :exist-ok True)
    ;; 置き場を別の volume へ移した後の古い置き場には印(MOVED)を置く。古い置き場から起動すると、移した後の書き
    ;; (版の番号を含む)が巻き戻るので、起動を断る(docs/experiment-log.md の「coordinator の置き場」)。
    (setv moved (/ self.dir MOVED-MARK))
    (when (.exists moved)
      (raise (RuntimeError (.format "この置き場は移した後の古い物です({}): {}" moved
                                    (.strip (.read-text moved :encoding "utf-8")))))))

  (defn #^ (| bytes None) read-snapshot-bytes [self]
    "写しの byte(写しがまだ無ければ None)。"
    (if (.exists self.snapshot) (.read-bytes self.snapshot) None))

  (defn #^ list read-log-lines [self]
    "log の行(改行つきの byte の list・log がまだ無ければ空)。最後の行は改行を持たないことがある(fsync の途中で落ちた)。"
    (if (.exists self.log)
        (with [handle (open self.log "rb")] (list handle))
        []))

  (defn #^ None drop-tail [self #^ int good #^ int dropped #^ str reason]
    "読み直しで最後の読めない 1 行を捨てる: log を good byte で切り詰めて fsync し、捨てた記録を recovered に残して 1 行出す。"
    (setv self.recovered {"dropped" dropped "reason" reason "kept" good})
    (print (.format "coordinator: log の最後の読めない行を捨てた({} byte・{})" dropped reason) :file sys.stderr :flush True)
    (with [handle (open self.log "r+b")]
      (.truncate handle good)
      (.flush handle)
      (self.fsync (.fileno handle))))

  (defn #^ BinaryIO open-log [self]
    "追記用の handle を返す(まだ開いていなければ開く)。返り値は開いている handle で、None を含まない。"
    (when (is self.handle None)
      (setv self.handle (open self.log "ab")))
    self.handle)

  (defn #^ int append-line [self #^ bytes line]
    "綴った 1 行(seq のまとまり)を log へ追記し、fsync してから戻る。返り値 = 追記の後の log の大きさ(byte — まとめ直しの判断に使う)。"
    (setv handle (.open-log self))
    (.write handle line)
    (.flush handle)
    (setv started (time.monotonic))
    (self.fsync (.fileno handle))
    (setv took (- (time.monotonic) started))
    (.append self.fsync-seconds took)
    (setv self.fsync-seconds (cut self.fsync-seconds -200 None))
    ;; 遅い fsync は 1 回ずつ出す(置き場の詰まりの時刻と大きさを、返事の遅さの記録と突き合わせられるように)。
    (when (>= took SLOW-FSYNC-SECONDS)
      (print (.format "coordinator: 遅い fsync {:.2f} 秒({} byte・まとまり {})" took (len line) self.seq)
             :file sys.stderr :flush True))
    ;; 200 まとまりごとに fsync の時間を 1 行出す(返事の遅さの内訳を後から測れるように)。
    (when (= (% self.seq 200) 0)
      (print (.format "coordinator: fsync {}" (.fsync-stats self)) :file sys.stderr :flush True))
    (.tell handle))

  (defn #^ None write-snapshot [self #^ bytes data]
    "綴った写し(全部のキー・checksum つき)を置き(fsync 済み)、その後で log を空にする。"
    (setv started (time.monotonic))
    (write-durably self.snapshot data)
    (when self.handle (.close self.handle) (setv self.handle None))
    (with [handle (open self.log "wb")]
      (.flush handle)
      (self.fsync (.fileno handle)))
    (print (.format "coordinator: log をまとめ直した({:.2f} 秒・キー {})" (- (time.monotonic) started) (len self.kv))
           :file sys.stderr :flush True))

  (defn #^ dict fsync-stats [self]
    (setv xs (sorted self.fsync-seconds))
    (if xs
        {"count" (len xs) "p50" (get xs (// (len xs) 2)) "max" (get xs -1)}
        {"count" 0})))
