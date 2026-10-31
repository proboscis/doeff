;;; JSON Lines(1 行に JSON の値を 1 つ書いて改行で区切る本文 — CLI の会話の記録・stream の出力など)を行に割る唯一の所。
;;;
;;; 割るのは改行(LINE-END)だけ。str.splitlines は JSON の文字列の中に生のまま書かれた U+2028・U+2029(JSON は escape を求めない —
;;; web の本文に在る)や \x1c〜\x1e・\x85・\v・\f でも割り、正しい JSON の行を読めない 2 行にする(実測: 本番の Claude Code の
;;; subagent の transcript の U+2028 4 つで、読みが行を JSON でないと断り、呼びの記録を全部落とした)。JSON Lines を読む package は
;;; 自分で割らず、この module の jsonl-lines で割る。
(require doeff-hy.macros [defk val])

;; JSON Lines の行の終わり(割るのはこの 1 字だけ)。
(val LINE-END "\n")

(defk jsonl-lines [#^ str text]
  {:pre [(: text str)] :post [(: % tuple)] :tags {:context "json-lines" :role "foundation"}}
  "JSON Lines の本文を、JSON の値の行の列にするため(頭の註 — 割るのは LINE-END だけ)。空白だけの行は落とす。最後の改行の後の
   切れ端も 1 行として答える(書き切ったかは読み手が見る — whole-lines-end)。"
  (tuple (gfor line (.split text LINE-END) :if (.strip line) line)))

(defk whole-lines-end [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % int)] :tags {:context "json-lines" :role "foundation"}}
  "書き足されている JSON Lines の byte のうち、改行で閉じた行の終わり(最後の LINE-END の次の byte の位置 — 無ければ 0)を知るため。
   読み手はここまでを読み終えた所とし、後ろの切れ端(書きの途中の行)は次の読みで頭から読む。UTF-8 の多 byte の字は 0x0A を
   含まないので、byte で探して字の途中を切らない。"
  (+ (.rfind data (.encode LINE-END "utf-8")) 1))
