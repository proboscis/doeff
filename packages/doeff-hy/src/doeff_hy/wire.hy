;;; defwire の実行時 — JSON の値と、型を宣言した値(defwire の型)を行き来する汎用の解き手(agora-redesign #840)。
;;;
;;; 出自 = operator の決定 2026-09-28: JsonValue を直に使わせず、pydantic のように解いて確かめる。そのための def* の macro を置く
;;; 案 A(別の macro defwire)を採る。
;;;
;;; 何をするか: JSON は、型を宣言した値に doeff の自前の解き手(doeff_hy/wire_codec.py の型の木)で一度に解いて確かめる。この module は
;;; pydantic を import しない(決定 2026-10-27・card ki-b7701d5fc0dd で pydantic の TypeAdapter から替えた。pydantic は照らしの相手として
;;; だけ残し、照らしは doeff_hy/wire_parity.py と packages/doeff-hy/tests/test_wire_parity.py が持つ)。JSON の値(JsonValue)に
;;; 触ってよいのは、この module の parse / dump と、送受信そのものを行う foundation の module だけ(doeff-linter DOEFF120)。
;;; protocol・intent・core は型のある値だけを見る。JsonValue を手で分解して読む関数は書かない。
;;;
;;;   (<- row (parse LandingRow raw))       ; 解いて確かめる — 答え = LandingRow の値か Malformed
;;;   (<- row (parse-json LandingRow text)) ; JSON の文字列(bytes も)から
;;;   (<- m (parse-as (get dict #(str OpaqueJson)) raw))  ; defwire でない入れ物の型へ(答え = その型の値か Malformed)
;;;   (<- m (parse-json-as (get list str) text))           ; 同じく JSON の文字列から
;;;   (<- raw (dump row))                   ; JSON の値へ(送り出す foundation だけ)
;;;   (<- schema (json-schema LandingRow))  ; JSON Schema(契約の file と照らす時)
;;;
;;; 形を呼び手が決める任意の JSON(tool の引数と結果・耐久の走行の memo の値)は、JsonValue ではなく OpaqueJson(doeff_hy.json_value)
;;; で運ぶ — 中を分解する口を持たない名のある型。defwire の欄に書けば解き手が包み・戻す。形を知る読み手は (parse T opaque) で読む。
;;;
;;; 解き方(defwire の展開がこの module の wire-shape を呼んで型に wire の形を置き、型の木は最初の使いで 1 度だけ組む — 設計の記録
;;; docs/design/defwire-decoder/design.md):
;;;   * 型の検めは厳しい(JSON の読み方): 文字列を数にしない・真偽を数にしない・整数は float の欄に入る・
;;;     配列は tuple の欄に入る・defenum の欄は値の綴りで読む。
;;;   * 欄の名は defwire の :names が決めた wire の名だけを受ける(Python の欄の名では受けない)。
;;;   * 知らない欄は :unknown :reject で断り、:ignore で読み捨てる。
;;;   * defrecord の :check は値を作る時に走り、落ちたら Malformed になる。
;;;   * 入れ子の欄の型も defwire の型にする(素の defrecord は自分の wire の形を持たないので、木を組む時に断る)。
;;;
;;; 書き方(dump / dump-json): 既定値の在る欄は「省ける欄」(JSON Schema でも required に入らない)。値が既定値と同じ欄は書かない
;;; (入れ子の defwire の型の欄も同じ)— `(setv #^ (| str None) x None)` の None は「欄が無い」で、null を書かない。読み(parse)は
;;; 無い欄を既定値で埋めるので、dump と parse は往復する。既定値の無い欄は None でも書く(null)。
;;; 表の行の全体の像のように「書かない欄 = 消す」が要る書き手は、型の欄の wire の名を全部 None で並べた上に dump を重ねる
;;; (欄の wire の名は型の __doeff_wire__ の names)。出自 = agora-redesign #840(画面の設定の行の入れ子の agent の宣言 — 契約は
;;; 任意の欄を null でなく欄の無いことで表す)。
;;;
;;; 形の違う JSON の答え = Malformed(5 つ目の失敗の種類 — 外の世界が約束と違う物を返した。相手の版の食い違いは運用で起きうる
;;; ので、実装の誤りの例外ではなく業務の失敗として扱う)。Absent / Raise の段階 2 が本線に入るまでは parse は Malformed を値で
;;; 返し(答えの型 = (| T Malformed))、段階 3 の切り替えで Raise(Malformed) に寄せる(ADR-DOE-HY-007 R8・ADR-DOE-CORE-EFFECTS-003 R17)。
;;;
;;; 型の解き手は WireShape の codec(doeff_hy.wire_codec の Codec)。前の pydantic の TypeAdapter(WireShape の adapter)は消した — 直に
;;; 読んでいた所は codec か上の 5 つの API へ移す。defwire の型は今までどおり __pydantic_config__(wire-config — pydantic を読まずに作る
;;; 設定の辞書)を持つ: 消すと、pydantic の TypeAdapter で defwire の型を直に解く読み手(agora-controllers の約 20 file)が黙って Python の
;;; 欄の名と緩い検めで読むようになるため。その読み手を wire の API へ移す card ki-7c4609cd96c9 が、展開の setattr と一緒に消す。
(require doeff-hy.macros [defk deff val <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import types)
(import typing [ClassVar Protocol runtime-checkable])
(import doeff_hy.frozen [FrozenMap])
(import doeff_hy.json_value [JsonValue OpaqueJson])
(import doeff_hy.wire_codec [codec-of])
;; Malformed と MalformedField は pydantic を読まない置き場に在る(自前の解き手 wire_codec.py も同じ型を返す — card ki-923c16d33551)。
(import doeff_hy.wire_malformed [Malformed MalformedField])

;; 知らない欄の扱いの綴り(defwire の :unknown の値)→ pydantic の extra(__pydantic_config__ だけが読む)。
(setv UNKNOWN-FIELDS {"reject" "forbid" "ignore" "ignore"})

;; parse-as が受ける語彙の型の綴りの形: 素の型・defenum・defwire の型(type)・(get dict #(str T)) などの総称の型(GenericAlias)・
;; (| T None) などの union(UnionType)。
(setv SHAPE-FORMS (| type types.GenericAlias types.UnionType))


(defrecord WireShape
  "defwire の型 1 つの wire の形: names = Python の欄の名 → wire の欄の名(欄を全部・名で引く索引)/ unknown = 知らない欄の扱い
   (\"reject\" / \"ignore\")/ wire-type = その型。解き手(型の木)は codec が最初に使う時に 1 度だけ組んで型に覚える。"
  {:tags {:context "wire" :role "type"}
   :check [(in unknown UNKNOWN-FIELDS)]}
  (#^ (get dict #(str str)) names)
  (#^ str unknown)
  (#^ type wire-type)

  (defn [property] codec [self]
    "その型の解き手(doeff_hy.wire_codec の Codec — 最初の使いで型の木を 1 度組み、2 回目からは組まない)。"
    (codec-of self.wire-type)))


(defclass [runtime-checkable] WireValue [Protocol]
  "defwire で建てた型の値(型が __doeff_wire__ に WireShape を持つ)— dump の受ける物。"
  (#^ (get ClassVar WireShape) __doeff-wire__))


(deff wire-config [names unknown]  ; defk にできない: defwire の展開が module を読む時に呼ぶ(Program を実行できない所)
  {:pre [(: names dict) (: unknown str) (in unknown UNKNOWN-FIELDS)]
   :post [(: % dict)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の __pydantic_config__(pydantic の TypeAdapter で defwire の型を直に解く読み手が、wire の名と厳しさで解くための設定 —
   pydantic の ConfigDict と同じ鍵の素の辞書で、pydantic を読まずに作る。照らしの道具 wire_parity.py もこの設定で pydantic の解き手を組む)。
   この module の API は使わない。card ki-7c4609cd96c9 で読み手を wire の API へ移した後に、defwire の展開の setattr と一緒に消す。"
  {"alias_generator" (. names __getitem__)
   "validate_by_alias" True
   "validate_by_name" False
   "serialize_by_alias" True
   "extra" (get UNKNOWN-FIELDS unknown)
   "strict" True
   "defer_build" True})


(deff wire-shape [wire-type names unknown]  ; defk にできない: defwire の展開が module を読む時に呼ぶ(Program を実行できない所)
  {:pre [(: wire-type type) (: names dict) (: unknown str)]
   :post [(: % WireShape)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の wire の形(型の __doeff_wire__ に置く)。解き手はここでは組まない — 最初の parse / dump / json-schema で 1 度だけ組む
   (使わない型まで組む費用を、module を読み込む process ごとに払わない — card acp:kanban-issue:ki-8b518c8f8e9d)。"
  (WireShape :names names :unknown unknown :wire-type wire-type))


(defk parse [wire-type raw]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: raw (| JsonValue FrozenMap tuple OpaqueJson))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の値(凍らせた JSON — FrozenMap・tuple — と、中を読まずに運んだ OpaqueJson も)を defwire の型 wire-type の値へ解いて
   確かめる。答え = wire-type の値か、形が合わない時は Malformed(どの型の・どの欄が・なぜ)。値を文字列へ書き直さずに直に木へ通し、
   答えは同じ値を JSON の文字列にして parse-json に渡した時と同じ(配列は tuple の欄に・値の綴りは defenum の欄に入る)。OpaqueJson は
   形を知る読み手が中を読む唯一の口(json_value.py の OpaqueJson)。"
  (.decode (. wire-type __doeff_wire__ codec) raw))


(defk parse-json [wire-type text]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: text (| str bytes))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の文字列(bytes も)を defwire の型 wire-type の値へ解いて確かめる。JSON として読めない文字列も Malformed(欄は空の文字列)。"
  (.decode-json (. wire-type __doeff_wire__ codec) text))


(defk parse-as [shape raw]
  {:pre [(: shape SHAPE-FORMS) (: raw (| JsonValue FrozenMap tuple OpaqueJson))]
   :post [(: % (| JsonValue tuple frozenset OpaqueJson WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の値を、defwire でない語彙の型 shape(入れ物 — (get dict #(str object))・(get list str)・(| str None)・
   (get dict #(str OpaqueJson))・(get tuple #(SomeWire ...)) など。defwire の型も受ける)の値へ解いて確かめる。答え = shape の値か
   Malformed(型の名は shape の綴り)。型の木と厳しさは parse と同じ(文字列を数にしない・真偽を数にしない・配列は tuple の欄に入る)。
   出自 = card ki-7c4609cd96c9(pydantic の TypeAdapter で入れ物を直に解いていた読み手を wire の API へ移す)。"
  (.decode (codec-of shape) raw))


(defk parse-json-as [shape text]
  {:pre [(: shape SHAPE-FORMS) (: text (| str bytes))]
   :post [(: % (| JsonValue tuple frozenset OpaqueJson WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の文字列(bytes も)を語彙の型 shape の値へ解いて確かめる(parse-as の文字列の版)。JSON として読めない文字列も Malformed。"
  (.decode-json (codec-of shape) text))


(defk dump [value]
  {:pre [(: value WireValue)]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の値を JSON の値へ(欄は wire の名・tuple は配列・defenum は値の綴り・既定値と同じ欄は書かない — 入れ子の型の欄も)。
   送り出す foundation だけが呼ぶ。"
  (.encode (. (type value) __doeff_wire__ codec) value))


(defk dump-json [value]
  {:pre [(: value WireValue)]
   :post [(: % str)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の値を JSON の文字列へ(dump と同じ形 — 既定値と同じ欄は書かない)。OpaqueJson の欄は、持っている JSON の文字列を
   解き直さずにそのまま出力へ埋める(文字列 → json.loads → もう一度書く、の二度手間をしない。出力の JSON としての意味は dump と同じ)。"
  (.encode-json (. (type value) __doeff_wire__ codec) value))


(defk json-schema [wire-type]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__")]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の JSON Schema(欄は wire の名・:unknown :reject の型は additionalProperties false)— 契約の file と照らす時に使う。"
  (dict (.schema (. wire-type __doeff_wire__ codec))))
