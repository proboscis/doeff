;;; coordinator の耐久の置き場(foundation/wal_store の帳簿 WalStore)の file の読み書き — 全部 file の effect(doeff-core-effects の file_effects)で、
;;; 答えるのは入口が並べる答え手(本番は os-file-handler・検は本物の一時 dir か memory-file-handler)。以前は WalStore の method が open・fsync・rename を
;;; 直に呼んでいた(#2943)。行と写しの形は wal_format.hy、帳簿と file を組んで Persist に答えるのは store.hy の置き場の口。
;;;
;;;   wal-check-place         置き場の dir を作り、移した後の古い置き場なら起動を断る(中身は書き換えない)
;;;   wal-exists              写しか log が在るか
;;;   wal-read-snapshot       写しの byte(まだ無ければ None)
;;;   wal-load-snapshot       写しを 1 MiB chunk の streaming で crc 検証・読み込み(全量 byte / UCS-2 文字列を memory に持たない)
;;;   wal-read-log-lines      log の行(改行つきの byte の list・まだ無ければ空)
;;;   wal-scan-log            log を 1 MiB chunk の streaming で読みながら表へ当てる(全量 3 重コピーを避ける)
;;;   wal-drop-tail           読み直しで最後の読めない 1 行を捨てる(log を切り詰めて fsync)
;;;   wal-append-line         綴った 1 行を log へ追記し、fsync してから戻る(返り値 = 追記の後の log の大きさ)
;;;   wal-write-snapshot      綴った写し(bytes)を置き、その後で log を空にする
;;;   wal-write-snapshot-kv   KV 表を 1 MiB chunk の streaming で写しへ書き出し、その後で log を空にする
;;;   wal-compact-snapshot    disk の snapshot.json + wal.jsonl を 2-way merge で streaming まとめ直し(WalStore.kv を memory に持たない)
;;; file の effect の断り(FileFailed)は file-done が OSError に上げる — 書けなかった Persist は返事をせずに落ちる(以前の open・fsync の OSError と同じ)。
;;; 1 行の報告は slog(遅い fsync・200 まとまりごとの fsync の内訳・捨てた行・まとめ直し)で、答え手は入口が並べる(本番は slog-handler)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import codecs)
(import json)
(import posixpath)
(import zlib)
(import doeff_core_effects [slog])
(import doeff_core_effects [Appended AppendBytes MakeDirectory PathKind PathStat ReadBytes ReadText RenamePath StatPath SyncDirectory TruncateFile WriteBytes]
        doeff_core_effects._file_effects [file-done])
(import doeff_cluster._shared.core.clock [now-epoch-ms])
(import doeff_cluster._coordinator.protocol.wal_format [BadLine CANONICAL-ENCODER DroppedTail GoodLine LineRead LogScan SEALED-CRC-PREFIX SnapshotRead WalCorrupted
                                                        apply-delta read-line-record read-snapshot streaming-crc])

;; 置き場を別の volume へ移した後の古い置き場に置く印の file の名。
(val MOVED-MARK "MOVED")
;; これ以上掛かった fsync は 1 回ずつ報告する(置き場の詰まりの時刻と大きさを、返事の遅さの記録と突き合わせられるように)。
(val SLOW-FSYNC-SECONDS 0.5)
;; 何まとまりごとに fsync の内訳を 1 行報告するか(返事の遅さの内訳を後から測れるように)。
(val FSYNC-REPORT-EVERY 200)
;; 置いた写しの mode(以前の open の既定 — umask の 022 で 0644 — と同じ。写しの読み手は別の process の事がある)。
(val SNAPSHOT-MODE 0o644)
;; streaming 読み書きの chunk 上限(1 MiB — memory の一時峰を状態の大きさに比例させない)。
(val STREAM-CHUNK-BYTES (* 1024 1024))
;; sliding text buffer を詰める閾値(256 KiB)。
(val BUFFER-COMPACT-CHARS (* 256 1024))
;; sealed が書く写しの先頭 24 byte: `{"crc":"<8hex>","kv":{`。
(val CANONICAL-KV-MARKER b"\",\"kv\":{")
(val HEX-BYTES (frozenset b"0123456789abcdef"))
(val WS-CHARS " \t\r\n")


