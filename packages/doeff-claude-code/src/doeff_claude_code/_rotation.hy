;;; 大きな transcript の差し替え— `--resume` の前に、会話の jsonl を最後の compact の境から後だけにする。
;;;
;;; 訳(実測 2026-10-18・zeus・claude 2.1.292): CLI は `--resume` の init を出す前に transcript を頭から全部読む(冷えた 216 MB の file で
;;; read_bytes = 216 MB)。書き足し続けて 6,570 片に割れた file を混んだ USB の disk から読むと 0.76〜2.4 MB/s で、読むだけで約 127 秒 —
;;; 起動の期限(ClaudeCodeHost の launch-timeout 120 秒)を越え、同じ起動を繰り返して手番が始まらなかった。その file の 98.3 % は最後の
;;; compact の境より前で、CLI が続きに使うのは境(と境が残すと記した行)から後だけ。
;;;
;;; 差し替え: file が上限(ROTATE-ABOVE-BYTES)を越えたら、末尾を狭い順の幅(BOUNDARY-TAIL-BYTES)で読み、最後の compact_boundary の行と、
;;; その行の compactMetadata が境の前に残すと記した行(preservedMessages の uuids / allUuids・preservedSegment の headUuid)を探す。
;;; 残す行の一番前から後ろを、同じ会話の id の file として置き直す(前の file は名前に .before-rotation-<epoch ミリ秒> を足した hard link
;;; として同じ dir に残す — .jsonl で終わらないので CLI は会話として数えない)。会話の id を変えないので、上の層(会話の記憶の参照・
;;; 管理の印・写しの預け)は何も知らずに続く。境の後だけの file と全部の file で CLI が API へ出す最初の要求は同じ(実測: messages 425 件と
;;; system が一致・init まで 1.06 秒)。境の行から切ると、境が残すと記した前の行が落ち、要求の頭の 3 件が欠けた(だから残す行から切る)。
;;;
;;; 前提: 差し替えるのは、その会話の CLI の process が降りた後だけ(handler の prepare-launch が前の process の終わりを待った後に呼ぶ —
;;; 走っている CLI は同じ file へ書き足す)。読んでから置き直すまでに大きさが変わった file は置き直さない。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import io)
(import json)
(import os)
(import os.path)

