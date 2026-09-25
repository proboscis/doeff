;;; coordinator の耐久の置き場: 追記の log(wal.jsonl)+ まとめ直した写し(snapshot.json)。Persist の handler。
;;;
;;; - Persist 1 回 = log へ 1 行(変わったキーだけ)を追記して fsync 1 回。調停ループはこれが戻ってから返事をする(group commit:
;;;   fsync の間に届いた要求は次のまとまりに入り、まとめて 1 回の fsync で済む)。
;;; - log が MAX-LOG-BYTES を超えたら、全部のキーを snapshot.json に書き直し(一時 file → fsync → rename → dir の fsync)、
;;;   その後で log を空にする。snapshot は「どの行まで含むか」(seq)を持つので、空にする前に落ちても読み直しで二重に当てない。
;;; - 行と snapshot は checksum を持つ(2026-09-25・docs/decision-2026-09-25-coordinator-review-fixes.md の「WAL」): 行 = {"crc", "delta", "seq"}、
;;;   crc = {"delta", "seq"} を正規化した JSON(鍵を並べ替え・区切りの空白なし・utf-8)の crc32(16 進 8 桁)。snapshot も同じ形で
;;;   {"crc", "kv", "seq"}。checksum の無い旧い形の行・snapshot も読む(互換 — 2026-09-25 より前に書いた置き場)。
;;; - 読み直し: snapshot → log の行を seq の順に当てる。捨ててよいのは **最後の 1 行** が読めない時だけ(改行が無い・JSON に
;;;   ならない・checksum が合わない = fsync の途中で落ちた、返事をしていないまとまり)。その手前で log を切り詰める。
;;;   後ろに別の行が続く行の破損・checksum の不一致・seq の飛び/逆行・壊れた snapshot は WalCorrupted で起動を断る(黙って
;;;   切り詰めると、返事を済ませた書き — 版の番号と lease の行を含む — が巻き戻った状態で起動してしまう)。
;;; I/O はこの module の中だけ(調停ループの Program は Persist の effect しか知らない)。scheduler の外の thread は作らない。
(require doeff-hy.macros [defhandler])
(import json)
(import os)
(import sys)
(import time)
(import zlib)
(import pathlib [Path])
(import .cluster_model [Persist])

(setv MAX-LOG-BYTES (* 32 1024 1024))
(setv SLOW-FSYNC-SECONDS 0.5)
(setv MOVED-MARK "MOVED")


(defclass WalCorrupted [RuntimeError]
  "置き場の途中が壊れている(返事を済ませた書きを失わずには読めない)。起動を断るために投げる。")


