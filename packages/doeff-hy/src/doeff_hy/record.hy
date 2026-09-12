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

(import dataclasses [dataclass])


(defmacro defrecord [name #* fields]
  `(defclass [(dataclass :frozen True :kw-only True)] ~name [] ~@fields))
