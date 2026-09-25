;;; 記録の足場 — 手番の記録(TurnRecord 相当)の書き手が使う macro(柵 5)。
;;;
;;; 出自 = agora-redesign 段 7 lane 7c(決定 1.4 の柵「記録の足場の macro」・
;;; 「新しい macro は doeff-hy に投影の規則と一緒に」・
;;; `docs/plans/decisions-merge-2026-09-12.md`)。
;;;
;;; 何のための足場か: 手番の記録の書き手(agentd)は 1 手番につき 1 行を書く。
;;; その行を裸の dict で組むと、柵 2(`"value"`・Any・object・裸の dict / list の
;;; 禁止)を破り、欄の名前の綴り違いも型の違いも実行時まで見えない。
;;; `defrecord` は欄の名前と型を宣言した凍結の record 型を 1 行で建てる —
;;; 書き手は dict ではなくその型を作る。
;;;
;;; 記録の**中身**(どの欄が在るか)はここが決めない: 手番の記録の schema の
;;; 正本は ACP の `docs/contracts/` で、書き手はそこから写した欄を宣言する。
;;; ここは形の足場だけを持つ(道具の層)。
;;;
;;; 投影の規則(この macro が静的検査を通れる理由): `defrecord` は
;;; quasiquote 1 つだけで書かれた template macro で、共通の品質検査
;;; (`~/dotfiles/agent/quality/hy_macro.py`)が現物の形を検証し、逐語の置換で
;;; 展開できる。使う側の module 契約に `"macros": {"defrecord": "template"}`
;;; を宣言する必要は無い — 由来が固定表(`doeff_hy.macros` / `doeff_hy.record`)
;;; に載っているので、`hy_projection` が展開の規則を持つ。
;;;
;;; 使い方:
;;;   (require doeff-hy.record [defrecord])
;;;   (import dataclasses [dataclass])
;;;
;;;   (defrecord TurnRecordRow
;;;     #^ str conversation-id
;;;     #^ str turn-id
;;;     #^ str phase
;;;     #^ (| str None) ended-at)
;;;
;;;   (TurnRecordRow :conversation-id c :turn-id t :phase "Running" :ended-at None)
;;;
;;; ⚠ template macro の形は閉じている(docstring も他の式も置けない)。
;;; だから説明はこの module の頭注が持つ — 説明の定義点は 1 つ。
;;;
;;; ---------------------------------------------------------------------------
;;; `defenum` — 閉じた値の集合(文字列の Enum)を 1 行で建てる
;;; ---------------------------------------------------------------------------
;;;
;;; 出自 = operator 2026-09-26「and perhaps we should use macro for enum」。
;;; 設計の記録 = `docs/design/defenum/design.md`(値の綴り・StrEnum・網羅の確認)。
;;;
;;; 何のための足場か: 「決まった数の名前のどれか」を裸の文字列で持つと、綴り違いが
;;; 実行時まで見えず、`match` の分岐の漏れも型検査で捕まらない。`defenum` は
;;; `enum.StrEnum` の class を建てる — 値は文字列なのでそのまま JSON に書け、
;;; 型検査は member の集合を知っているので `match` の網羅を確かめられる。
;;;
;;; 使い方:
;;;   (require doeff-hy.record [defenum])
;;;   (import enum [StrEnum])
;;;
;;;   (defenum PlacementMismatch KEY CONVERSATION KIND PHASE CANCELLED GUARANTEED)
;;;   (defenum Phase (PENDING "Pending") (RUNNING "Running"))   ; 値を明示する形
;;;
;;; 展開(値は member の名前から作る — 小文字にし、`_` / `-` を `-` にそろえる):
;;;   (defclass PlacementMismatch [StrEnum]
;;;     (setv KEY "key" CONVERSATION "conversation" … GUARANTEED "guaranteed"))
;;;   `IN-PROGRESS` / `IN_PROGRESS` → 属性 `IN_PROGRESS`・値 `"in-progress"`。
;;;   `(NAME "value")` の形の member は値をそのまま使う(k8s の `"Running"` のように
;;;   外の約束で綴りが決まっている値のため)。
;;;
;;; 展開の時に拒むもの(Enum が黙って別名にする・読み違える形を先に止める):
;;;   member が 1 つも無い / 名前が英字で始まる `[A-Za-z][A-Za-z0-9_-]*` でない /
;;;   同じ名前(mangle した後)が 2 度 / 同じ値が 2 度(StrEnum は後の方を別名に
;;;   してしまい、member の数が黙って減る)。
;;;
;;; ⚠ `defenum` は template macro ではない: 値を名前から作るので、展開の時に
;;; 計算が要る(quasiquote の逐語の置換では `KEY` から `"key"` を作れない)。
;;; だから共通の品質検査の template の検証(`quality.hy_macro`)には載らず、
;;; 投影の規則は検査器の側に専用の規則として要る(`quality.hy_record` に
;;; defrecord と並べて置く想定 — 設計の記録の「品質検査の側に要る変更」)。
;;; 展開の基底の `StrEnum` は、defrecord の `dataclass` と同じく使う側の
;;; scope の名前を指す(Python 3.11 以上は `enum.StrEnum`)。

(import dataclasses [dataclass])


(defmacro defrecord [name #* fields]
  `(defclass [(dataclass :frozen True :kw-only True)] ~name [] ~@fields))


;; 展開の時に member の名前から値を計算する(template macro の形の外 — 頭注)。
;; 検査の helper を別の関数に切り出すと、展開の時に呼ばれる関数は defk にできない
;; (defk は Program を返し、macro は Hy の model を即座に要る)ので defn になる。
;; それを避けて、計算は macro の本体の中にだけ置く。
(defmacro defenum [name #* members]
  (import re)
  (import hy.models [Expression String Symbol])
  (when (not (isinstance name Symbol))
    (raise (TypeError f"defenum の第 1 引数は enum の名前(symbol)ちょうど: {(hy.repr name)}")))
  (when (not members)
    (raise (ValueError f"defenum {name} に member が 1 つも無い")))
  (setv pairs []
        seen-names {}
        seen-values {})
  (for [member members]
    (cond
      (isinstance member Symbol)
        (setv key member
              value (.replace (.lower (str member)) "_" "-"))
      (and (isinstance member Expression)
           (= (len member) 2)
           (isinstance (get member 0) Symbol)
           (isinstance (get member 1) String))
        (setv key (get member 0)
              value (str (get member 1)))
      True
        (raise (TypeError
                 f"defenum {name} の member は NAME か (NAME \"値\") ちょうど: {(hy.repr member)}")))
    (when (not (re.fullmatch r"[A-Za-z][A-Za-z0-9_-]*" (str key)))
      (raise (ValueError
               f"defenum {name} の member の名前は英字で始まる [A-Za-z][A-Za-z0-9_-]* ちょうど: {key}")))
    (setv mangled (hy.mangle key))
    (when (in mangled seen-names)
      (raise (ValueError
               f"defenum {name} の member の名前が重なっている: {(get seen-names mangled)} と {key}")))
    (when (in value seen-values)
      (raise (ValueError
               f"defenum {name} の値 {(hy.repr value)} が {(get seen-values value)} と {key} で重なっている(StrEnum は後の方を黙って別名にする)")))
    (setv (get seen-names mangled) key
          (get seen-values value) key)
    (.extend pairs [(Symbol mangled) (String value)]))
  `(defclass ~name [StrEnum] (setv ~@pairs)))
