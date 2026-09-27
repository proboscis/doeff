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
;;; 投影の規則(この macro が静的検査を通れる理由): `defrecord` は展開の時に
;;; 頭の辞書を読む計算をするので template macro ではない(defenum と同じ「fixed」)。
;;; 共通の品質検査の側に専用の読み方の規則(`quality.hy_record` の
;;; record_declaration)が在り、この module の頭注の展開と 1 対 1 に対応する。
;;; 使う側の module 契約に宣言は要らない — 由来が固定表(`doeff_hy.record`)に載っている。
;;;
;;; 使い方(頭の辞書の無い形 — 前からの形そのまま):
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
;;; 展開: (defclass [(dataclass :frozen True :kw-only True)] Name [] 欄 …)。
;;; `dataclass` は使う側の scope の名前を指す(使う側が import する)。
;;;
;;; ---------------------------------------------------------------------------
;;; 頭の辞書 {:tags … :check […]} — 定義のタグと作る時の検め(どちらも省ける)
;;; ---------------------------------------------------------------------------
;;;
;;; 出自 = agora-redesign #798 と operator 2026-09-27 "perfect, lets go with def*"
;;; (道具が読む宣言は def*・データの型は defrecord・振る舞いを持つ class は作らない)。
;;; dataclass の `__post_init__` に defn を書いて値を検めていた形を、defrecord の節へ移す。
;;;
;;;   (setv CHAT-ID-PATTERN (re.compile r"c-[0-9A-HJKMNP-TV-Z]{26}"))
;;;
;;;   (defrecord ChatId
;;;     "chat の id"
;;;     {:tags  {:context "chat" :role "type"}
;;;      :check [(CHAT-ID-PATTERN.fullmatch value)]}
;;;     (#^ str value))
;;;
;;; - 頭の辞書は名前(と docstring)の直後に 1 つ。受ける鍵は :tags と :check だけ(他の鍵は展開の誤り)。
;;; - :tags は defk / deff と同じ契約({:context "…" :role "…"}・役は declarations.ROLES)。
;;;   class の属性 `__doeff_tags__`(DefinitionTags)に残す。
;;; - :check は真偽の式の list。各式は欄の名前で欄を参照し、作る時に書いた順に評価する。
;;;   偽なら ValueError「ChatId の欄 value が検め (CHAT-ID-PATTERN.fullmatch value) で落ちた: value='x'」。
;;;   欄を 1 つも参照しない式・型の (: 欄 型)(型は欄の注記で書く)は展開の誤り。
;;;   式の綴りの列を class の属性 `__doeff_checks__` に残す。
;;;
;;; 検めに使う述語の置き場(設計の要): __post_init__ は Program を実行できないので、
;;; :check から defk は呼べない(呼ぶと Program が返り、真に見えて黙って通る — だから
;;; 結果が Program / effect なら TypeError で止める)。述語は関数にせず、
;;;   1. 式を :check にそのまま書く(`(<= start end)`・`(in kind KINDS)`)。
;;;   2. 式が使う値は module の定数に置く(正規表現・閉じた集合 — `(setv CHAT-ID-PATTERN …)`)。
;;;   3. 同じ検めを別の型でも使いたい時は、その欄の型を検め済みの record にする
;;;      (`#^ ChatId chat` — 文字列の欄に同じ述語を並べない)。
;;; どうしても名前つきの述語が要る時だけ、純粋な deff に理由の註
;;; (`; defk にできない: defrecord の :check が作る時に呼ぶ`)と :tags {… :role "judgment"} を付ける。
;;;
;;; 展開(頭の辞書つき):
;;;   (do (import doeff_hy.declarations doeff_hy.record)
;;;       (defclass [(dataclass :frozen True :kw-only True)] ChatId []
;;;         "chat の id"
;;;         ((annotate value str))
;;;         (defn __post-init__ [self]
;;;           (setv value self.value)
;;;           (setv _verdict (CHAT-ID-PATTERN.fullmatch value))
;;;           (when (isinstance _verdict #(DoExpr EffectBase)) (raise (TypeError "… Program を返した …")))
;;;           (doeff_hy.record.require-check "ChatId" #("value") "(CHAT-ID-PATTERN.fullmatch value)"
;;;                                          (bool _verdict) {"value" value})
;;;           None))
;;;       (setattr ChatId "__doeff_tags__" (doeff_hy.declarations.DefinitionTags :context "chat" :role "type"))
;;;       (setattr ChatId "__doeff_checks__" #("(CHAT-ID-PATTERN.fullmatch value)")))
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

(require doeff-hy.macros [deff])
(import dataclasses [dataclass])
(import doeff [DoExpr EffectBase])


;; 展開の時に頭の辞書を読む計算は macro の本体の中にだけ置く(defenum と同じ — 切り出すと
;; 展開の時に呼ぶ関数は defk にできず defn になり、quasiquote を持つ defn は型の投影に載らない)。
;; 欄の読み方(hy-index の record_def・quality.hy_record と同じ): `#^ T x` = (annotate x T)・
;; `(#^ T x)` = ((annotate x T))・`(setv #^ T x 既定値)`・裸の記号 x。それ以外の form は欄ではない。
(defmacro defrecord [name #* forms]
  (import hy.models [Dict Expression Keyword List Sequence String Symbol])
  (import doeff_hy.declarations [declared-value refuse-unknown-keys tags-form])
  (setv docstring None
        rest (list forms))
  (when (and rest (isinstance (get rest 0) String))
    (setv docstring (get rest 0)
          rest (cut rest 1 None)))
  (when (not (and rest (isinstance (get rest 0) Dict)))
    (return `(defclass [(dataclass :frozen True :kw-only True)] ~name [] ~@forms)))
  (setv header (get rest 0)
        fields (cut rest 1 None)
        where (+ "defrecord " (str name)))
  (refuse-unknown-keys header #(":tags" ":check") where)
  (setv checks (declared-value header ":check")
        tags (declared-value header ":tags"))
  (when (and (is-not checks None) (not (isinstance checks List)))
    (raise (SyntaxError (.format "{}: :check は検めの式の list([(pred 欄) …] の形): {}" where (hy.repr checks)))))
  (setv annotated (fn [form] (when (and (isinstance form Expression) (>= (len form) 2)
                                         (= (str (get form 0)) "annotate"))
                               (get form 1)))
        names [])
  (for [form fields]
    (setv target (cond
                   (isinstance form Symbol) form
                   (annotated form) (annotated form)
                   (and (isinstance form Expression) (= (len form) 1)) (annotated (get form 0))
                   (and (isinstance form Expression) (>= (len form) 2) (= (str (get form 0)) "setv"))
                     (annotated (get form 1))
                   True None))
    (when (isinstance target Symbol)
      (.append names (hy.mangle target))))
  (setv statements []
        bound [])
  (for [check (or checks [])]
    (when (and (isinstance check Expression) (>= (len check) 3)
               (isinstance (get check 0) Keyword) (= (str (get check 0)) ":"))
      (raise (SyntaxError (.format "{}: :check の {} — 欄の型は欄の注記 #^ 型 で書く(:check は真偽の式だけ)" where (hy.repr check)))))
    ;; 式が参照する欄(欄の順)。`value.x` のような dotted の記号は頭の名で読む。
    (setv seen (set)
          pending [check])
    (while pending
      (setv form (.pop pending)
            head (when (isinstance form Symbol) (get (.split (str form) ".") 0)))
      (cond
        head (.add seen (hy.mangle head))
        (and (isinstance form Sequence) (not (isinstance form String))) (.extend pending form)))
    (setv used (lfor n names :if (in n seen) n))
    (when (not used)
      (raise (SyntaxError (.format "{}: :check の {} は欄を 1 つも参照しない(欄 = {})" where (hy.repr check) (.join " " names)))))
    (.extend bound (lfor n used :if (not-in n bound) n))
    ;; 結果が Program / effect(defk を :check で呼んだ形)なら TypeError — Program は真に見えて黙って通ってしまうため。
    (setv verdict (hy.gensym "verdict")
          text (.lstrip (hy.repr check) "'"))
    (.extend statements
      [`(setv ~verdict ~check)
       `(when (isinstance ~verdict #(doeff_hy.record.DoExpr doeff_hy.record.EffectBase))
          (raise (TypeError ~(.format "{} の :check の {} が Program を返した — :check は純粋な式で書く(defk は作る時に呼べない)" name text))))
       `(doeff_hy.record.require-check
          ~(str name) #(~@(lfor n used (String (hy.unmangle n)))) ~text
          (bool ~verdict) {~@(sum (lfor n used [(String (hy.unmangle n)) (Symbol n)]) [])})]))
  (setv post-init (if statements
                      [`(defn __post-init__ [self]
                          ~@(lfor n bound `(setv ~(Symbol n) (. self ~(Symbol n))))
                          ~@statements
                          None)]
                      []))
  `(do
     (import doeff_hy.declarations doeff_hy.record)
     (defclass [(dataclass :frozen True :kw-only True)] ~name []
       ~@(if (is docstring None) [] [docstring])
       ~@fields
       ~@post-init)
     (setattr ~name "__doeff_tags__" ~(tags-form tags where))
     (setattr ~name "__doeff_checks__" #(~@(lfor c (or checks []) (String (.lstrip (hy.repr c) "'")))))))


(deff require-check [#^ str record #^ tuple fields #^ str check #^ bool passed #^ dict values]  ; defk にできない: defrecord の展開が dataclass の __post_init__ から呼ぶ(Program を実行できない所)
  {:pre [(: record str) (: fields tuple) (: check str) (: passed bool) (: values dict)]
   :post [(: % None)]
   :tags {:context "doeff-hy" :role "judgment"}}
  "defrecord の :check の 1 式の結果を検める。偽なら ValueError で「どの型のどの欄がどの検めで落ちたか」と欄の値を名指す
   (Program を返した式は展開の中で先に TypeError にしてある)。"
  (when (not passed)
    (raise (ValueError (.format "{} の欄 {} が検め {} で落ちた: {}"
                                record (.join "・" fields) check
                                (.join " " (gfor #(k v) (.items values) (.format "{}={!r}" k v)))))))
  None)


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
