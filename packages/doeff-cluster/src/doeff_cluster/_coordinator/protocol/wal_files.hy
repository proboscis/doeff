;;; coordinator の耐久の置き場(foundation/wal_store の帳簿 WalStore)の file の読み書き — 全部 file の effect(doeff-core-effects の file_effects)で、
;;; 答えるのは入口が並べる答え手(本番は os-file-handler・検は本物の一時 dir か memory-file-handler)。以前は WalStore の method が open・fsync・rename を
;;; 直に呼んでいた(#2943)。行と写しの形は wal_format.hy、帳簿と file を組んで Persist に答えるのは store.hy の置き場の口。
;;;
;;;   wal-check-place         置き場の dir を作り、移した後の古い置き場なら起動を断る(中身は書き換えない)
;;;   wal-exists              写しか log が在るか
;;;   wal-read-snapshot       写しの byte(まだ無ければ None)
;;;   wal-read-log-lines      log の行(改行つきの byte の list・まだ無ければ空)
;;;   wal-drop-tail           読み直しで最後の読めない 1 行を捨てる(log を切り詰めて fsync)
;;;   wal-append-line         綴った 1 行を log へ追記し、fsync してから戻る(返り値 = 追記の後の log の大きさ)
;;;   wal-write-snapshot      写しを一時 file → fsync → rename → dir の fsync で置き、その後で log を空にする
;;; file の effect の断り(FileFailed)は file-done が OSError に上げる — 書けなかった Persist は返事をせずに落ちる(以前の open・fsync の OSError と同じ)。
;;; 1 行の報告は slog(遅い fsync・200 まとまりごとの fsync の内訳・捨てた行・まとめ直し)で、答え手は入口が並べる(本番は slog-handler)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import posixpath)
(import doeff_core_effects [slog])
(import doeff_core_effects [Appended AppendBytes MakeDirectory PathKind PathStat ReadBytes ReadText StatPath SyncDirectory TruncateFile WriteBytes]
        doeff_core_effects._file_effects [file-done])
(import doeff_cluster._shared.core.clock [now-epoch-ms])

;; 置き場を別の volume へ移した後の古い置き場に置く印の file の名。
(val MOVED-MARK "MOVED")
;; これ以上掛かった fsync は 1 回ずつ報告する(置き場の詰まりの時刻と大きさを、返事の遅さの記録と突き合わせられるように)。
(val SLOW-FSYNC-SECONDS 0.5)
;; 何まとまりごとに fsync の内訳を 1 行報告するか(返事の遅さの内訳を後から測れるように)。
(val FSYNC-REPORT-EVERY 200)
;; 置いた写しの mode(以前の open の既定 — umask の 022 で 0644 — と同じ。写しの読み手は別の process の事がある)。
(val SNAPSHOT-MODE 0o644)


