;;; coordinator の耐久の置き場(追記の log wal.jsonl + まとめ直した写し snapshot.json)の行と写しの形 — 綴り・検め・読み直しの当て方
;;; (#2785・#2680 の (イ))。I/O を持たない: byte を受けて読み、byte を綴って返すだけ。file の I/O は
;;; foundation/wal_store.hy の WalStore(追記と fsync・rename・切り詰め)で、両方を組むのは coordinator/protocol/store.hy の置き場の口。
;;;
;;; 形(2026-09-25・docs/decision-2026-09-25-coordinator-review-fixes.md の「WAL」):
;;; - 行 = {"crc", "delta", "seq"}。crc = {"delta", "seq"} を正規化した JSON(鍵を並べ替え・区切りの空白なし・utf-8)の crc32(16 進 8 桁)。
;;;   写しも同じ形で {"crc", "kv", "seq"}。checksum の無い旧い形の行・写しも読む(互換 — 2026-09-25 より前に書いた置き場)。
;;; - 読み直し: 写し → log の行を seq の順に当てる。捨ててよいのは最後の 1 行が読めない時だけ(改行が無い・JSON にならない・checksum が
;;;   合わない = fsync の途中で落ちた、返事をしていないまとまり)。後ろに別の行が続く行の破損・checksum の不一致・seq の飛び/逆行・
;;;   壊れた写しは WalCorrupted で起動を断る(黙って切り詰めると、返事を済ませた書き — 版の番号と lease の行を含む — が巻き戻った
;;;   状態で起動してしまう)。
(require doeff-hy.macros [defk val var <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import json)
(import zlib)
(import doeff_hy.table [TableWrite])


(defclass WalCorrupted [RuntimeError]
  "置き場の途中が壊れている(返事を済ませた書きを失わずには読めない)。起動を断るために投げる。")


(defrecord GoodLine
  "読めた log の 1 行 — 読み直しが当てる材料。seq = まとまりの番号・writes = その差分のキーごとの書き(TableWrite — value は新しい値・
   消えたキーは None。Persist の欄と同じ形で、写像を欄に持たない — DOEFF172)・checked = checksum を確かめた行か(旧い形の行は False)。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ int seq)
  (#^ (get tuple #((get TableWrite object) ...)) writes)
  (#^ bool checked))


(defrecord BadLine
  "読めない log の 1 行 — 最後の行なら捨て、後ろに行が続けば起動を断る。reason = 読めない理由。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ str reason))


;; log の 1 行を読んだ結果(閉じた和)— 読み直しが行ごとに、当てるか・捨てるか・起動を断るかを決める。
(val LineRead (| GoodLine BadLine))


(defrecord DroppedTail
  "読み直しで捨てた最後の 1 行 — kept = 残す byte 数(log をここで切り詰める)・size = 捨てた行の byte 数・reason = 捨てた理由。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ int kept)
  (#^ int size)
  (#^ str reason))


(defrecord LogScan
  "log を写しの上へ当てた結果 — 起動の読み直しの答え。表は scan-log に渡した物をその場で進める(写さない — 表は置き場ごとに 1 つで
   大きい)。seq = 最後に当てたまとまりの番号・dropped = 捨てた最後の行(None = 捨てていない)。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ int seq)
  (#^ (| DroppedTail None) dropped))


(defrecord SnapshotRead
  "写しを読んだ結果 — 読み直しの起点。seq = 写しが含む最後のまとまりの番号・rows = 写しの全部のキーの書き(TableWrite — 写像を欄に
   持たない — DOEFF172。表に組むのは置き場の口)。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ int seq)
  (#^ (get tuple #((get TableWrite object) ...)) rows))


(defk writes-of [delta]
  {:pre [(: delta (get dict #(str object)))] :post [(: % (get tuple #((get TableWrite object) ...)))] :tags {:context "coordinator" :role "protocol"}}
  "差分の写像(JSON の行の delta・写しの kv・Persist の答え手が受けた差分)をキーごとの書きの組にする — record と表の当て方が写像を
   欄に持たずに差分を受け渡すため。"
  (tuple (gfor #(key value) (.items delta) (TableWrite key value))))


(defk apply-delta [kv writes]
  {:pre [(: kv (get dict #(str object))) (: writes (get tuple #((get TableWrite object) ...)))] :post [(: % (get dict #(str object)))]
   :tags {:context "coordinator" :role "protocol"}}
  "差分のキーごとの書き(value = 新しい値・消えたキーは None)を表 kv に当てて kv を返す — 読み直しと書きの後で、耐久になった全部の
   キーの表を同じ当て方で進めるため(表は置き場ごとに 1 つで大きいので、写さずにその場で書き換える)。"
  (for [w writes]
    (if (is w.value None) (.pop kv w.key None) (setv (get kv w.key) w.value)))
  kv)


(defk canonical [body]
  {:pre [(: body (get dict #(str object)))] :post [(: % str)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "checksum を取る正規化した JSON(鍵を並べ替え・区切りの空白なし)— 書きと検めが同じ text の crc を比べるため。"
  (json.dumps body :ensure-ascii False :sort-keys True :separators #("," ":")))


(defk checksum [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "text の crc32(16 進 8 桁)— 行と写しの中身が書いた時のままかを確かめるため。"
  (format (& (zlib.crc32 (.encode text "utf-8")) 0xffffffff) "08x"))


(defk sealed [body]
  {:pre [(: body (get dict #(str object)))] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "body(crc を持たない dict)を crc つきの 1 つの JSON の byte にする — 行と写しが同じ封じ方を使うため。正規化した text の先頭へ
   crc の欄を差し込むだけなので dump は 1 回。body の鍵はどれも \"crc\" より後に並ぶ(delta・kv・seq)。"
  (<- text str (canonical body))
  (<- crc str (checksum text))
  (.encode (+ "{\"crc\":\"" crc "\"," (cut text 1 None)) "utf-8"))


(defk encode-line [seq delta]
  {:pre [(: seq int) (: delta (get dict #(str object)))] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "log の 1 行(改行つき)— Persist 1 回分のまとまりを番号 seq つきで追記するため。"
  (<- line bytes (sealed {"seq" seq "delta" delta}))
  (+ line b"\n"))


(defk encode-snapshot [seq kv]
  {:pre [(: seq int) (: kv (get dict #(str object)))] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "まとめ直した写しの byte — log を空にする前に、全部のキーを seq のまとまりまで含む 1 つの写しにするため。"
  (<- data bytes (sealed {"seq" seq "kv" kv}))
  data)


(defk read-line-record [line]
  {:pre [(: line bytes)] :post [(: % LineRead)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "log の 1 行を読む — 読み直しが行ごとに、当てるか・捨てるか・起動を断るかを決めるため。"
  (when (not (.endswith line b"\n"))
    (return (BadLine :reason "改行が無い(途中で切れた)")))
  (val record (try (json.loads line) (except [ValueError] (return (BadLine :reason "JSON にならない")))))
  (when (not (isinstance record dict))
    (return (BadLine :reason "seq と delta の形でない")))
  (val seq (.get record "seq"))
  (val delta (.get record "delta"))
  (when (not (and (isinstance seq int) (isinstance delta dict)))
    (return (BadLine :reason "seq と delta の形でない")))
  (<- writes (get tuple #((get TableWrite object) ...)) (writes-of delta))
  (when (not-in "crc" record)
    (return (GoodLine :seq seq :writes writes :checked False)))
  ;; crc は crc の欄を除いた全部の欄(書き手が書くのは seq と delta だけ)の正規化した JSON に対して取ってある。
  (<- text str (canonical (dfor #(k v) (.items record) :if (!= k "crc") k v)))
  (<- crc str (checksum text))
  (if (= (get record "crc") crc)
      (GoodLine :seq seq :writes writes :checked True)
      (BadLine :reason "checksum が合わない")))


(defk scan-log [lines base kv where]
  {:pre [(: lines (get list bytes)) (: base int) (: kv (get dict #(str object))) (: where str)] :post [(: % LogScan)]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "log の行(改行つきの byte の list)を写しの上(base = 写しの seq・kv = その中身の表 — その場で進める)へ当てる — 起動の読み直しで、返事を済ませた
   書きを 1 つも失わずに表と番号を作るため。読めないのが最後の 1 行なら捨てる(返事をしていないまとまり)。それ以外の破損・seq の飛び/逆行は WalCorrupted。
   checksum つきの行が 1 つでも出た後の、checksum の無い行も破損とみなす(書き手は旧い形へ戻らない)。"
  (var seq base)
  (var prev None)
  (var good 0)
  (var checked-seen False)
  (var dropped None)
  (for [#(i line) (enumerate lines)]
    (<- read LineRead (read-line-record line))
    ;; checksum の無い行は、checksum つきの行の後なら読めない行と同じに扱う。
    (val judged (match read
                  (GoodLine :checked False) (if checked-seen (BadLine :reason "checksum の無い行が checksum つきの行の後に在る") read)
                  _ read))
    (match judged
      (BadLine :reason reason)
        (do (when (!= i (- (len lines) 1))
              (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目が壊れている({})。後ろに {} 行が続くので、切り詰めずに起動を断る(直前の seq {})"
                                            where good (+ i 1) reason (- (len lines) i 1) prev))))
            (:= dropped (DroppedTail :kept good :size (len line) :reason reason))
            (break))
      (GoodLine :seq n :writes writes :checked checked)
        (do (when (if (is prev None) (> n (+ base 1)) (!= n (+ prev 1)))
              (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目の seq {} が続きでない(直前の seq {}・snapshot の seq {})。起動を断る"
                                            where good (+ i 1) n prev base))))
            (:= prev n)
            (:= checked-seen (or checked-seen checked))
            (:= good (+ good (len line)))
            (when (> n base)
              (<- (apply-delta kv writes))
              (:= seq n)))))
  (LogScan :seq seq :dropped dropped))


(defk read-snapshot [data where]
  {:pre [(: data bytes) (: where str)] :post [(: % SnapshotRead)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "写しの中身を読む — 読み直しの起点を写しから作るため。壊れていれば WalCorrupted(写しは rename で置くので途中で切れる
   ことはない)。crc の無い旧い形はそのまま読む。"
  (val record (try (json.loads data)
                   (except [ValueError] (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))))
  (when (not (isinstance record dict))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  (val seq (.get record "seq"))
  (val kv (.get record "kv"))
  (when (not (and (isinstance seq int) (isinstance kv dict)))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  (when (in "crc" record)
    (<- text str (canonical (dfor #(k v) (.items record) :if (!= k "crc") k v)))
    (<- crc str (checksum text))
    (when (!= (get record "crc") crc)
      (raise (WalCorrupted (.format "{}: checksum が合わない(seq {})。起動を断る" where seq)))))
  (<- rows (get tuple #((get TableWrite object) ...)) (writes-of kv))
  (SnapshotRead :seq seq :rows rows))