;; 差し替える大きさの下限(byte)。最悪に測った 0.76 MB/s でも 120 秒の期限の 1/3 ほどで読める大きさ。境の後は実測で 0.7〜6.9 MB
;; (zeus の 30 MB を越える 18 本 — 2026-10-18)なので、差し替えた file はこの下限の下に戻る。
(val ROTATE-ABOVE-BYTES (* 32 1024 1024))
;; 境を探す末尾の幅(byte・狭い順)。最後の幅にも境が無ければ差し替えない。
(val BOUNDARY-TAIL-BYTES #((* 1 1024 1024) (* 4 1024 1024) (* 16 1024 1024) (* 64 1024 1024)))
(val BOUNDARY-MARK b"\"subtype\":\"compact_boundary\"")
;; 前の file を残す名前の後ろ(.jsonl の後に足す — CLI が会話の file として拾わない)。
(val ARCHIVE-SUFFIX ".before-rotation-")


(defrecord TranscriptKept
  "差し替えなかった(file が無い・symlink・上限の内・読む間に大きさが変わった)。size = 読んだ時の大きさ(無ければ 0)。"
  (#^ int size))

(defrecord TranscriptRotated
  "差し替えた。before = 前の大きさ・after = 置き直した file の大きさ・archive = 前の file を残した path。"
  (#^ int before)
  (#^ int after)
  (#^ str archive))

(defrecord TranscriptUnrotatable
  "上限を越えるのに、探した末尾(searched byte)に使える境が無い(圧縮の無い会話・境が残すと記した行が見つからない)。"
  (#^ int size)
  (#^ int searched))

(defrecord TranscriptRotationFailed
  "置き直しを file system が断った(reason = 訳)。前の file はそのまま。"
  (#^ str reason))


(defk line-around [#^ bytes data #^ int at]
  {:pre [(: data bytes) (: at int)] :post [(: % tuple)] :tags {:context "claude-code" :role "foundation"}}
  "data の at byte を含む行の頭と終わり(改行の手前)を知るため。"
  (val head (+ (.rfind data b"\n" 0 at) 1))
  (val newline (.find data b"\n" at))
  #(head (if (< newline 0) (len data) newline)))

(defk boundary-record [#^ bytes line]
  {:pre [(: line bytes)] :post [(: % (| dict None))] :tags {:context "claude-code" :role "foundation"}}
  "compact_boundary の行を JSON として読むため(読めない・object でない = None)。"
  (val record (try
                (json.loads (.decode line "utf-8"))
                (except [#(UnicodeDecodeError ValueError)] None)))
  (if (isinstance record dict) record None))

(defk preserved-uuids [#^ dict boundary]
  {:pre [(: boundary dict)] :post [(: % tuple)] :tags {:context "claude-code" :role "foundation"}}
  "境の行が境の前に残すと記した行の uuid を、要る物とあれば足す物に分けるため。答え = #(要る uuid の組 あれば足す uuid の組)。
   要る = preservedMessages.uuids と preservedSegment.headUuid(CLI が続きに使う)・あれば足す = preservedMessages.allUuids の残り
   (行の無い uuid も混じる — 実測)。"
  (val meta (or (.get boundary "compactMetadata") {}))
  (val messages (or (.get meta "preservedMessages") {}))
  (val segment (or (.get meta "preservedSegment") {}))
  (val head (.get segment "headUuid"))
  (val needed (| (set (gfor u (or (.get messages "uuids") []) :if (isinstance u str) u))
                 (if (isinstance head str) #{head} (set))))
  (val extra (set (gfor u (or (.get messages "allUuids") []) :if (isinstance u str) u)))
  #(needed (- extra needed)))

(defk uuid-line-head [#^ bytes data #^ str uuid #^ int before]
  {:pre [(: data bytes) (: uuid str) (: before int)] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "data の before byte より前で、自分の uuid が uuid の行の頭を探すため(無ければ None)。\"uuid\":\"…\" の綴りは parentUuid の欄や
   文字列の中の(逃がした引用符の)綴りに当たらない。"
  (val at (.rfind data (.encode (.format "\"uuid\":\"{}\"" uuid) "utf-8") 0 before))
  (when (< at 0) (return None))
  (<- place (line-around data at))
  (get place 0))

(defk rotation-cut [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "行の頭から始まる transcript の末尾 data で、置き直す file の頭にする byte の位置を探すため。最後の compact_boundary の行の、境が
   残すと記した行の一番前(残す行が無ければ境の行の頭)。境が無い・境の行が読めない・要る残す行がこの data の中に無い = None
   (呼び手は幅を広げて探し直す)。"
  (val mark (.rfind data BOUNDARY-MARK))
  (when (< mark 0) (return None))
  (<- place (line-around data mark))
  (val head (get place 0))
  (<- boundary (boundary-record (cut data head (get place 1))))
  (when (or (is boundary None) (!= (.get boundary "subtype") "compact_boundary")) (return None))
  (<- uuids (preserved-uuids boundary))
  (var cut-at head)
  (for [uuid (get uuids 0)]
    (<- found (uuid-line-head data uuid head))
    (when (is found None) (return None))
    (:= cut-at (min cut-at found)))
  (for [uuid (get uuids 1)]
    (<- found (uuid-line-head data uuid head))
    (when (is-not found None) (:= cut-at (min cut-at found))))
  cut-at)

(defk read-tail [handle #^ int size #^ int width]
  {:pre [(: handle io.BufferedReader) (: size int) (: width int)] :post [(: % tuple)] :tags {:context "claude-code" :role "foundation"}}
  "開いた file の末尾 width byte を、行の頭から読むため。答え = #(読んだ byte 全部を読んだか)。"
  (when (<= size width)
    (.seek handle 0)
    (return #((.read handle size) True)))
  (.seek handle (- size width 1))
  (val chunk (.read handle (+ width 1)))
  (val newline (.find chunk b"\n"))
  #((if (< newline 0) b"" (cut chunk (+ newline 1) None)) False))

(defk tail-to-keep [handle #^ int size]
  {:pre [(: handle io.BufferedReader) (: size int)] :post [(: % (| bytes None))] :tags {:context "claude-code" :role "foundation"}}
  "置き直す中身(最後の境の残す行から末尾まで)を、末尾を狭い順の幅で読んで探すため。どの幅にも無ければ None。"
  (for [width BOUNDARY-TAIL-BYTES]
    (<- read (read-tail handle size width))
    (val data (get read 0))
    (<- cut-at (rotation-cut data))
    (when (is-not cut-at None) (return (cut data cut-at None)))
    (when (get read 1) (return None)))
  None)

(defk rotate-transcript [#^ str path #^ int above-bytes #^ int now-ms]
  {:pre [(: path str) (: above-bytes int) (: now-ms int)]
   :post [(: % (| TranscriptKept TranscriptRotated TranscriptUnrotatable TranscriptRotationFailed))]
   :tags {:context "claude-code" :role "foundation"}}
  "path の transcript が above-bytes を越えていれば、最後の境の残す行から後ろだけの file に置き直すため(頭の註)。読むのは末尾だけ。
   置き直しは一時の file を書いてから、前の file を archive の名前に hard link し、一時の file を path へ rename する(path が無い瞬間を
   作らない)。now-ms = archive の名前に足す刻。"
  (when (or (os.path.islink path) (not (os.path.isfile path)))
    (return (TranscriptKept :size 0)))
  (val size (os.path.getsize path))
  (when (<= size above-bytes)
    (return (TranscriptKept :size size)))
  (val handle (open path "rb"))
  (<- keep (tail-to-keep handle size))
  (.close handle)
  (when (is keep None)
    (return (TranscriptUnrotatable :size size :searched (min size (get BOUNDARY-TAIL-BYTES -1)))))
  (val temporary (+ path ".doeff-rotate-tmp"))
  (val archive (.format "{}{}{}" path ARCHIVE-SUFFIX now-ms))
  (try
    (with [out (open temporary "wb")]
      (.write out keep)
      (.flush out)
      (os.fsync (.fileno out)))
    (if (!= (os.path.getsize path) size)
        (do (os.unlink temporary)
            (TranscriptKept :size (os.path.getsize path)))
        (do (os.link path archive)
            (os.replace temporary path)
            (TranscriptRotated :before size :after (len keep) :archive archive)))
    (except [error OSError]
      (when (os.path.lexists temporary) (os.unlink temporary))
      (TranscriptRotationFailed :reason (.format "{}: {}" path error)))))
