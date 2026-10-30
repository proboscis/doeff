;;; coordinator の耐久の置き場(追記の log wal.jsonl + まとめ直した写し snapshot.json)の帳簿: どの dir の置き場か・log と写しの path・まとめ直しの上限・
;;; いま耐久になっている全部のキーの表と最後のまとまりの番号・読み直しで捨てた最後の行の記録・直近の fsync の秒。
;;; 行と写しの形(checksum つきの JSON の綴りと検め・読み直しの当て方)は coordinator/protocol/wal_format.hy、帳簿と file を組んで Persist に
;;; 答えるのは coordinator/protocol/store.hy の置き場の口(#2785)。
;;;
;;; この module は file に触らない(#2943 — 以前は open と fsync と rename を持つ class だった)。読み・追記・切り詰め・写しの置き換え・dir の
;;; fsync は file の effect(AppendBytes・TruncateFile・WriteBytes・SyncDirectory ほか)で、置き場の口の defk(coordinator/protocol/wal_files.hy)が出し、
;;; 答えるのは入口が並べる答え手(本番は os-file-handler)。fsync に掛かった秒は AppendBytes の答えに載って届き、帳簿は直近の 200 回を持つ。
;;;
;;; - Persist 1 回 = log へ綴った 1 行を追記して fsync 1 回(wal_files の wal-append-line)。調停ループはこれが戻ってから返事をする(group commit:
;;;   fsync の間に届いた要求は次のまとまりに入り、まとめて 1 回の fsync で済む)。
;;; - log が max-log-bytes を超えたら、置き場の口が全部のキーを写しに綴り直し、一時 file → fsync → rename → dir の fsync で置いてから log を空にする
;;;   (wal-write-snapshot)。写しは「どのまとまりまで含むか」(seq)を持つので、空にする前に落ちても読み直しで二重に当てない。
;;; - 読み直し: 写しと log の byte を読み(wal-read-snapshot・wal-read-log-lines)、置き場の口が検めた後で、最後の読めない 1 行だけをその手前で切り詰める
;;;   (wal-drop-tail)。途中の破損では何も書き換えない(置き場の口が起動を断る)。
;;; sync = False は fsync を落とす(検が disk を待たずに置き場の口を回すため — 本番は True)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import posixpath)

(setv MAX-LOG-BYTES (* 32 1024 1024))
;; 直近の fsync の秒を持つ数(log の遅さを測る)。
(setv FSYNC-WINDOW 200)


(defclass WalStore []
  "dir の置き場の帳簿(頭の註)。directory・snapshot・log = 置き場の dir と、写しと log の file の path(file の effect に渡す綴り)・
   kv / seq = いま耐久になっている全部のキーと最後のまとまりの番号(まとめ直しの材料 — 置き場の口が読み直しと書きの後に進める。表は
   method table / replace-table で受け渡す)。recovered = 読み直しで最後の読めない行を捨てた時の記録(捨てた byte 数・理由・残した byte 数 —
   coordinator が起動の行と計器に出す・method recovery)。fsync-seconds = 直近の fsync の時間(秒)の記録(log の遅さを測る)。"
  (defn #^ None __init__ [self #^ str directory #^ int [max-log-bytes MAX-LOG-BYTES] #^ bool [sync True]]
    ;; 欄の注記は型の宣言(.pyi)が欄を読むため — 置き場の口の形 ByteLog(coordinator/protocol/store)が欄 seq・max-log-bytes・
    ;; snapshot・log を求める(注記が無いと .pyi に欄の型が出ず、使い手の型検査で WalStore が ByteLog を満たさない — #2972)。
    (setv #^ str self.directory directory #^ int self.max-log-bytes max-log-bytes #^ bool self.sync sync
          #^ str self.snapshot (posixpath.join directory "snapshot.json") #^ str self.log (posixpath.join directory "wal.jsonl")
          #^ (get dict #(str object)) self.kv {} #^ int self.seq 0
          #^ (get list float) self.fsync-seconds []
          #^ (| (get dict #(str (| int str))) None) self.recovered None))

  (defn #^ (get dict #(str object)) table [self]
    "いま耐久になっている全部のキーの表(写さない — 置き場の口がその場で当てて進める)。"
    self.kv)

  (defn #^ None replace-table [self #^ (get dict #(str object)) kv]
    "表を置き換える — 置き場の口が読み直した表と、以前の形の file から移した表を置くため。"
    (setv self.kv kv))

  (defn #^ (| (get dict #(str (| int str))) None) recovery [self]
    "読み直しで最後の読めない行を捨てた時の記録(捨てていなければ None)— 起動が 1 行の報告に出すため。"
    self.recovered)

  (defn #^ None note-recovered [self #^ int kept #^ int size #^ str reason]
    "読み直しで最後の読めない 1 行(size byte)を捨てて log を kept byte に切り詰める時に、その記録を recovered に残すため。"
    (setv self.recovered {"dropped" size "reason" reason "kept" kept}))

  (defn #^ None note-fsync [self #^ float seconds]
    "追記 1 回の fsync に掛かった秒を記録に足すため(直近の FSYNC-WINDOW 回だけ持つ)。"
    (.append self.fsync-seconds seconds)
    (setv self.fsync-seconds (cut self.fsync-seconds (- FSYNC-WINDOW) None)))

  (defn #^ dict fsync-stats [self]
    "直近の fsync の回数・中央値・最大(まだ無ければ回数 0 だけ)。"
    (setv xs (sorted self.fsync-seconds))
    (if xs
        {"count" (len xs) "p50" (get xs (// (len xs) 2)) "max" (get xs -1)}
        {"count" 0})))
