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


(defclass WalCorrupted [RuntimeError]
  "置き場の途中が壊れている(返事を済ませた書きを失わずには読めない)。起動を断るために投げる。")


(defrecord LineRead
  "log の 1 行を読んだ結果 — 読み直しが行ごとに、当てるか・捨てるか・起動を断るかを決める材料。record = 行の JSON の object
   (crc を除いた seq と delta — JSON の境目の値なので dict のまま持つ)か、読めなければ None(reason に理由)。checked = checksum を
   確かめた行か(旧い形の行は False)。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ (| dict None) record)
  (#^ bool checked)
  (#^ (| str None) reason))


(defrecord LogScan
  "log を写しの上へ当てた結果 — 起動の読み直しの答え。kv = 当てた後の全部のキー(置き場の JSON の表なので dict)・seq = 最後に当てた
   まとまりの番号・good = 残す byte 数・dropped = 捨てた最後の行の byte 数(0 = 捨てていない)・reason = 捨てた理由。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ dict kv)
  (#^ int seq)
  (#^ int good)
  (#^ int dropped)
  (#^ (| str None) reason))


(defrecord SnapshotRead
  "写しを読んだ結果 — 読み直しの起点。kv = 写しの全部のキー(置き場の JSON の表なので dict)・seq = 写しが含む最後のまとまりの番号。"
  {:tags {:context "coordinator" :role "protocol"}}
  (#^ dict kv)
  (#^ int seq))


(defk apply-delta [kv delta]
  {:pre [(: kv dict) (: delta dict)] :post [(: % dict)] :tags {:context "coordinator" :role "protocol"}}
  "差分(キー → 新しい値・消えたキーは None)を表 kv に当てて kv を返す — 読み直しと書きの後で、耐久になった全部のキーの表を
   同じ当て方で進めるため(表は置き場ごとに 1 つで大きいので、写さずにその場で書き換える)。"
  (for [#(k v) (.items delta)]
    (if (is v None) (.pop kv k None) (setv (get kv k) v)))
  kv)


(defk canonical [body]
  {:pre [(: body dict)] :post [(: % str)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "checksum を取る正規化した JSON(鍵を並べ替え・区切りの空白なし)— 書きと検めが同じ text の crc を比べるため。"
  (json.dumps body :ensure-ascii False :sort-keys True :separators #("," ":")))


(defk checksum [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "coordinator" :role "protocol"}}
  "text の crc32(16 進 8 桁)— 行と写しの中身が書いた時のままかを確かめるため。"
  (format (& (zlib.crc32 (.encode text "utf-8")) 0xffffffff) "08x"))


(defk sealed [body]
  {:pre [(: body dict)] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "body(crc を持たない dict)を crc つきの 1 つの JSON の byte にする — 行と写しが同じ封じ方を使うため。正規化した text の先頭へ
   crc の欄を差し込むだけなので dump は 1 回。body の鍵はどれも \"crc\" より後に並ぶ(delta・kv・seq)。"
  (<- text str (canonical body))
  (<- crc str (checksum text))
  (.encode (+ "{\"crc\":\"" crc "\"," (cut text 1 None)) "utf-8"))


(defk encode-line [seq delta]
  {:pre [(: seq int) (: delta dict)] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "log の 1 行(改行つき)— Persist 1 回分のまとまりを番号 seq つきで追記するため。"
  (<- line bytes (sealed {"seq" seq "delta" delta}))
  (+ line b"\n"))


(defk encode-snapshot [seq kv]
  {:pre [(: seq int) (: kv dict)] :post [(: % bytes)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "まとめ直した写しの byte — log を空にする前に、全部のキーを seq のまとまりまで含む 1 つの写しにするため。"
  (<- data bytes (sealed {"seq" seq "kv" kv}))
  data)


(defk read-line-record [line]
  {:pre [(: line bytes)] :post [(: % LineRead)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "log の 1 行を読む — 読み直しが行ごとに、当てるか・捨てるか・起動を断るかを決めるため。"
  (val parsed (if (.endswith line b"\n")
                  (try #("json" (json.loads line)) (except [ValueError] #("not-json" None)))
                  #("cut" None)))
  (match parsed
    #("cut" _) (LineRead None False "改行が無い(途中で切れた)")
    #("not-json" _) (LineRead None False "JSON にならない")
    #(_ record)
      (if (not (and (isinstance record dict) (isinstance (.get record "seq") int) (isinstance (.get record "delta") dict)))
          (LineRead None False "seq と delta の形でない")
          (do (val body (dfor #(k v) (.items record) :if (!= k "crc") k v))
              (if (not-in "crc" record)
                  (LineRead record False None)
                  (do (<- text str (canonical body))
                      (<- crc str (checksum text))
                      (if (= (get record "crc") crc)
                          (LineRead body True None)
                          (LineRead None False "checksum が合わない"))))))))


(defk scan-log [lines base kv where]
  {:pre [(: lines list) (: base int) (: kv dict) (: where str)] :post [(: % LogScan)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "log の行(改行つきの byte の list)を写しの上(base = 写しの seq・kv = その中身)へ当てる — 起動の読み直しで、返事を済ませた
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
    (val unchecked-after-checked (and (is-not read.record None) (not read.checked) checked-seen))
    (val record (if unchecked-after-checked None read.record))
    (val reason (if unchecked-after-checked "checksum の無い行が checksum つきの行の後に在る" read.reason))
    (when (is record None)
      (when (!= i (- (len lines) 1))
        (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目が壊れている({})。後ろに {} 行が続くので、切り詰めずに起動を断る(直前の seq {})"
                                      where good (+ i 1) reason (- (len lines) i 1) prev))))
      (:= dropped #((len line) reason))
      (break))
    (val n (get record "seq"))
    (when (if (is prev None) (> n (+ base 1)) (!= n (+ prev 1)))
      (raise (WalCorrupted (.format "{}: {} byte 目から始まる {} 行目の seq {} が続きでない(直前の seq {}・snapshot の seq {})。起動を断る"
                                    where good (+ i 1) n prev base))))
    (:= prev n)
    (:= checked-seen (or checked-seen read.checked))
    (:= good (+ good (len line)))
    (when (> n base)
      (<- (apply-delta kv (get record "delta")))
      (:= seq n)))
  (match dropped
    None (LogScan kv seq good 0 None)
    #(size why) (LogScan kv seq good size why)))


(defk read-snapshot [data where]
  {:pre [(: data bytes) (: where str)] :post [(: % SnapshotRead)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "写しの中身を読む — 読み直しの起点を写しから作るため。壊れていれば WalCorrupted(写しは rename で置くので途中で切れる
   ことはない)。crc の無い旧い形はそのまま読む。"
  (val record (try (json.loads data)
                   (except [ValueError] (raise (WalCorrupted (.format "{}: JSON にならない。起動を断る" where))))))
  (when (not (and (isinstance record dict) (isinstance (.get record "seq") int) (isinstance (.get record "kv") dict)))
    (raise (WalCorrupted (.format "{}: seq と kv の形でない。起動を断る" where))))
  (when (in "crc" record)
    (<- text str (canonical (dfor #(k v) (.items record) :if (!= k "crc") k v)))
    (<- crc str (checksum text))
    (when (!= (get record "crc") crc)
      (raise (WalCorrupted (.format "{}: checksum が合わない(seq {})。起動を断る" where (get record "seq"))))))
  (SnapshotRead (get record "kv") (get record "seq")))
