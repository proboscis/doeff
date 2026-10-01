;;; effect の型に付ける「記録の形の宣言」の型(#2578 — #2543 の単位 B の 1 段目)。
;;;
;;; 記録と再生の符号化(foundation/record_codec.hy)は、型ごとの扱いを登録表の 1 行(関数で引数の形を作る)で持っていた。既定の形で
;;; 足りる型は、その扱いを型の側にデータで書く: effect の class の本体に ClassVar の属性 `__record_spec__` として RecordSpec の値を置く。
;;; codec は登録表に無い型を引く時に、その型そのものの `__dict__` の宣言の値を読む(子 class には継がない — 子が同じ扱いでよいかは
;;; 子の宣言が決める)。この module は宣言の値と型だけを持ち、宣言を検める処理と読む処理は codec の側に置く(関数を置かない)。
;;; 宣言は effect の意味だけを持つ(記録の JSON の綴りは codec の持ち物で、ここに置かない)。codec は RecordSpec を import せずに
;;; 欄の名で読む — 欄を足す・名を変える時は codec の SPEC-FIELDS も同じ便で直す(一致は tests/test_effect_record.hy が縛る)。
;;;
;;; 欄(codec の登録の 1 行の欄と同じ意味 — 頭注は record_codec.hy):
;;;   mode        再生での扱い(RecordMode)。
;;;   binds       答えを handle として名付ける種類(HandleKind・None = 名付けない)。
;;;   subject     decision / output の対を取る鍵にする、記録の引数の欄の名(None = 対の鍵を持たない)。
;;;   args        問いを見分ける欄の名(記録の引数に載せる順)。None = dataclass の全部の欄。
;;;   unexecuted  記録に対の無い decision / output に返す答え(Unexecuted)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import enum [StrEnum])


;; 再生での扱い(値の綴りは記録の行の "m" と同じ)。
(defenum RecordMode READ LIVE DECISION OUTPUT)

;; 答えを名付ける handle の種類(値の綴りは記録の handle の印 "$h" と同じ)。
(defenum HandleKind NAMED-SEM SEM TASK PROMISE)

;; 記録に対の無い書きへ返す答え: DIVERGE = 返さずに分岐として止める・LANDED = 「着地した」(True)・NOTHING = None。
(defenum Unexecuted DIVERGE LANDED NOTHING)


(defrecord RecordSpec
  "effect の型の記録の形の宣言(effect の class の ClassVar `__record_spec__` に置く)。欄の意味は module の頭注。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ RecordMode mode)
  (setv #^ (| HandleKind None) binds None)
  (setv #^ (| str None) subject None)
  (setv #^ (| tuple None) args None)
  (setv #^ Unexecuted unexecuted Unexecuted.DIVERGE))
