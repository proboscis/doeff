;;; defwire の実行時 — JSON の値と、型を宣言した値(defwire の型)を行き来する汎用の解き手(agora-redesign #840)。
;;;
;;; 出自 = operator 2026-09-28(逐語 4 つ): "I realized JSONValue is being used" / "JsonValue is in wire.hy," /
;;; "and i dont think we should make anyone use that directry instead of actually parsing and validating it like pydantic does" /
;;; "hmm, cant we have some def* macro for this?" → 案 A(別の macro defwire)に "A"。
;;;
;;; 何をするか: JSON は、型を宣言した値に pydantic と同じ仕組み(TypeAdapter)で一度に解いて確かめる。JSON の値(JsonValue)に
;;; 触ってよいのは、この module の parse / dump と、送受信そのものを行う foundation の module だけ(doeff-linter DOEFF120)。
;;; protocol・intent・core は型のある値だけを見る。JsonValue を手で分解して読む関数は書かない。
;;;
;;;   (<- row (parse LandingRow raw))       ; 解いて確かめる — 答え = LandingRow の値か Malformed
;;;   (<- row (parse-json LandingRow text)) ; JSON の文字列(bytes も)から
;;;   (<- raw (dump row))                   ; JSON の値へ(送り出す foundation だけ)
;;;   (<- schema (json-schema LandingRow))  ; JSON Schema(契約の file と照らす時)
;;;
;;; 形を呼び手が決める任意の JSON(tool の引数と結果・耐久の走行の memo の値)は、JsonValue ではなく OpaqueJson(doeff_hy.json_value)
;;; で運ぶ — 中を分解する口を持たない名のある型。defwire の欄に書けば解き手が包み・戻す。形を知る読み手は (parse T opaque) で読む。
;;;
;;; 解き方(defwire の展開がこの module の wire-config と wire-shape を呼んで型ごとに 1 度だけ組む):
;;;   * 型の検めは厳しい(pydantic の strict・JSON の読み方): 文字列を数にしない・真偽を数にしない・整数は float の欄に入る・
;;;     配列は tuple の欄に入る・defenum の欄は値の綴りで読む。
;;;   * 欄の名は defwire の :names が決めた wire の名だけを受ける(Python の欄の名では受けない)。
;;;   * 知らない欄は :unknown :reject で断り、:ignore で読み捨てる。
;;;   * defrecord の :check は値を作る時に走り、落ちたら Malformed になる。
;;;   * 入れ子の欄の型も defwire の型にする(素の defrecord は自分の wire の形を持たないので、欄の名の写しと厳しさが効かない)。
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
(require doeff-hy.macros [defk deff <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import json)
(import typing [ClassVar Protocol runtime-checkable])
(import pydantic [ConfigDict TypeAdapter ValidationError])
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_hy.json_value [JsonValue OpaqueJson])

;; 知らない欄の扱いの綴り(defwire の :unknown の値)→ pydantic の extra。
(setv UNKNOWN-FIELDS {"reject" "forbid" "ignore" "ignore"})


(defrecord MalformedField
  "形の合わない所 1 つ: field = 欄の場所(wire の名・入れ子と配列の位置は . でつなぐ・値の全体は空の文字列)/ reason = なぜ
   (pydantic の文と誤りの種類)。"
  {:tags {:context "wire" :role "type"}}
  (#^ str field)
  (#^ str reason))


(defrecord Malformed
  "外から来た JSON が型の約束の形でない — 5 つ目の失敗の種類(ADR-DOE-CORE-EFFECTS-003 R17): wire-type = 解こうとした型の名 /
   fields = 合わない所(1 つ以上・pydantic が見つけた順)。"
  {:tags {:context "wire" :role "type"}
   :check [(> (len fields) 0)]}
  (#^ str wire-type)
  (#^ (get tuple #(MalformedField ...)) fields))


(defrecord WireShape
  "defwire の型 1 つの wire の形: names = Python の欄の名 → wire の欄の名(欄を全部・名で引く索引)/ unknown = 知らない欄の扱い
   (\"reject\" / \"ignore\")/ adapter = その型の解き手(pydantic の TypeAdapter — 型を定義した時に組む。後で定義する型を欄に持つ型だけは最初に使う時)。"
  {:tags {:context "wire" :role "type"}
   :check [(in unknown UNKNOWN-FIELDS)]}
  (#^ (get dict #(str str)) names)
  (#^ str unknown)
  (#^ TypeAdapter adapter))


(defclass [runtime-checkable] WireValue [Protocol]
  "defwire で建てた型の値(型が __doeff_wire__ に WireShape を持つ)— dump の受ける物。"
  (#^ (get ClassVar WireShape) __doeff-wire__))


(deff wire-config [names unknown]  ; defk にできない: defwire の展開が module を読む時に呼ぶ(Program を実行できない所)
  {:pre [(: names dict) (: unknown str) (in unknown UNKNOWN-FIELDS)]
   :post [(: % dict)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の pydantic の設定(型の __pydantic_config__ に置く): 欄の名は names の写しだけを受けて書き、知らない欄は unknown に
   従い、型の検めは厳しい。解き手は型を定義した時(module の読み込み)に組む — 最初の parse / dump の呼び手(検の実行・本番の最初の
   要求)が組み立ての費用を払わないため(agora-redesign #2420・前例 #2327)。欄にまだ定義していない型(後で定義する型)を書いた型だけは
   その時に組めないので、pydantic の既定どおり最初に使う時に組む(defer-build を置かない = 組めれば組み、組めなければ後へ回す)。"
  (ConfigDict :alias-generator (. names __getitem__)
              :validate-by-alias True
              :validate-by-name False
              :serialize-by-alias True
              :extra (get UNKNOWN-FIELDS unknown)
              :strict True))


(deff wire-shape [wire-type names unknown]  ; defk にできない: defwire の展開が module を読む時に呼ぶ(Program を実行できない所)
  {:pre [(: wire-type type) (: names dict) (: unknown str)]
   :post [(: % WireShape)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の wire の形(型の __doeff_wire__ に置く)。wire-config を __pydantic_config__ に置いた後に呼ぶ — TypeAdapter はその設定を読む。"
  (WireShape :names names :unknown unknown :adapter (TypeAdapter wire-type)))


(defk malformed-of [wire-type error]
  {:pre [(: wire-type type) (: error ValidationError)]
   :post [(: % Malformed)]
   :tags {:context "wire" :role "judgment"}}
  "pydantic の検めの誤りを Malformed へ写す(欄の場所は wire の名・配列の位置を . でつなぐ)。"
  (Malformed :wire-type wire-type.__name__
             :fields (tuple (gfor problem (.errors error :include-url False)
                                  (MalformedField :field (.join "." (gfor part (get problem "loc") (str part)))
                                                  :reason (.format "{} [{}]" (get problem "msg") (get problem "type")))))))


(defk parse [wire-type raw]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: raw (| JsonValue FrozenMap tuple OpaqueJson))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の値(凍らせた JSON — FrozenMap・tuple — と、中を読まずに運んだ OpaqueJson も)を defwire の型 wire-type の値へ解いて
   確かめる。答え = wire-type の値か、形が合わない時は Malformed(どの型の・どの欄が・なぜ)。JSON の読み方で検めるので、配列は
   tuple の欄に・値の綴りは defenum の欄に入る。OpaqueJson は形を知る読み手が中を読む唯一の口(json_value.py の OpaqueJson)。"
  (<- parsed (parse-json wire-type (if (isinstance raw OpaqueJson) raw.text (json.dumps (thaw-json raw) :ensure-ascii False))))
  parsed)


(defk parse-json [wire-type text]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: text (| str bytes))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "JSON の文字列(bytes も)を defwire の型 wire-type の値へ解いて確かめる。JSON として読めない文字列も Malformed(欄は空の文字列)。"
  (try
    (.validate-json (. wire-type __doeff_wire__ adapter) text)
    (except [error ValidationError]
      (<- malformed Malformed (malformed-of wire-type error))
      malformed)))


(defk dump [value]
  {:pre [(: value WireValue)]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の値を JSON の値へ(欄は wire の名・tuple は配列・defenum は値の綴り・既定値と同じ欄は書かない — 入れ子の型の欄も)。
   送り出す foundation だけが呼ぶ。"
  (.dump-python (. (type value) __doeff_wire__ adapter) value :mode "json" :by-alias True :exclude-defaults True))


(defk dump-json [value]
  {:pre [(: value WireValue)]
   :post [(: % str)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の値を JSON の文字列へ(dump と同じ形 — 既定値と同じ欄は書かない)。"
  (.decode (.dump-json (. (type value) __doeff_wire__ adapter) value :by-alias True :exclude-defaults True) "utf-8"))


(defk json-schema [wire-type]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__")]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "defwire の型の JSON Schema(欄は wire の名・:unknown :reject の型は additionalProperties false)— 契約の file と照らす時に使う。"
  (.json-schema (. wire-type __doeff_wire__ adapter) :by-alias True))
