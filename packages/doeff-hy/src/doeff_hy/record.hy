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
;;; 展開: 型検査の投影(static-view)では (defclass [(dataclass :frozen True :kw-only True)] Name [] 欄 …)。
;;; 実行では (defclass [(dataclass :init False :repr False :eq False :kw-only True)] Name [] 欄 … 関数 …) と仕上げ(record_runtime の
;;; seal-record)— dataclass には欄の表だけを作らせ、__init__(kw-only)・__repr__・__eq__・__hash__・凍結の __setattr__ / __delattr__ は
;;; 展開が書く(関数が module の code として .pyc に入り、読み込みの時に dataclass が関数の source を compile しない — 1 型 約 1 ms →
;;; 約 0.1 ms・card ki-a0cca3bdb9a0)。型ごとに書く関数は __init__(注記なし)・__eq__・__hash__ と :check の __post_init__ だけで、
;;; __repr__ と凍結の __setattr__ / __delattr__ は record_runtime の共有の 1 組を指し、型は欄の名の並びを tuple で持つ。docstring は実行の展開に置かず、__doc__ は欄の名から組む
;;; 共有の記述子(型ごとの関数と文が job の memory を 10 MB 越えて持った・card ki-ff2c33449b3f)。
;;; 振る舞いは標準の dataclass(frozen・kw_only)と同じ(tests/defrecord_shape_deftests.hy が
;;; 突き合わせる)。欄の形を展開が読み切れない時(名を介した field・字面でない :init / :repr / :compare / :hash・欄の名 self / object・
;;; 本体が __init__ ほかを自分で書く)は投影と同じ展開(dataclass が関数を作る)にする。
;;; `dataclass` は使う側の scope の名前を指す(使う側が import する)。
;;; 関数の本体の中に書いた defrecord・defwire の実行の展開は、展開の import(doeff_hy ほか)と型と記帳をすぐ呼ぶ関数に入れて
;;; (setv Name ((fn [] … Name))) と型の名だけを束ねる(doeff_hy.macros の _scoped-definition — import した名が外の関数の局所の名に
;;; ならないため・card ki-8a17293bc3e5・tests/test_nested_definition_scope.py)。型の __qualname__ には包んだ関数の名が入る
;;; (defwire は中の defrecord がもう 1 度包むので 2 つ)。module と class の本体と型検査の投影は包まない。
;;;
;;; ---------------------------------------------------------------------------
;;; 頭の辞書 {:tags … :check […]} — 定義のタグと作る時の検め(どちらも省ける)
;;; ---------------------------------------------------------------------------
;;;
;;; 出自 = agora-redesign #798 と operator の決定 2026-09-27
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
;;; - 頭の辞書は名前(と docstring)の直後に 1 つ。受ける鍵は :tags・:check・:failure だけ(他の鍵は展開の誤り)。
;;; - :tags は defk / deff と同じ契約({:context "…" :role "…"}・役は declarations.ROLES)。
;;;   class の属性 `__doeff_tags__`(DefinitionTags)に残す。
;;; - :check は真偽の式の list。各式は欄の名前で欄を参照し、作る時に書いた順に評価する。
;;;   偽なら ValueError「ChatId の欄 value が検め (CHAT-ID-PATTERN.fullmatch value) で落ちた: value='x'」。
;;;   欄を 1 つも参照しない式・型の (: 欄 型)(型は欄の注記で書く)は展開の誤り。
;;;   式の綴りの列を class の属性 `__doeff_checks__` に残す。
;;; - :failure True は「この型は失敗の値」の印(値は字面の True / False だけ)。class の属性 `__doeff_failure__` に残し、
;;;   doeff-linter が実行せずに読む(手書きの失敗の再送出 DOEFF122 が、match の腕の型を失敗の型と知るため — 型の名から
;;;   推し量らない。ADR-DOE-HY-007 R10)。effect の答えの失敗は defeffect の :failure / :absent で宣言する(ADR-DOE-CORE-EFFECTS-003
;;;   R5)— こちらは effect の答えでない値(検めの関数が返す断りなど)のための印。
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
;;;           (when (is-not _verdict True)   ; 答えが True そのものなら残りは要らない(#2421)
;;;             (when (isinstance _verdict #(DoExpr EffectBase)) (raise (TypeError "… Program を返した …")))  ; 実行時の展開だけ
;;;             (when (not _verdict)
;;;               (doeff_hy.record.require-check "ChatId" #("value") "(CHAT-ID-PATTERN.fullmatch value)"
;;;                                              (bool _verdict) {"value" value})))
;;;           None))
;;;       (setattr ChatId "__doeff_tags__" (doeff_hy.declarations.DefinitionTags :context "chat" :role "type"))
;;;       (setattr ChatId "__doeff_checks__" #("(CHAT-ID-PATTERN.fullmatch value)")))
;;;
;;; ---------------------------------------------------------------------------
;;; `defwire` — JSON の境目の型(JSON を解いて確かめる型を 1 つの宣言で建てる)
;;; ---------------------------------------------------------------------------
;;;
;;; 出自 = agora-redesign #840・operator の決定 2026-09-28(素の dict を直に使わせず pydantic のように解いて確かめる。
;;; そのための def* の macro を置く案 A を採る)。
;;; 決まりの正本 = ADR-DOE-HY-007 R8。
;;;
;;; 何のための足場か: 外から来た JSON を JsonValue(名前を変えた素の dict)のまま運び、読む所ごとに手で分解すると、
;;; 欄の名の綴り違いも型の違いも使う所まで見えない。defwire は JSON の形を型として宣言し、解き手
;;; (doeff_hy.wire の parse / dump — pydantic の TypeAdapter)で境目の 1 か所で一度に解いて確かめる。
;;;
;;;   (require doeff-hy.record [defwire])
;;;   (import dataclasses [dataclass])
;;;   (import doeff_hy.wire [parse dump Malformed])
;;;
;;;   (defwire LandingRow
;;;     "取り込みの台帳の 1 行(記録の service が返す JSON)"
;;;     {:tags {:context "land-notice" :role "type"} :names :camel :unknown :reject
;;;      :check [(.startswith lane-id "L")]}
;;;     (#^ str lane-id)
;;;     (#^ LandState state)
;;;     (setv #^ (| int None) landed-at None))
;;;
;;;   (<- row (parse LandingRow raw))   ; 答え = LandingRow の値か Malformed(どの型の・どの欄が・なぜ)
;;;   (<- raw (dump row))               ; JSON の値へ(送り出す foundation だけ)
;;;
;;; - 欄の形は defrecord と同じ(`#^ T x`・`(#^ T x)`・既定値は `(setv #^ T x 値)`)。展開は defrecord をそのまま使う
;;;   (凍結・キーワード引数だけ・:tags・:check)ので、`dataclass` は defrecord と同じく使う側が import する。
;;; - 頭の辞書は必須。受ける鍵は :names・:unknown・:tags・:check だけ。
;;;   :names(必須)= wire の欄の名の写し。:camel(lane-id → laneId)・:snake(lane_id)・:kebab(lane-id)・
;;;     明示の辞書 {lane-id "LANE" …}(欄を全部ちょうど名指す)。区切りは - と _。Python の欄の名では受けない。
;;;   :unknown = 知らない欄の扱い。:reject(既定 — Malformed)か :ignore(読み捨てる)。
;;; - 型の検めは厳しい(文字列を数にしない・真偽を数にしない・配列は tuple の欄・defenum の欄は値の綴り)。
;;;   入れ子の欄の型も defwire の型にする(素の defrecord は wire の形を持たない)。
;;;
;;; 展開(上の LandingRow):
;;;   (do (import typing [ClassVar :as _doeff-ClassVar])
;;;       (import doeff_hy.wire)
;;;       (hy.R.doeff_hy/record.defrecord LandingRow "…" {:tags {…} :check […] :class-vars […]} 欄 …)
;;;       (setattr LandingRow "__pydantic_config__" (doeff_hy.wire.wire-config {"lane_id" "laneId" …} "reject"))
;;;       (setattr LandingRow "__doeff_wire__" (doeff_hy.wire.wire-shape LandingRow {"lane_id" "laneId" …} "reject")))
;;;
;;; 投影の規則: defrecord と同じ形の class に写す(:names と :unknown は型の形を変えない)— 共通の品質検査の
;;; `quality.hy_record` の同じ便で持つ。hy-index は kind defrecord として読む(doeff-indexer の record_def)。
;;;
;;; ---------------------------------------------------------------------------
;;; `defenum` — 閉じた値の集合(文字列の Enum)を 1 行で建てる
;;; ---------------------------------------------------------------------------
;;;
;;; 出自 = operator の決定 2026-09-26(enum も macro で建てる)。
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
;; `(#^ T x)` = ((annotate x T))・`(setv #^ T x 既定値 #^ U y 既定値 …)`(組ごとに 1 欄)・裸の記号 x。それ以外の form は欄ではない。
;; 展開の文の並び(import の doeff_hy — record_runtime・頭の辞書の宣言 — と型と記帳)は、doeff_hy.macros の _scoped-definition
;; (定義の macro が全部通る 1 か所)で書いた所に合わせて組む: 関数の本体の中では、すぐ呼ぶ関数に入れて型の名だけを束ねる —
;; 並べたままだと import した doeff_hy が外の関数全体の局所の名になり、外の関数で先に読む module の段の doeff_hy が
;; UnboundLocalError で落ちた(card ki-8a17293bc3e5)。module と class の本体では今までどおり並べる。
(defmacro defrecord [_hy-compiler name #* forms]
  (import doeff_hy.macros [_scoped-definition])
  (import hy.models [Dict Expression Keyword List Sequence String Symbol])
  (import doeff_hy.declarations [declared-value field-targets refuse-unknown-keys tags-form])
  (import doeff_hy.expansion_names [import-form])
  (setv docstring None
        rest (list forms))
  (when (and rest (isinstance (get rest 0) String))
    (setv docstring (get rest 0)
          rest (cut rest 1 None)))
  (import doeff_hy.static_view [static-view-enabled])
  (setv static-view (static-view-enabled))
  ;; 実行の展開の class の form(頭注「関数は展開の時に書く」— card ki-a0cca3bdb9a0): dataclass には欄の表(__dataclass_fields__・
  ;; 既定値・field の default-factory・可変の既定の断り)だけを作らせ、関数(__init__・__repr__・__eq__・__hash__・凍結の
  ;; __setattr__ / __delattr__)は展開がここで書く — 関数は module の code として .pyc に入り、読み込みの時に dataclass が関数の
  ;; source を exec(compile)しない(1 型 約 1 ms → 約 0.1 ms)。欄の形を展開で読み切れない時(下の断りの条件)は None を返し、
  ;; 呼び手は今までの展開(dataclass が関数を作る)にする。型検査の投影(static-view)は dataclass のまま(pyright が dataclass として読む)。
  (setv class-form
    (fn [docstring class-vars fields post-init]
      (setv generated #("__init__" "__repr__" "__eq__" "__hash__" "__setattr__" "__delattr__" "__post_init__")
            literal {"True" True "False" False "None" None}
            annotated (fn [form] (and (isinstance form Expression) (= (len form) 3) (= (str (get form 0)) "annotate")
                                      (isinstance (get form 1) Symbol)))
            field-call? (fn [form] (and (isinstance form Expression) form (isinstance (get form 0) Symbol)
                                        (in (str (get form 0)) #("field" "dataclasses.field"))))
            ;; 本体の form が、展開が書く関数を自分で書くか(defn・defn/a ほか — 名は飾りの list の後ろでも読む)。答えの型の注記つきの名
            ;; (defn #^ None __post-init__ …)は Hy が (annotate __post-init__ None) と読むので、注記の中の名を読む(card ki-e77d44aa61e5 —
            ;; 記号の名だけを見た形は本体の __post_init__ を見落とし、展開の __init__ が呼ばなかった)。
            own-method? (fn [form] (and (isinstance form Expression) (>= (len form) 2) (isinstance (get form 0) Symbol)
                                        (.startswith (str (get form 0)) "def")
                                        (do (setv written (if (and (isinstance (get form 1) List) (>= (len form) 3)) (get form 2) (get form 1))
                                                  named (if (annotated written) (get written 1) written))
                                            (and (isinstance named Symbol) (in (hy.mangle (str named)) generated)))))
            ;; 欄の的と既定値の組(的・既定値の form・既定値の有無)を書いた順に(field-targets と同じ読み方)。
            targets (sum (lfor form fields
                               (cond
                                 (annotated form) [#(form None False)]
                                 (and (isinstance form Expression) (= (len form) 1) (annotated (get form 0))) [#((get form 0) None False)]
                                 (and (isinstance form Expression) (>= (len form) 3) (= (str (get form 0)) "setv"))
                                   (lfor #(t v) (zip (cut form 1 None 2) (cut form 2 None 2)) :if (annotated t) #(t v True))
                                 True []))
                         [])
            ;; field の呼びの鍵と値(鍵は mangle した名)。鍵の数が合わない呼びは None。
            call-args (fn [form] (setv args (list (cut form 1 None)))
                        (if (% (len args) 2)
                            None
                            (lfor #(k v) (zip (cut args None None 2) (cut args 1 None 2))
                                  #((if (isinstance k Keyword) (hy.mangle (str k.name)) None) v))))
            ;; 欄 1 つの形 — (記号・python の名・注記・種類 required / plain / call・値を持つか・工場を持つか・init・repr・compare・hash)。
            ;; 展開が読み切れない欄は None(field の呼びの鍵が分からない・旗が字面でない・欄の名が self / object)。
            spec-of (fn [target default has-default]
                      (setv sym (get target 1)
                            py (hy.mangle (str sym))
                            call (and has-default (field-call? default))
                            pairs (if call (call-args default) []))
                      (cond
                        (in py #("self" "object")) None
                        (is pairs None) None
                        (any (gfor #(k v) pairs (not (in k #("init" "repr" "compare" "hash" "default" "default_factory" "metadata" "doc")))))
                          None
                        (any (gfor #(k v) pairs (and (in k #("init" "repr" "compare" "hash"))
                                                     (not (and (isinstance v Symbol) (in (str v) literal))))))
                          None
                        True
                          (do (setv flags (dfor #(k v) pairs :if (in k #("init" "repr" "compare" "hash")) k (get literal (str v)))
                                    keys (frozenset (gfor #(k v) pairs k)))
                              {"sym" sym "py" py "ann" (get target 2)
                               "kind" (cond call "call" has-default "plain" True "required")
                               "has-value" (if call (in "default" keys) has-default)
                               "has-factory" (in "default_factory" keys)
                               "init" (.get flags "init" True) "repr" (.get flags "repr" True)
                               "compare" (.get flags "compare" True) "hash" (.get flags "hash" None)})))
            specs (lfor #(t d h) targets (spec-of t d h)))
      (when (or (any (gfor form fields (isinstance form Symbol))) (any (gfor form fields (own-method? form)))
                (any (gfor s specs (is s None))))
        (return None))
      ;; 書く関数は定義した型を名でなく `__class__`(Python が method に作る、定義した class の閉包)と引数なしの super で読む —
      ;; class の本体に書いた型の名は外の class の属性で、method は名を引く時に class の段を飛ばすので、名で読むと凍結の
      ;; __setattr__ と default-factory・init を切った欄の __init__ が NameError で落ちた(tests/test_nested_definition_scope.py の
      ;; test_class_body_type_methods_read_their_own_class)。
      (setv self-sym (hy.gensym "self")
            missing 'doeff_hy.record_runtime.FACTORY-DEFAULT
            factory (fn [py] `((. (get (. __class__ __dataclass_fields__) ~py) default-factory)))
            ;; 欄 1 つの __init__ の引数(None = 引数にしない — init を切った欄)と、self に値を置く式(None = 置かない)。
            ;; 引数に型の注記は書かない(下の class の form の注 — card ki-ff2c33449b3f)。
            init-of (fn [s]
                      (setv sym (get s "sym") py (get s "py")
                            put (fn [value] `(object.__setattr__ ~self-sym ~py ~value)))
                      (cond
                        (not (get s "init"))
                          #(None (cond (get s "has-factory") (put (factory py)) (get s "has-value") (put `(. __class__ ~sym)) True None))
                        (get s "has-factory")
                          #(`[~sym ~missing] (put `(if (is ~sym ~missing) ~(factory py) ~sym)))
                        (and (= (get s "kind") "call") (get s "has-value"))
                          #(`[~sym (. ~sym default)] (put sym))
                        (= (get s "kind") "plain")
                          #(`[~sym ~sym] (put sym))
                        True
                          #(sym (put sym))))
            inits (lfor s specs (init-of s))
            params (lfor #(p _) inits :if (is-not p None) p)
            sets (lfor #(_ v) inits :if (is-not v None) v)
            shown (lfor s specs :if (get s "repr") (get s "py"))
            compared (lfor s specs :if (get s "compare") (get s "py"))
            hashed (lfor s specs :if (if (is (get s "hash") None) (get s "compare") (get s "hash")) (get s "py"))
            plain `#(~@(lfor s specs :if (= (get s "kind") "plain") (String (get s "py"))))
            names-of (fn [names] `#(~@(lfor n names (String n))))
            other (hy.gensym "other"))
      ;; 型ごとに書くのは __init__・__eq__・__hash__(と :check の __post_init__)。__repr__ と凍結の __setattr__ / __delattr__ は
      ;; record_runtime の共有の 1 組を指し、型は欄の名の並びを字面の tuple で持つ(card ki-ff2c33449b3f — 型ごとの関数が型 約 1,760 の
      ;; job で 10 MB を越えた)。__eq__ と __hash__ は値を辞書の鍵や集合に入れるたびに呼ばれるので型ごとのまま — 共有の 1 つにすると
      ;; 欄の名を回す分 1.3〜3 倍遅くなった(Python 3.14t の測り)。__init__ の引数に型の注記を書かない(実行時に読む所が無く、注記ごとに
      ;; __annotate__ の関数が残る — 欄の型は class の注記と型検査の投影が持つ)。docstring は実行の展開に置かず(実行時に読む所が無い・
      ;; 型検査の投影と source には残る)、__doc__ は読んだ時に欄の名から組む共有の記述子にする(dataclass が docstring の無い型に
      ;; 読み込みの時に署名を計算しない)。
      `(do
       (defclass [(dataclass :init False :repr False :eq False :kw-only True)] ~name []
         (setv __doc__ doeff_hy.record_runtime.RECORD-DOC
               __doeff-fields__ ~(names-of (lfor s specs (get s "py")))
               __doeff-repr-fields__ ~(names-of shown)
               __repr__ doeff_hy.record_runtime.record-repr
               __setattr__ doeff_hy.record_runtime.record-setattr
               __delattr__ doeff_hy.record_runtime.record-delattr)
         ~@(or class-vars [])
         ~@fields
         ~@post-init
         (defn __init__ [~self-sym ~@(if params ['* #* params] [])]
           ~@sets
           ~@(if post-init [`(.__post-init__ ~self-sym)] [])
           None)
         (defn __eq__ [~self-sym ~other]
           (cond
             (is ~self-sym ~other) True
             (is (. ~other __class__) (. ~self-sym __class__))
               (and ~@(or (lfor n compared `(= (. ~self-sym ~(Symbol n)) (. ~other ~(Symbol n)))) ['True]))
             True NotImplemented))
         (defn __hash__ [~self-sym]
           (hash #(~@(lfor n hashed `(. ~self-sym ~(Symbol n)))))))
       (doeff_hy.record_runtime.seal-record ~name ~plain ~(bool post-init)))))
  (when (not (and rest (isinstance (get rest 0) Dict)))
    (setv written (when (not static-view) (class-form docstring None rest [])))
    (return (_scoped-definition _hy-compiler name
              [(if (is written None)
                   `(defclass [(dataclass :frozen True :kw-only True)] ~name [] ~@forms)
                   `(do (import doeff_hy.record_runtime) ~written))]
              :nested-reads True)))
  (setv header (get rest 0)
        fields (cut rest 1 None)
        where (+ "defrecord " (str name)))
  (refuse-unknown-keys header #(":tags" ":check" ":failure" ":class-vars") where)
  (setv checks (declared-value header ":check")
        tags (declared-value header ":tags")
        failure (declared-value header ":failure")
        class-vars (declared-value header ":class-vars"))
  ;; :class-vars = defwire の展開だけが渡す内部の口: class の本体に置く ClassVar の注記 `#^ (get ClassVar T) 名` の list。
  ;; 型検査に属性の型を見せるための注記で、欄の列(fields)と別に運ぶので欄の読み・:check の名・dataclass / pydantic の欄・
  ;; __match_args__・構築子の引数には混ざらない(dataclass は ClassVar の注記を欄にしない)。
  (when (and (is-not class-vars None)
             (not (and (isinstance class-vars List)
                       (all (gfor form class-vars
                                  (and (isinstance form Expression) (= (len form) 3)
                                       (= (str (get form 0)) "annotate") (isinstance (get form 1) Symbol)
                                       (isinstance (get form 2) Expression) (= (str (get (get form 2) 0)) "get")
                                       (.endswith (hy.repr (get (get form 2) 1)) "ClassVar")))))))
    (raise (SyntaxError (.format "{}: :class-vars は ClassVar の注記 #^ (get ClassVar 型) 名 の list: {}" where (hy.repr class-vars)))))
  (when (and (is-not failure None) (not (and (isinstance failure Symbol) (in (str failure) #("True" "False")))))
    (raise (SyntaxError (.format "{}: :failure は字面の True か False(失敗の型の印): {}" where (hy.repr failure)))))
  (when (and (is-not checks None) (not (isinstance checks List)))
    (raise (SyntaxError (.format "{}: :check は検めの式の list([(pred 欄) …] の形): {}" where (hy.repr checks)))))
  ;; 欄の読み方の正本は declarations の field-targets(defwire・hy-index・doeff-linter と揃える)。
  (setv names (lfor target (field-targets fields) (hy.mangle target)))
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
    ;; 検めの多くは真偽の式(`(<= start end)`)で、答えは True そのもの — その時は Program でも偽でもないので、残りの 2 つの
    ;; 検め(Program の判定・require-check)を飛ばす。record を 1 つ作るたびに検めの数だけ走る所で、Program の判定
    ;; (DoExpr の isinstance は Python の metaclass を通る)と require-check の呼び(:pre の isinstance 5 つと値の辞書)を
    ;; 毎回払っていた(agora-redesign #2421)。True でない答え(正規表現の Match など)は今までどおり Program を判定し、
    ;; 偽の時だけ require-check を呼ぶ — require-check は偽の時にしか何もしない(真なら None を返すだけ)ので、断る物と文言は同じ。
    ;; 型検査の展開では答えを object に広げる — `Match | None` を返す検め式は `is not True` と比べると「常に真」
    ;; (reportUnnecessaryComparison)の赤になり、書き手は直せない(agora-redesign #4196)。実行時の展開は答えをそのまま置く。
    ;; Program の判定は実行時の展開だけに置く(card ki-601652993a18): 型検査の展開に置くと、pyright 1.1.414 は
    ;; `isinstance(v, (DoExpr, EffectBase))`(DoExpr は型検査では program の node の union)の型を、同じ process で前に検めた
    ;; file によって `tuple[UnionType, type[EffectBase[_Answer]]]` と読み、class_or_tuple の reportArgumentType の赤を出した
    ;; (agora-controllers の日次 — repo 全体を `--threads 2` で測った時だけ・同じ file の同じ形 2 つのうち 1 つだけ)。書き手は直せない。
    ;; 判定は実行時に Program を返した :check を断るためだけの物で、型検査の展開の型(答えは object)には何も足さない。
    (.extend statements
      [`(setv ~verdict ~(if static-view `(_doeff-cast object ~check) check))
       `(when (is-not ~verdict True)
          ~@(if static-view
                []
                [`(when (isinstance ~verdict #(doeff_hy.record.DoExpr doeff_hy.record.EffectBase))
                    (raise (TypeError ~(.format "{} の :check の {} が Program を返した — :check は純粋な式で書く(defk は作る時に呼べない)" name text))))])
          (when (not ~verdict)
            (doeff_hy.record.require-check
              ~(str name) #(~@(lfor n used (String (hy.unmangle n)))) ~text
              (bool ~verdict) {~@(sum (lfor n used [(String (hy.unmangle n)) (Symbol n)]) [])})))]))
  (setv post-init (if statements
                      [`(defn __post-init__ [self]
                          ~@(lfor n bound `(setv ~(Symbol n) (. self ~(Symbol n))))
                          ~@statements
                          None)]
                      []))
  (setv written (when (not static-view) (class-form docstring class-vars fields post-init)))
  (_scoped-definition _hy-compiler name
    [`(do
        (import doeff_hy.declarations doeff_hy.record ~@(if (is written None) [] ['doeff_hy.record_runtime]))
        ~@(if (and static-view statements) [(import-form ["cast"])] [])
        ~@(if (is written None)
              [`(defclass [(dataclass :frozen True :kw-only True)] ~name []
                  ~@(if (is docstring None) [] [docstring])
                  ~@(or class-vars [])
                  ~@fields
                  ~@post-init)]
              [written])
        (setattr ~name "__doeff_tags__" ~(tags-form tags where))
        (setattr ~name "__doeff_checks__" #(~@(lfor c (or checks []) (String (.lstrip (hy.repr c) "'")))))
        (setattr ~name "__doeff_failure__" ~(if (and (is-not failure None) (= (str failure) "True")) 'True 'False)))]
    :nested-reads True))


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


;; JSON の境目の型(頭注の「defwire」)。欄の読み方は defrecord と同じ(展開は defrecord をそのまま使う)。
;; wire の名の計算は展開の時にだけ要るので macro の本体の中に置く(defrecord と同じ理由 — 切り出すと defn になる)。
(defmacro defwire [_hy-compiler name #* forms]
  (import re)
  (import hy.models [Dict Expression Keyword List String Symbol])
  (import doeff_hy.declarations [declared-value field-targets refuse-unknown-keys])
  (import doeff_hy.expansion_names [import-form])
  (import doeff_hy.macros [_scoped-definition])
  (when (not (isinstance name Symbol))
    (raise (SyntaxError (.format "defwire の第 1 引数は型の名前(symbol)ちょうど: {}" (hy.repr name)))))
  (setv where (+ "defwire " (str name))
        docstring None
        rest (list forms))
  (when (and rest (isinstance (get rest 0) String))
    (setv docstring (get rest 0)
          rest (cut rest 1 None)))
  (when (not (and rest (isinstance (get rest 0) Dict)))
    (raise (SyntaxError (.format "{}: 名前(と docstring)の直後に頭の辞書 {{:names … :unknown …}} が要る — wire の欄の名の写しを黙って決めない" where))))
  (setv header (get rest 0)
        fields (cut rest 1 None))
  (refuse-unknown-keys header #(":tags" ":check" ":names" ":unknown") where)
  (setv names-form (declared-value header ":names")
        unknown-form (declared-value header ":unknown"))
  (when (is names-form None)
    (raise (SyntaxError (.format "{}: :names が要る — :camel / :snake / :kebab か {{欄 \"wire の名\" …}}(欄を全部)" where))))
  (setv unknown (cond
                  (is unknown-form None) "reject"
                  (and (isinstance unknown-form Keyword) (in (str unknown-form) #(":reject" ":ignore"))) (cut (str unknown-form) 1 None)
                  True (raise (SyntaxError (.format "{}: :unknown は :reject か :ignore: {}" where (hy.repr unknown-form))))))
  ;; 欄の名(defrecord と同じ読み方 — 書いた順)。
  (setv targets (field-targets fields))
  ;; 欄の名 → wire の名。:camel = 2 つ目からの区切りの頭を大文字にしてつなぐ(lane-id → laneId)・:snake = 区切りを _(lane_id)・
  ;; :kebab = 区切りを -(lane-id)。区切り = - と _。明示の辞書は欄を全部ちょうど名指す(黙って既定の写しへ倒さない)。
  (setv wire-names {})
  (cond
    (isinstance names-form Dict)
      (do
        (setv explicit {})
        (for [#(key value) (zip (cut names-form None None 2) (cut names-form 1 None 2))]
          (when (not (and (isinstance key Symbol) (isinstance value String) (str value)))
            (raise (SyntaxError (.format "{}: :names の辞書は {{欄 \"wire の名\" …}}(欄は記号・wire の名は空でない文字列): {} {}"
                                         where (hy.repr key) (hy.repr value)))))
          (setv (get explicit (hy.mangle key)) (str value)))
        (setv declared (lfor t targets (hy.mangle t))
              missing (lfor n declared :if (not-in n explicit) (hy.unmangle n))
              extra (lfor n explicit :if (not-in n declared) (hy.unmangle n)))
        (when missing
          (raise (SyntaxError (.format "{}: :names の辞書に欄が足りない: {}" where (.join " " missing)))))
        (when extra
          (raise (SyntaxError (.format "{}: :names の辞書に知らない欄: {}" where (.join " " extra)))))
        (for [t targets]
          (setv (get wire-names (hy.mangle t)) (get explicit (hy.mangle t)))))
    (and (isinstance names-form Keyword) (in (str names-form) #(":camel" ":snake" ":kebab")))
      (for [t targets]
        (when (not (re.fullmatch r"[A-Za-z][A-Za-z0-9_-]*" (str t)))
          (raise (SyntaxError (.format "{}: 欄 {} の名から wire の名を作れない(英字で始まる [A-Za-z0-9_-] だけ)— :names を辞書で書く" where t))))
        (setv parts (lfor p (re.split r"[-_]" (str t)) :if p p)
              (get wire-names (hy.mangle t))
                (cond
                  (= (str names-form) ":camel") (+ (get parts 0) (.join "" (lfor p (cut parts 1 None) (+ (.upper (get p 0)) (cut p 1 None)))))
                  (= (str names-form) ":snake") (.join "_" parts)
                  True (.join "-" parts))))
    True
      (raise (SyntaxError (.format "{}: :names は :camel / :snake / :kebab か {{欄 \"wire の名\" …}}: {}" where (hy.repr names-form)))))
  (setv seen {})
  (for [#(field wire) (.items wire-names)]
    (when (in wire seen)
      (raise (SyntaxError (.format "{}: 欄 {} と {} が同じ wire の名 {!r} になる" where (hy.unmangle (get seen wire)) (hy.unmangle field) wire))))
    (setv (get seen wire) field))
  ;; defrecord へ渡す頭の辞書(:tags と :check だけ — :names と :unknown は wire の形)。
  ;; :class-vars は型の本体に `__doeff_wire__: ClassVar[WireShape]` の注記を置く(下の setattr が置く値の型を型検査に見せる —
  ;; dump の引数 WireValue を defwire の型が満たすと読める。setattr は記帳として型検査の展開から外れる・agora-redesign #2296)。
  (setv record-header (Dict (+ (sum (lfor key #(":tags" ":check")
                                          :if (is-not (declared-value header key) None)
                                          [(Keyword (cut key 1 None)) (declared-value header key)])
                                    [])
                               [(Keyword "class-vars")
                                `[#^ (get _doeff-ClassVar doeff_hy.wire.WireShape) __doeff-wire__]])))
  ;; 書いた所に合わせて組む(defrecord と同じ — card ki-8a17293bc3e5): 関数の本体の中では下の import(`_doeff-ClassVar`・
  ;; doeff_hy)と型と記帳をすぐ呼ぶ関数に入れ、型の名だけを束ねる。中の defrecord は、その関数の中でもう 1 度自分を包む
  ;; (型の __qualname__ に包んだ関数の名が 2 つ入る)— 包まない型の組み立てを別の macro に切り出すと、その macro の読み方の
  ;; 規則が共通の品質検査の固定表(quality.hy_projection の FIXED_MACROS)に無く、record.hy の検査が欠測になる。
  (_scoped-definition _hy-compiler name [
     ;; 注記が読む名(Python 3.13 までは class の本体で注記を評価する)を型を建てる前に読む。ClassVar は from-import の別名で読む:
     ;; pyright は dataclass の欄から外す ClassVar を `ClassVar` の名か `typing.ClassVar` の形でだけ見分け(module の再公開の
     ;; 名では見分けない)、`import typing` を defwire ごとに出すと strict が同じ import の重ね(reportDuplicateImport)を赤にする。
     ;; 別名は `__` で始めない(class の本体で名が private の形に書き換わり実行時に読めない)。綴りと置き場は展開の名の表から引く
     ;; (class の本体に書いた型の投影で、後処理が表の綴りの import を module の段へ移す — static_check._synthesized_import)。
     (import-form ["ClassVar"])
     '(import doeff_hy.wire)
     `(hy.R.doeff_hy/record.defrecord ~name ~@(if (is docstring None) [] [docstring]) ~record-header ~@fields)
     `(setattr ~name "__pydantic_config__" (doeff_hy.wire.wire-config ~(Dict (sum (lfor #(k v) (.items wire-names) [(String k) (String v)]) [])) ~unknown))
     `(setattr ~name "__doeff_wire__" (doeff_hy.wire.wire-shape ~name ~(Dict (sum (lfor #(k v) (.items wire-names) [(String k) (String v)]) [])) ~unknown))]
    :nested-reads True))


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