(defk wal-check-place [store]
  {:pre [(: store "WalStore")] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "読み直しの前に置き場の dir を作り、移した後の古い置き場なら起動を断るため。中身は書き換えない。置き場を別の volume へ移した後の古い置き場には
   印(MOVED)を置く。古い置き場から起動すると、移した後の書き(版の番号を含む)が巻き戻るので、起動を断る(docs/experiment-log.md の
   「coordinator の置き場」)。"
  (<- (file-done (MakeDirectory store.directory)))
  (val moved (posixpath.join store.directory MOVED-MARK))
  (<- found PathStat (file-done (StatPath moved)))
  (when (!= found.kind PathKind.MISSING)
    (<- text str (file-done (ReadText moved)))
    (raise (RuntimeError (.format "この置き場は移した後の古い物です({}): {}" moved (.strip text)))))
  None)


(defk wal-exists [store]
  {:pre [(: store "WalStore")] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "写しか log が在るか — 起動が置き場から読み直すか、以前の形の file から移すかを決めるため。"
  (<- snapshot PathStat (file-done (StatPath store.snapshot)))
  (<- log PathStat (file-done (StatPath store.log)))
  (or (!= snapshot.kind PathKind.MISSING) (!= log.kind PathKind.MISSING)))


(defk wal-read-snapshot [store]
  {:pre [(: store "WalStore")] :post [(: % (| bytes None))] :tags {:context "coordinator" :role "protocol"}}
  "写しの byte(写しがまだ無ければ None)。"
  (<- found PathStat (file-done (StatPath store.snapshot)))
  (when (= found.kind PathKind.MISSING)
    (return None))
  (<- data bytes (file-done (ReadBytes store.snapshot)))
  data)


(defk log-lines-of [data]
  {:pre [(: data bytes)] :post [(: % (get list bytes))] :tags {:context "coordinator" :role "protocol"}}
  "log の byte を改行つきの行の list にするため(改行 \\n だけで区切る — 開いた file を回した時と同じ。最後の行は改行を持たないことがある)。"
  (val parts (.split data b"\n"))
  (val complete (lfor part (cut parts 0 -1) (+ part b"\n")))
  (val tail (get parts -1))
  (if tail (+ complete [tail]) complete))


(defk wal-read-log-lines [store]
  {:pre [(: store "WalStore")] :post [(: % (get list bytes))] :tags {:context "coordinator" :role "protocol"}}
  "log の行(改行つきの byte の list・log がまだ無ければ空)。最後の行は改行を持たないことがある(fsync の途中で落ちた)。"
  (<- found PathStat (file-done (StatPath store.log)))
  (when (= found.kind PathKind.MISSING)
    (return []))
  (<- data bytes (file-done (ReadBytes store.log)))
  (<- lines (get list bytes) (log-lines-of data))
  lines)


(defk wal-drop-tail [store kept size reason]
  {:pre [(: store "WalStore") (: kept int) (: size int) (: reason str)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "読み直しで最後の読めない 1 行(size byte)を捨てるため: log を kept byte で切り詰めて fsync し、捨てた記録を帳簿に残して 1 行報告する。"
  (.note-recovered store kept size reason)
  (<- (slog (.format "coordinator: log の最後の読めない行を捨てた({} byte・{})" size reason)))
  (<- (file-done (TruncateFile store.log kept :sync store.sync)))
  None)


(defk wal-append-line [store line]
  {:pre [(: store "WalStore") (: line bytes)] :post [(: % int)] :tags {:context "coordinator" :role "protocol"}}
  "綴った 1 行(seq のまとまり)を log へ追記し、fsync してから戻るため。返り値 = 追記の後の log の大きさ(byte — まとめ直しの判断に使う)。
   fsync に掛かった秒は帳簿に足し、遅い fsync は 1 回ずつ、200 まとまりごとに内訳を報告する。"
  (<- appended Appended (file-done (AppendBytes store.log line :sync store.sync)))
  (.note-fsync store appended.synced-seconds)
  (when (>= appended.synced-seconds SLOW-FSYNC-SECONDS)
    (<- (slog (.format "coordinator: 遅い fsync {:.2f} 秒({} byte・まとまり {})" appended.synced-seconds (len line) store.seq))))
  (when (= (% store.seq FSYNC-REPORT-EVERY) 0)
    (<- (slog (.format "coordinator: fsync {}" (.fsync-stats store)))))
  appended.size)


(defk wal-write-snapshot [store data]
  {:pre [(: store "WalStore") (: data bytes)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "綴った写し(全部のキー・checksum つき)を置き(fsync 済み — 一時 file → fsync → rename → dir の fsync)、その後で log を空にするため。
   dir の fsync を済ませてから log を空にする: 写しの名が残らないまま log だけが空になる順を作らない。"
  (<- started int (now-epoch-ms))
  (<- (file-done (WriteBytes store.snapshot data :mode SNAPSHOT-MODE :replace True :sync store.sync)))
  (when store.sync
    (<- (file-done (SyncDirectory store.directory))))
  (<- (file-done (WriteBytes store.log b"" :sync store.sync)))
  (<- ended int (now-epoch-ms))
  (<- (slog (.format "coordinator: log をまとめ直した({:.2f} 秒・キー {})" (/ (- ended started) 1000.0) (len (.table store)))))
  None)
