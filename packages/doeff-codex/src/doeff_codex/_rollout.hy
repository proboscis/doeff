;;; 会話の記録(codex の rollout の jsonl)の取り出しと書き戻し — 家の外に預けた写しから、別の家・記録を外した家で同じ thread を続けるため。
;;;
;;; 置き場(本物の codex 0.162.1 で測った形 — 2026-11-02・zeus の /work/codex-home の 200 本と、一時の家での thread/resume):
;;;   <CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-<刻>-<thread の id>.jsonl
;;;   年月日と刻は、file の 1 行目(種類 session_meta)の payload.timestamp(UTC・例 2026-11-01T18:22:44.123Z)の頭 19 字で、刻は ':' を
;;;   '-' にした綴り。payload.id は thread の id。rollout は後ろへ書き足すだけ(圧縮も書き足す)。
;;; thread/resume の読み方(同じ測り):
;;;   - 状態の DB に行が在り、その path に file が無い → 「no rollout found for thread id」で断る(ThreadUnknown)。
;;;   - file を元の置き場(上の綴り)へ戻すと続けられる。別の日の dir へ戻すと、行の path と食い違って断られる。
;;;   - DB に行が無ければ、sessions の file を名で探して行を作る(元の置き場へ戻せば続けられる)。
;;; そこで書き戻しは、写しの 1 行目から元の置き場を導いて、そこへだけ書く(DB は触らない)。
;;;
;;; 取り出しの答えは行の終わり('\n')までの全部の行(書きかけの最後の行は入れない — 書き戻した記録の最後の行が切れた JSON にならない)。
;;; from-byte は前に取り出した写しの長さ(行の頭)で、その後ろに書き足された全部の行を答える(手番の終わりごとに全部を読み直さない)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "codex" :role "process"})
(import json)
(import os)
(import re)
(import pathlib [Path])

(val SESSIONS-DIR "sessions")
(val ROLLOUT-PREFIX "rollout-")
(val ROLLOUT-SUFFIX ".jsonl")
(val SESSION-META "session_meta")
;; thread の id の綴り(codex は UUID — glob の模様の字を含まない事を確かめてから探す)。
(val THREAD-ID-PATTERN (re.compile "[0-9A-Za-z-]+"))
;; session_meta の timestamp の頭(年月日と刻)。
(val STAMP-PATTERN (re.compile "([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"))
;; 書き戻しの途中の file の名の尾(書き終えてから元の名へ置き換える — codex が書きかけの記録を読まない)。
(val CARRY-TEMPORARY-SUFFIX ".doeff-carry")


(defk sessions-of [#^ dict env]
  {:pre [(: env dict)] :post [(: % (| Path None))] :tags {:context "codex" :role "process"}}
  "宣言の env の CODEX_HOME の sessions の dir を答えるため(資格の家の sessions はここへの link — credential_home.hy)。CODEX_HOME が
   無ければ None。"
  (val base (.get env "CODEX_HOME"))
  (if base (/ (Path base) SESSIONS-DIR) None))


(defk rollout-of [#^ Path sessions #^ str thread-id]
  {:pre [(: sessions Path) (: thread-id str)] :post [(: % (| Path None))] :tags {:context "codex" :role "process"}}
  "thread の rollout の file を sessions の日の dir から名で探すため(頭の註の置き場)。無い・id の綴りが違えば None。"
  (when (not (.fullmatch THREAD-ID-PATTERN thread-id))
    (return None))
  (next (iter (sorted (.glob sessions (+ "*/*/*/" ROLLOUT-PREFIX "*-" thread-id ROLLOUT-SUFFIX)))) None))


(defk whole-lines [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % bytes)] :tags {:context "codex" :role "judgment"}}
  "純粋: 読んだ bytes の、最後の行の終わり('\\n')までを答えるため(書きかけの最後の行を外す)。"
  (val end (.rfind data b"\n"))
  (if (< end 0) b"" (cut data 0 (+ end 1))))


(defk read-rollout [#^ Path path tail-bytes from-byte]
  {:pre [(: path Path) (: tail-bytes (| int None)) (: from-byte (| int None))] :post [(: % (| str None))]
   :tags {:context "codex" :role "process"}}
  "rollout の file を utf-8 で読むため: from-byte が在ればその byte 目から後ろ・tail-bytes が在れば末尾のその幅(切り口の後の最初の行の
   頭から)・どちらも無ければ全部。どれも行の終わりまで(頭の註)。読めない・空白だけ・from-byte より短い・from-byte が字の途中なら None。"
  (var data None)
  (var start None)
  (try
    (with [handle (open path "rb")]
      (:= start (! (window-of (.seek handle 0 os.SEEK-END) tail-bytes from-byte)))
      (when (is-not start None)
        (.seek handle start)
        (:= data (.read handle))))
    (except [OSError]
      (return None)))
  (if (is data None) None (! (text-of data (and (is from-byte None) (> start 0))))))