(defn #^ (| str None) canonical-snapshot-crc [#^ bytes head]
  "写しの先頭 24 byte が sealed の正規形 `{\"crc\":\"<8hex>\",\"kv\":{` なら 8 桁の crc を返し、それ以外は None。"
  (when (and (= (len head) 24)
             (= (cut head 0 8) SEALED-CRC-PREFIX)
             (= (cut head 16 24) CANONICAL-KV-MARKER)
             (all (gfor b (cut head 8 16) (in b HEX-BYTES))))
    (.decode (cut head 8 16) "ascii")))


(defn #^ int skip-ws [#^ str text #^ int idx]
  "text の idx 以降の ASCII 空白を読み飛ばした位置。"
  (setv n (len text))
  (while (and (< idx n) (in (get text idx) WS-CHARS))
    (+= idx 1))
  idx)


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


(defk wal-verify-snapshot-crc [path expected-crc]
  {:pre [(: path str) (: expected-crc str)] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "正規形の写し `{\"crc\":\"<8hex>\",<rest>` の crc32 を 1 MiB chunk ごとに検証する(全量読み込みなし)。"
  (var acc (zlib.crc32 b"{"))
  (var pos 18)
  (while True
    (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
    (when (not chunk)
      (break))
    (:= acc (zlib.crc32 chunk acc))
    (:= pos (+ pos (len chunk))))
  (= (format (& acc 0xffffffff) "08x") expected-crc))


(defk wal-parse-canonical-snapshot [path where]
  {:pre [(: path str) (: where str)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "正規形の写し `{\"crc\":\"<8hex>\",\"kv\":{...},\"seq\":N}` を 1 MiB chunk ごとに streaming で読み #(seq kv) を返す。"
  (val utf8 ((codecs.getincrementaldecoder "utf-8")))
  (val decoder (json.JSONDecoder))
  (var buf "")
  (var idx 0)
  (var pos 24)
  (var eof False)
  (val kv {})
  ;; 次の 1 MiB chunk を buf へ足す(idx より前は捨てて sliding window に保つ)。
  (while True
    (when (>= idx BUFFER-COMPACT-CHARS)
      (:= buf (cut buf idx None))
      (:= idx 0))
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (when (>= idx (len buf))
      (raise (WalCorrupted (.format "{}: JSON にならない(途中で切れた)。起動を断る" where))))
    (when (= (get buf idx) "}")
      (:= idx (+ idx 1))
      (break))
    ;; key の読み
    (var key None)
    (while True
      (try
        (setv #(parsed next-idx) (.raw-decode decoder buf idx))
        (if (and (not eof) (>= next-idx (len buf)))
            (do (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
                (if chunk
                    (do (:= pos (+ pos (len chunk)))
                        (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                        (:= idx 0))
                    (do (:= eof True)
                        (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                        (:= idx 0))))
            (do (:= key parsed)
                (:= idx next-idx)
                (break)))
        (except [ValueError]
          (when eof
            (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))
          (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
          (if chunk
              (do (:= pos (+ pos (len chunk)))
                  (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                  (:= idx 0))
              (do (:= eof True)
                  (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                  (:= idx 0))))))
    (when (not (isinstance key str))
      (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
    ;; `:` を飛ばす
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (when (or (>= idx (len buf)) (!= (get buf idx) ":"))
      (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))
    (:= idx (+ idx 1))
    (:= idx (skip-ws buf idx))
    ;; value の読み
    (var val-obj None)
    (while True
      (when (and (>= idx (len buf)) (not eof))
        (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
        (if chunk
            (do (:= pos (+ pos (len chunk)))
                (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                (:= idx 0))
            (do (:= eof True)
                (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                (:= idx 0)))
        (:= idx (skip-ws buf idx)))
      (try
        (setv #(parsed next-idx) (.raw-decode decoder buf idx))
        (if (and (not eof) (>= next-idx (len buf)))
            (do (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
                (if chunk
                    (do (:= pos (+ pos (len chunk)))
                        (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                        (:= idx 0))
                    (do (:= eof True)
                        (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                        (:= idx 0))))
            (do (:= val-obj parsed)
                (:= idx next-idx)
                (break)))
        (except [ValueError]
          (when eof
            (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))
          (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
          (if chunk
              (do (:= pos (+ pos (len chunk)))
                  (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                  (:= idx 0))
              (do (:= eof True)
                  (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                  (:= idx 0))))))
    (setv (get kv key) val-obj)
    ;; `,` または `}`
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (when (>= idx (len buf))
      (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))
    (val sep (get buf idx))
    (:= idx (+ idx 1))
    (cond (= sep "}") (break)
          (= sep ",") None
          True (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where)))))
  ;; 末尾の `,"seq":N}` を読む
  (var tail (cut buf idx None))
  (while (not eof)
    (<- chunk bytes (file-done (ReadBytes path :offset pos :limit STREAM-CHUNK-BYTES)))
    (if chunk
        (do (:= pos (+ pos (len chunk)))
            (:= tail (+ tail (.decode utf8 chunk False))))
        (do (:= eof True)
            (:= tail (+ tail (.decode utf8 b"" True))))))
  (val stripped (.strip tail))
  (when (not (.startswith stripped ","))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  (val tail-obj (try (json.loads (+ "{" (cut stripped 1 None)))
                     (except [ValueError] (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))))
  (val seq (.get tail-obj "seq"))
  (when (or (not (isinstance seq int)) (isinstance seq bool))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  #(seq kv))


(defk wal-load-snapshot [store]
  {:pre [(: store "WalStore")] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "写しを streaming で読み #(seq kv) を返す(写しがまだ無ければ #(0 {}))。旧形式や手書き形式は read-snapshot へ落とす。"
  (<- found PathStat (file-done (StatPath store.snapshot)))
  (when (= found.kind PathKind.MISSING)
    (return #(0 {})))
  (<- head bytes (file-done (ReadBytes store.snapshot :offset 0 :limit 24)))
  (val expected-crc (canonical-snapshot-crc head))
  (when (is-not expected-crc None)
    (<- ok bool (wal-verify-snapshot-crc store.snapshot expected-crc))
    (when (not ok)
      (raise (WalCorrupted (.format "{}: checksum が合わない。起動を断る" store.snapshot))))
    (<- parsed tuple (wal-parse-canonical-snapshot store.snapshot store.snapshot))
    (return parsed))
  ;; 旧形式(crc 無し・鍵の順が違う)は read-snapshot へ落とす(crc 検証は streaming-crc)。
  (<- data bytes (file-done (ReadBytes store.snapshot)))
  (<- base SnapshotRead (read-snapshot data store.snapshot))
  #(base.seq (dfor row base.rows row.key row.value)))


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


(defk wal-scan-log [store base kv]
  {:pre [(: store "WalStore") (: base int) (: kv (get dict #(str object)))] :post [(: % LogScan)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "log を 1 MiB chunk の streaming で読み、写しの上(base・kv)へ 1 行ずつ当てる — 全量の 3 重コピーを避ける。"
  (<- found PathStat (file-done (StatPath store.log)))
  (when (or (= found.kind PathKind.MISSING) (= found.size 0))
    (return (LogScan :seq base :dropped None)))
  (val file-size found.size)
  (var pos 0)
  (var buf b"")
  (var line-idx 0)
  (var seq base)
  (var prev None)
  (var good 0)
  (var checked-seen False)
  (var dropped None)
  (var stopped False)
  (while (not stopped)
    (when (and (< pos file-size) (= (.find buf b"\n") -1))
      (<- chunk bytes (file-done (ReadBytes store.log :offset pos :limit STREAM-CHUNK-BYTES)))
      (when (not chunk)
        (break))
      (:= pos (+ pos (len chunk)))
      (:= buf (+ buf chunk)))
    (val nl (.find buf b"\n"))
    (when (= nl -1)
      (when (>= pos file-size)
        (break))
      (continue))
    (val line (cut buf 0 (+ nl 1)))
    (:= buf (cut buf (+ nl 1) None))
    (val has-more (or (bool buf) (< pos file-size)))
    (<- read LineRead (read-line-record line))
    (val judged (match read
                  (GoodLine :checked False) (if checked-seen (BadLine :reason "checksum の無い行が checksum つきの行の後に在る") read)
                  _ read))
    (match judged
      (BadLine :reason reason)
        (do (when has-more
              (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目が壊れている({})。後ろに行が続くので、切り詰めずに起動を断る(直前の seq {})"
                                            store.log good (+ line-idx 1) reason prev))))
            (:= dropped (DroppedTail :kept good :size (len line) :reason reason))
            (:= stopped True))
      (GoodLine :seq n :writes writes :checked checked)
        (do (when (if (is prev None) (> n (+ base 1)) (!= n (+ prev 1)))
              (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目の seq {} が続きでない(直前の seq {}・snapshot の seq {})。起動を断る"
                                            store.log good (+ line-idx 1) n prev base))))
            (:= prev n)
            (:= checked-seen (or checked-seen checked))
            (:= good (+ good (len line)))
            (when (> n base)
              (<- (apply-delta kv writes))
              (:= seq n))))
    (:= line-idx (+ line-idx 1)))
  (when (and (not stopped) (bool buf))
    (:= dropped (DroppedTail :kept good :size (len buf) :reason "改行が無い(途中で切れた)")))
  (LogScan :seq seq :dropped dropped))


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


(defk wal-write-snapshot-kv [store seq kv]
  {:pre [(: store "WalStore") (: seq int) (: kv (get dict #(str object)))] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "KV 表を 1 MiB chunk の streaming で一時 file へ書き出し(fsync → rename → dir の fsync)、その後で log を空にする。
   全量の JSON 文字列や byte 列を memory に作らない。"
  (<- started int (now-epoch-ms))
  (val body {"seq" seq "kv" kv})
  (<- crc str (streaming-crc body))
  (val staged (+ store.snapshot ".tmp"))
  (val out (bytearray (.encode (+ "{\"crc\":\"" crc "\",") "utf-8")))
  (var created False)
  (var first True)
  (for [chunk (.iterencode CANONICAL-ENCODER body)]
    (setv piece (.encode chunk "utf-8"))
    (when (and first piece)
      (:= first False)
      (setv piece (cut piece 1 None)))
    (.extend out piece)
    (when (>= (len out) STREAM-CHUNK-BYTES)
      (if (not created)
          (do (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync False)))
              (:= created True))
          (<- (file-done (AppendBytes staged (bytes out) :sync False))))
      (.clear out)))
  (if (not created)
      (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync store.sync)))
      (<- (file-done (AppendBytes staged (bytes out) :sync store.sync))))
  (<- (file-done (RenamePath staged store.snapshot)))
  (when store.sync
    (<- (file-done (SyncDirectory store.directory))))
  (<- (file-done (WriteBytes store.log b"" :sync store.sync)))
  (<- ended int (now-epoch-ms))
  (<- (slog (.format "coordinator: log をまとめ直した({:.2f} 秒・キー {})" (/ (- ended started) 1000.0) (len kv))))
  None)


(defk wal-read-canonical-snap-seq [path size]
  {:pre [(: path str) (: size int)] :post [(: % (| int None))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "正規形の写しの末尾 64 byte から `,\"seq\":N}` の seq を O(1) で読む。"
  (val start (max 0 (- size 64)))
  (<- tail bytes (file-done (ReadBytes path :offset start)))
  (val marker (.rfind tail b"},\"seq\":"))
  (when (= marker -1)
    (return None))
  (try
    (val parsed (json.loads (+ "{" (.decode (cut tail (+ marker 2) None) "utf-8"))))
    (val seq (.get parsed "seq"))
    (if (and (isinstance seq int) (not (isinstance seq bool))) seq None)
    (except [ValueError] None)))


(defk wal-collect-wal-delta [store after-seq]
  {:pre [(: store "WalStore") (: after-seq int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "wal.jsonl を 1 MiB chunk で読み、after-seq より新しい行の差分(削除は None のまま残す)と最大 seq を集める。"
  (<- found PathStat (file-done (StatPath store.log)))
  (when (or (= found.kind PathKind.MISSING) (= found.size 0))
    (return #(after-seq {})))
  (val file-size found.size)
  (val delta {})
  (var max-seq after-seq)
  (var pos 0)
  (var buf b"")
  (while True
    (when (and (< pos file-size) (= (.find buf b"\n") -1))
      (<- chunk bytes (file-done (ReadBytes store.log :offset pos :limit STREAM-CHUNK-BYTES)))
      (when (not chunk)
        (break))
      (:= pos (+ pos (len chunk)))
      (:= buf (+ buf chunk)))
    (val nl (.find buf b"\n"))
    (when (= nl -1)
      (break))
    (val line (cut buf 0 (+ nl 1)))
    (:= buf (cut buf (+ nl 1) None))
    (<- read LineRead (read-line-record line))
    (match read
      (GoodLine :seq n :writes writes)
        (when (> n after-seq)
          (for [w writes]
            (setv (get delta w.key) w.value))
          (when (> n max-seq)
            (:= max-seq n)))
      _ None))
  #(max-seq delta))


(defk wal-stream-merge-pass [snap-path seq wal-items staged sync crc-header]
  {:pre [(: snap-path str) (: seq int) (: wal-items tuple) (: staged str) (: sync bool) (: crc-header (| str None))]
   :post [(: % tuple)] :tags {:context "coordinator" :role "protocol" :reads "json" :spells "json"}}
  "正規形の旧 snapshot.json(鍵が昇順)と sorted な wal-items を 2-way merge で streaming 処理する。
   crc-header が None の時は crc32 と鍵数だけを数え(Pass 1)、文字列の時は staged file へ 1 MiB chunk で書き出す(Pass 2)。"
  (val utf8 ((codecs.getincrementaldecoder "utf-8")))
  (val decoder (json.JSONDecoder))
  (var buf "")
  (var idx 0)
  (var pos 24)
  (var eof False)
  (var acc 0)
  (var emitted 0)
  (var wi 0)
  (val wn (len wal-items))
  (val out (bytearray))
  (var created False)
  ;; 先頭: Pass 1 は `{"kv":{` を crc へ、Pass 2 は `{"crc":"<crc>","kv":{` を出力へ。
  (if (is crc-header None)
      (:= acc (zlib.crc32 b"{\"kv\":{" acc))
      (.extend out (.encode (+ "{\"crc\":\"" crc-header "\",\"kv\":{") "utf-8")))
  (while True
    (when (>= idx BUFFER-COMPACT-CHARS)
      (:= buf (cut buf idx None))
      (:= idx 0))
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (when (or (>= idx (len buf)) (= (get buf idx) "}"))
      (break))
    ;; 旧 snapshot の key を読む
    (var old-key "")
    (while True
      (try
        (setv #(parsed next-idx) (.raw-decode decoder buf idx))
        (if (and (not eof) (>= next-idx (len buf)))
            (do (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
                (if chunk
                    (do (:= pos (+ pos (len chunk)))
                        (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                        (:= idx 0))
                    (do (:= eof True)
                        (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                        (:= idx 0))))
            (do (:= old-key parsed)
                (:= idx next-idx)
                (break)))
        (except [ValueError]
          (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
          (if chunk
              (do (:= pos (+ pos (len chunk)))
                  (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
                  (:= idx 0))
              (do (:= eof True)
                  (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
                  (:= idx 0))))))
    ;; `:` を飛ばす
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (:= idx (+ idx 1))
    (:= idx (skip-ws buf idx))
    ;; 旧 snapshot の value の生の文字範囲をそのまま切り出す(val-start より前は詰めてよい)
    (var val-start idx)
    (var val-end idx)
    (while True
      (when (and (>= idx (len buf)) (not eof))
        (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
        (if chunk
            (do (:= pos (+ pos (len chunk)))
                (:= buf (+ (cut buf val-start None) (.decode utf8 chunk False)))
                (:= idx (- idx val-start))
                (:= val-start 0))
            (do (:= eof True)
                (:= buf (+ (cut buf val-start None) (.decode utf8 b"" True)))
                (:= idx (- idx val-start))
                (:= val-start 0)))
        (:= idx (skip-ws buf idx))
        (:= val-start idx))
      (try
        (setv #(_ next-idx) (.raw-decode decoder buf idx))
        (if (and (not eof) (>= next-idx (len buf)))
            (do (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
                (if chunk
                    (do (:= pos (+ pos (len chunk)))
                        (:= buf (+ (cut buf val-start None) (.decode utf8 chunk False)))
                        (:= idx (- idx val-start))
                        (:= val-start 0))
                    (do (:= eof True)
                        (:= buf (+ (cut buf val-start None) (.decode utf8 b"" True)))
                        (:= idx (- idx val-start))
                        (:= val-start 0))))
            (do (:= val-end next-idx)
                (:= idx next-idx)
                (break)))
        (except [ValueError]
          (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
          (if chunk
              (do (:= pos (+ pos (len chunk)))
                  (:= buf (+ (cut buf val-start None) (.decode utf8 chunk False)))
                  (:= idx (- idx val-start))
                  (:= val-start 0))
              (do (:= eof True)
                  (:= buf (+ (cut buf val-start None) (.decode utf8 b"" True)))
                  (:= idx (- idx val-start))
                  (:= val-start 0))))))
    (val raw-val (cut buf val-start val-end))
    ;; old-key より前の wal-items を先に流す
    (while (and (< wi wn) (< (get (get wal-items wi) 0) old-key))
      (setv #(wk wv) (get wal-items wi))
      (:= wi (+ wi 1))
      (when (is-not wv None)
        (setv prefix (.encode (+ (if (> emitted 0) "," "") (json.dumps wk :ensure-ascii False) ":") "utf-8"))
        (if (is crc-header None)
            (:= acc (zlib.crc32 prefix acc))
            (.extend out prefix))
        (for [chunk (.iterencode CANONICAL-ENCODER wv)]
          (setv piece (.encode chunk "utf-8"))
          (if (is crc-header None)
              (:= acc (zlib.crc32 piece acc))
              (do (.extend out piece)
                  (when (>= (len out) STREAM-CHUNK-BYTES)
                    (if (not created)
                        (do (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync False)))
                            (:= created True))
                        (<- (file-done (AppendBytes staged (bytes out) :sync False))))
                    (.clear out)))))
        (:= emitted (+ emitted 1))))
    ;; old-key が wal-items に在れば上書き(または None で削除)、無ければ生の JSON をそのまま流す
    (if (and (< wi wn) (= (get (get wal-items wi) 0) old-key))
        (do (setv wv (get (get wal-items wi) 1))
            (:= wi (+ wi 1))
            (when (is-not wv None)
              (setv prefix (.encode (+ (if (> emitted 0) "," "") (json.dumps old-key :ensure-ascii False) ":") "utf-8"))
              (if (is crc-header None)
                  (:= acc (zlib.crc32 prefix acc))
                  (.extend out prefix))
              (for [chunk (.iterencode CANONICAL-ENCODER wv)]
                (setv piece (.encode chunk "utf-8"))
                (if (is crc-header None)
                    (:= acc (zlib.crc32 piece acc))
                    (do (.extend out piece)
                        (when (>= (len out) STREAM-CHUNK-BYTES)
                          (if (not created)
                              (do (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync False)))
                                  (:= created True))
                              (<- (file-done (AppendBytes staged (bytes out) :sync False))))
                          (.clear out)))))
              (:= emitted (+ emitted 1))))
        (do (setv entry-bytes (.encode (+ (if (> emitted 0) "," "") (json.dumps old-key :ensure-ascii False) ":" raw-val) "utf-8"))
            (if (is crc-header None)
                (:= acc (zlib.crc32 entry-bytes acc))
                (do (.extend out entry-bytes)
                    (when (>= (len out) STREAM-CHUNK-BYTES)
                      (if (not created)
                          (do (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync False)))
                              (:= created True))
                          (<- (file-done (AppendBytes staged (bytes out) :sync False))))
                      (.clear out))))
            (:= emitted (+ emitted 1))))
    ;; `,` または `}`
    (:= idx (skip-ws buf idx))
    (while (and (>= idx (len buf)) (not eof))
      (<- chunk bytes (file-done (ReadBytes snap-path :offset pos :limit STREAM-CHUNK-BYTES)))
      (if chunk
          (do (:= pos (+ pos (len chunk)))
              (:= buf (+ (cut buf idx None) (.decode utf8 chunk False)))
              (:= idx 0))
          (do (:= eof True)
              (:= buf (+ (cut buf idx None) (.decode utf8 b"" True)))
              (:= idx 0)))
      (:= idx (skip-ws buf idx)))
    (when (>= idx (len buf))
      (break))
    (val sep (get buf idx))
    (:= idx (+ idx 1))
    (when (= sep "}")
      (break)))
  ;; 残りの wal-items を流す
  (while (< wi wn)
    (setv #(wk wv) (get wal-items wi))
    (:= wi (+ wi 1))
    (when (is-not wv None)
      (setv prefix (.encode (+ (if (> emitted 0) "," "") (json.dumps wk :ensure-ascii False) ":") "utf-8"))
      (if (is crc-header None)
          (:= acc (zlib.crc32 prefix acc))
          (.extend out prefix))
      (for [chunk (.iterencode CANONICAL-ENCODER wv)]
        (setv piece (.encode chunk "utf-8"))
        (if (is crc-header None)
            (:= acc (zlib.crc32 piece acc))
            (do (.extend out piece)
                (when (>= (len out) STREAM-CHUNK-BYTES)
                  (if (not created)
                      (do (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync False)))
                          (:= created True))
                      (<- (file-done (AppendBytes staged (bytes out) :sync False))))
                  (.clear out)))))
      (:= emitted (+ emitted 1))))
  ;; 末尾 `},"seq":N}`
  (val closing (.encode (.format "}},\"seq\":{}}}" seq) "utf-8"))
  (if (is crc-header None)
      (:= acc (zlib.crc32 closing acc))
      (do (.extend out closing)
          (if (not created)
              (<- (file-done (WriteBytes staged (bytes out) :mode SNAPSHOT-MODE :replace False :sync sync)))
              (<- (file-done (AppendBytes staged (bytes out) :sync sync))))))
  #((format (& acc 0xffffffff) "08x") emitted))


(defk wal-compact-snapshot [store]
  {:pre [(: store "WalStore")] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :reads "json" :spells "json"}}
  "disk の snapshot.json と wal.jsonl を 2-way merge で streaming まとめ直しする(WalStore に全量 KV 表を持たずに checkpoint できる)。"
  (<- started int (now-epoch-ms))
  (<- snap-stat PathStat (file-done (StatPath store.snapshot)))
  (when (= snap-stat.kind PathKind.MISSING)
    (<- fresh-collected tuple (wal-collect-wal-delta store 0))
    (val fresh-seq (max store.seq (get fresh-collected 0)))
    (val live (dfor #(k v) (.items (get fresh-collected 1)) :if (is-not v None) k v))
    (<- (wal-write-snapshot-kv store fresh-seq live))
    (return None))
  (<- head bytes (file-done (ReadBytes store.snapshot :offset 0 :limit 24)))
  (val canonical-crc (canonical-snapshot-crc head))
  (val snap-seq (if (is-not canonical-crc None)
                    (! (wal-read-canonical-snap-seq store.snapshot snap-stat.size))
                    None))
  ;; 旧形式(crc 無し・末尾が seq でない)は 1 度読んで streaming 書き出しへ落とす(以後は正規形になる)。
  (when (is snap-seq None)
    (<- loaded tuple (wal-load-snapshot store))
    (val old-seq (get loaded 0))
    (val kv (get loaded 1))
    (<- legacy-collected tuple (wal-collect-wal-delta store old-seq))
    (for [#(k v) (.items (get legacy-collected 1))]
      (if (is v None) (.pop kv k None) (setv (get kv k) v)))
    (val legacy-seq (max store.seq old-seq (get legacy-collected 0)))
    (<- (wal-write-snapshot-kv store legacy-seq kv))
    (return None))
  ;; 正規形 snapshot.json + wal.jsonl の streaming 2-way merge(2 pass: Pass 1 で crc・Pass 2 で 1 MiB chunk 書き出し)。
  (<- collected tuple (wal-collect-wal-delta store snap-seq))
  (val seq (max store.seq snap-seq (get collected 0)))
  (val wal-items (tuple (sorted (.items (get collected 1)))))
  (val staged (+ store.snapshot ".tmp"))
  (<- pass1 tuple (wal-stream-merge-pass store.snapshot seq wal-items staged store.sync None))
  (val crc (get pass1 0))
  (val key-count (get pass1 1))
  (<- (wal-stream-merge-pass store.snapshot seq wal-items staged store.sync crc))
  (<- (file-done (RenamePath staged store.snapshot)))
  (when store.sync
    (<- (file-done (SyncDirectory store.directory))))
  (<- (file-done (WriteBytes store.log b"" :sync store.sync)))
  (<- ended int (now-epoch-ms))
  (<- (slog (.format "coordinator: log をまとめ直した({:.2f} 秒・キー {})" (/ (- ended started) 1000.0) key-count)))
  None)