(defn write-durably [#^ Path target #^ bytes data]
  "一時 file に書いて fsync し、rename してから dir を fsync する。"
  (setv tmp (Path (+ (str target) ".tmp")))
  (with [handle (open tmp "wb")]
    (.write handle data)
    (.flush handle)
    (os.fsync (.fileno handle)))
  (os.replace tmp target)
  (fsync-dir target.parent))


(defn fsync-dir [#^ Path d]
  (setv fd (os.open (str d) os.O_RDONLY))
  (try (os.fsync fd) (finally (os.close fd))))


(defn #^ dict apply-delta [#^ dict kv #^ dict delta]
  (for [#(k v) (.items delta)]
    (if (is v None) (.pop kv k None) (setv (get kv k) v)))
  kv)


;; --- checksum つきの形(純粋な関数)--------------------------------------------------------------

(defn #^ str canonical [#^ dict body]
  "checksum を取る正規化した JSON(鍵を並べ替え・区切りの空白なし)。"
  (json.dumps body :ensure-ascii False :sort-keys True :separators #("," ":")))


(defn #^ str checksum [#^ str text]
  (format (& (zlib.crc32 (.encode text "utf-8")) 0xffffffff) "08x"))


(defn #^ bytes sealed [#^ dict body]
  "body(crc を持たない dict)を crc つきの 1 つの JSON の byte にする。正規化した text の先頭へ crc の欄を差し込むだけなので
   dump は 1 回。body の鍵はどれも \"crc\" より後に並ぶ(delta・kv・seq)。"
  (setv text (canonical body))
  (.encode (+ "{\"crc\":\"" (checksum text) "\"," (cut text 1 None)) "utf-8"))


(defn #^ bytes encode-line [#^ int seq #^ dict delta]
  "log の 1 行(改行つき)。"
  (+ (sealed {"seq" seq "delta" delta}) b"\n"))


(defn #^ tuple read-line-record [#^ bytes line]
  "log の 1 行を読む。返り値 = #(record checked reason)。record = None なら読めない(reason に理由)。
   checked = checksum を確かめた行か(旧い形の行は False)。"
  (when (not (.endswith line b"\n"))
    (return #(None False "改行が無い(途中で切れた)")))
  (try (setv record (json.loads line))
       (except [ValueError] (return #(None False "JSON にならない"))))
  (when (not (and (isinstance record dict) (isinstance (.get record "seq") int) (isinstance (.get record "delta") dict)))
    (return #(None False "seq と delta の形でない")))
  (when (not-in "crc" record)
    (return #(record False None)))
  (setv crc (.pop record "crc"))
  (if (= crc (checksum (canonical record)))
      #(record True None)
      #(None False "checksum が合わない")))


(defn #^ dict scan-log [#^ list lines #^ int base #^ dict kv #^ str where]
  "log の行(改行つきの byte の list)を snapshot の上(base = snapshot の seq・kv = その中身)へ当てる。
   返り値 = {\"kv\" \"seq\" \"good\"(残す byte 数)\"dropped\"(捨てた最後の行の byte 数)\"reason\"}。
   読めないのが最後の 1 行なら捨てる(返事をしていないまとまり)。それ以外の破損・seq の飛び/逆行は WalCorrupted。
   checksum つきの行が 1 つでも出た後の、checksum の無い行も破損とみなす(書き手は旧い形へ戻らない)。"
  (setv seq base prev None good 0 checked-seen False)
  (for [#(i line) (enumerate lines)]
    (setv #(record checked reason) (read-line-record line))
    (when (and (is-not record None) (not checked) checked-seen)
      (setv record None reason "checksum の無い行が checksum つきの行の後に在る"))
    (when (is record None)
      (when (= i (- (len lines) 1))
        (return {"kv" kv "seq" seq "good" good "dropped" (len line) "reason" reason}))
      (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目が壊れている({})。後ろに {} 行が続くので、切り詰めずに起動を断る(直前の seq {})"
                                    where good (+ i 1) reason (- (len lines) i 1) prev))))
    (setv n (get record "seq"))
    (when (if (is prev None) (> n (+ base 1)) (!= n (+ prev 1)))
      (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目の seq {} が続きでない(直前の seq {}・snapshot の seq {})。起動を断る"
                                    where good (+ i 1) n prev base))))
    (setv prev n checked-seen (or checked-seen checked))
    (+= good (len line))
    (when (> n base)
      (apply-delta kv (get record "delta"))
      (setv seq n)))
  {"kv" kv "seq" seq "good" good "dropped" 0 "reason" None})


(defn #^ tuple read-snapshot [#^ bytes data #^ str where]
  "snapshot の中身 → #(kv seq)。壊れていれば WalCorrupted(snapshot は rename で置くので途中で切れることはない)。
   crc の無い旧い形はそのまま読む。"
  (try (setv record (json.loads data))
       (except [ValueError] (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where)))))
  (when (not (and (isinstance record dict) (isinstance (.get record "seq") int) (isinstance (.get record "kv") dict)))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  (when (in "crc" record)
    (setv crc (.pop record "crc"))
    (when (!= crc (checksum (canonical record)))
      (raise (WalCorrupted (.format "{}: checksum が合わない(seq {})。起動を断る" where (get record "seq"))))))
  #((get record "kv") (get record "seq")))


;; --- 置き場(I/O)---------------------------------------------------------------------------------

(defclass WalStore []
  "dir の中の snapshot.json と wal.jsonl。kv = いま耐久になっている全部のキー(まとめ直しに使う)。
   stats = 直近の fsync の時間(秒)の記録(log の遅さを測る)。"
  (defn __init__ [self #^ str directory [max-log-bytes MAX-LOG-BYTES] [fsync os.fsync]]
    (setv self.dir (Path directory) self.max-log-bytes max-log-bytes self.fsync fsync
          self.snapshot (/ self.dir "snapshot.json") self.log (/ self.dir "wal.jsonl")
          self.kv {} self.seq 0 self.handle None self.fsync-seconds []
          ;; 読み直しで最後の読めない行を捨てた時の記録(捨てた byte 数・理由・残した byte 数)。coordinator が起動の行と計器に出す。
          self.recovered None))

  (defn #^ bool exists [self] (or (.exists self.snapshot) (.exists self.log)))

  (defn #^ dict load [self]
    "耐久の中身を読み直す(snapshot + log)。読めない最後の 1 行だけは捨て、log をその手前で切り詰める。途中の破損は
     WalCorrupted(何も書き換えない)。返り値 = 全部のキーの表。"
    (.mkdir self.dir :parents True :exist-ok True)
    ;; 置き場を別の volume へ移した後の古い置き場には印(MOVED)を置く。古い置き場から起動すると、移した後の書き
    ;; (版の番号を含む)が巻き戻るので、起動を断る(docs/experiment-log.md の「coordinator の置き場」)。
    (setv moved (/ self.dir MOVED-MARK))
    (when (.exists moved)
      (raise (RuntimeError (.format "この置き場は移した後の古い物です({}): {}" moved
                                    (.strip (.read-text moved :encoding "utf-8"))))))
    (setv kv {} base 0)
    (when (.exists self.snapshot)
      (setv #(kv base) (read-snapshot (.read-bytes self.snapshot) (str self.snapshot))))
    (setv seq base)
    (when (.exists self.log)
      (with [handle (open self.log "rb")]
        (setv lines (list handle)))
      (setv scan (scan-log lines base kv (str self.log)))
      (setv kv (get scan "kv") seq (get scan "seq"))
      (when (get scan "dropped")
        (setv self.recovered {"dropped" (get scan "dropped") "reason" (get scan "reason") "kept" (get scan "good")})
        (print (.format "coordinator: log の最後の読めない行を捨てた({} byte・{})" (get scan "dropped") (get scan "reason"))
               :file sys.stderr :flush True)
        (with [handle (open self.log "r+b")]
          (.truncate handle (get scan "good"))
          (.flush handle)
          (self.fsync (.fileno handle)))))
    (setv self.kv kv self.seq seq)
    kv)

  (defn open-log [self]
    (when (is self.handle None)
      (setv self.handle (open self.log "ab"))))

  (defn persist [self #^ dict delta]
    "1 まとまりを log へ 1 行(checksum つき)で書き、fsync してから戻る。"
    (when (not delta) (return None))
    (.open-log self)
    (+= self.seq 1)
    (setv line (encode-line self.seq delta))
    (.write self.handle line)
    (.flush self.handle)
    (setv started (time.monotonic))
    (self.fsync (.fileno self.handle))
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
    (apply-delta self.kv delta)
    (when (> (.tell self.handle) self.max-log-bytes)
      (.checkpoint self)))

  (defn checkpoint [self]
    "全部のキーを snapshot(checksum つき)に書き直し(fsync 済み)、その後で log を空にする。"
    (setv started (time.monotonic))
    (write-durably self.snapshot (sealed {"seq" self.seq "kv" self.kv}))
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


(defhandler wal-store [#^ WalStore store]
  (Persist [delta]
    (.persist store delta)
    (resume None)))


(defhandler memory-store [#^ list log]
  ;; テスト: まとまりごとの delta を list に積む(耐久の置き場の代わり)。
  (Persist [delta]
    (.append log delta)
    (resume None)))