(defk window-of [#^ int size tail-bytes from-byte]
  {:pre [(: size int) (: tail-bytes (| int None)) (: from-byte (| int None))] :post [(: % (| int None))]
   :tags {:context "codex" :role "judgment"}}
  "純粋: 大きさ size の記録のどこから読むかを決めるため(read-rollout と fake の取り出しの 1 か所)。答え = 読み始めの byte(from-byte・
   末尾の tail-bytes の幅の頭・0)か、from-byte が記録より後ろなら None。"
  (cond
    (is-not from-byte None) (if (> from-byte size) None from-byte)
    (and (is-not tail-bytes None) (> size tail-bytes)) (- size tail-bytes)
    True 0))


(defk text-of [#^ bytes data #^ bool cut-head]
  {:pre [(: data bytes) (: cut-head bool)] :post [(: % (| str None))] :tags {:context "codex" :role "judgment"}}
  "純粋: 読んだ bytes を、預ける写しの文字列にするため(頭の註 — 行の終わりまで)。cut-head = 頭が行の途中かもしれない(末尾だけの読み)
   ので最初の行の終わりまでを外す。空白だけ・字の途中を含む(from-byte が字の途中)なら None。"
  (val head (.find data b"\n"))
  (val body (cond (not cut-head) data
                  (< head 0) b""
                  True (cut data (+ head 1) None)))
  (val text (try
              (.decode (! (whole-lines body)) "utf-8")
              (except [UnicodeDecodeError] None)))
  (if (and text (not (.isspace text))) text None))


(defk carry-place-of [#^ str carry #^ str thread-id]
  {:pre [(: carry str) (: thread-id str)] :post [(: % (| str None))] :tags {:context "codex" :role "judgment"}}
  "純粋: 持ち込む記録の 1 行目(session_meta)から、sessions の下の元の置き場(<年>/<月>/<日>/rollout-<刻>-<id>.jsonl — 頭の註)を導く
   ため。1 行目が session_meta でない・payload の id が thread-id と違う・timestamp の綴りが違う・id の綴りが違えば None。"
  (val first (get (.split carry "\n" 1) 0))
  (val meta (try
              (json.loads first)
              (except [ValueError] None)))
  (when (or (not (isinstance meta dict)) (!= (.get meta "type") SESSION-META) (not (isinstance (.get meta "payload") dict)))
    (return None))
  (val payload (get meta "payload"))
  (val stamp (.match STAMP-PATTERN (str (.get payload "timestamp" ""))))
  (when (or (!= (.get payload "id") thread-id) (is stamp None) (not (.fullmatch THREAD-ID-PATTERN thread-id)))
    (return None))
  (val parts (.groups stamp))
  (.format "{0}/{1}/{2}/{6}{0}-{1}-{2}T{3}-{4}-{5}-{7}{8}" #* parts ROLLOUT-PREFIX thread-id ROLLOUT-SUFFIX))


(defk restore-rollout [#^ Path sessions #^ str thread-id #^ str carry]
  {:pre [(: sessions Path) (: thread-id str) (: carry str)] :post [(: % (| str None))] :tags {:context "codex" :role "process"}}
  "thread の rollout が sessions に無い時だけ、持ち込む記録を元の置き場へ書き戻すため(在れば上書きしない)。書きかけを codex に読ませない
   よう、隣の一時の file に書き終えてから置き換える。答え = None(書き戻した・既に在る)か、書き戻せない訳。"
  (when (is-not (! (rollout-of sessions thread-id)) None)
    (return None))
  (val place (! (carry-place-of carry thread-id)))
  (when (is place None)
    (return (.format "持ち込む記録の 1 行目から thread {} の rollout の置き場を導けない(session_meta の id と timestamp)" thread-id)))
  (val target (/ sessions place))
  (val temporary (.with-name target (+ target.name CARRY-TEMPORARY-SUFFIX)))
  (try
    (.mkdir target.parent :parents True :exist-ok True)
    (with [handle (open temporary "w" :encoding "utf-8")]
      (.write handle carry))
    (os.chmod temporary 0o600)
    (os.replace temporary target)
    (except [error OSError]
      (return (.format "{}: {}" target error))))
  None)


(defk read-thread-rollout [#^ Path sessions #^ str thread-id from-byte tail-bytes]
  {:pre [(: sessions Path) (: thread-id str) (: from-byte (| int None)) (: tail-bytes (| int None))] :post [(: % (| str None))]
   :tags {:context "codex" :role "process"}}
  "thread の記録を家の外に預けるため、sessions から探して読む(CodexExportThread の中身 — 読み方は read-rollout)。無ければ None。"
  (val path (! (rollout-of sessions thread-id)))
  (if (is path None) None (! (read-rollout path tail-bytes from-byte))))
