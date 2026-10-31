;;; 形の違う JSON の答え Malformed の置き場。
;;;
;;; wire.hy(pydantic の解き手)と wire_codec.py(自前の解き手・pydantic を読まない)の両方が同じ型を返すので、pydantic を読まない
;;; この module に置く。wire.hy は ここから import して今までどおり名を出す(`(import doeff_hy._wire [Malformed])` の読み手は変わらない)。
;;; 設計の記録 = docs/design/defwire-decoder/design.md(節 3「Malformed の欄の場所」と「reason の綴り」)。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defrecord MalformedField
  "形の合わない所 1 つ: field = 欄の場所(wire の名・入れ子と配列の位置は . でつなぐ・値の全体は空の文字列)/ reason = なぜ
   (`<msg> [<種類>]` — 種類は pydantic の誤りの種類の綴り)。"
  {:tags {:context "wire" :role "type"}}
  (#^ str field)
  (#^ str reason))


(defrecord Malformed
  "外から来た JSON が型の約束の形でない — 5 つ目の失敗の種類(ADR-DOE-CORE-EFFECTS-003 R17): wire-type = 解こうとした型の名 /
   fields = 合わない所(1 つ以上・解き手が見つけた順)。"
  {:tags {:context "wire" :role "type"}
   :check [(> (len fields) 0)]}
  (#^ str wire-type)
  (#^ (get tuple #(MalformedField ...)) fields))
