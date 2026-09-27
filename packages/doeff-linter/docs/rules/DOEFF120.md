# DOEFF120: JsonValue Outside Wire Modules

`JsonValue`・`JSONValue`・`JsonObject`・`JSONObject` は、素の `dict | list | str | int | float | bool | None` を名で包んだだけの型です
(`JsonObject` は `dict[str, JsonValue]` で、同じ逃げ道)。中の形を何も約束しないので、使う側が手で分解して読むと、形の食い違いが
読む所ごとに違う形で漏れます。

この規則は、`architecture.hy` の在る repo で、JsonValue を使ってよい module を次の 2 つだけにします。

1. 汎用の解き手 — doeff-hy の `defwire` が使う `doeff_hy.wire` と、doeff-records の `doeff_records.wire`(module の綴りの末尾の段で照らす)。
2. `architecture.hy` の `:wire-modules` に挙げ、かつ foundation の層(`:foundation` の層の置き場)に在る、JSON の送受信そのものを行う module。

ほかの module(protocol・intent・core …)は、解き手が pydantic と同じ仕組み(TypeAdapter)で一度に形を確かめた、型のある値
(`defwire` の型・defrecord・frozen の dataclass)だけを見ます。

出自: agora-redesign #840・operator 2026-09-28 逐語 "and i dont think we should make anyone use that directry instead of actually parsing
and validating it like pydantic does"。

## 何を数えるか

- **Hy**: 読み取り器の記号で、最後の `.` の段が 4 つの名のどれかの物 — `(import m [JsonValue])`・`(setv JsonValue …)`・`(val JsonValue …)`・
  `#^ JsonValue x`・`(: x JsonValue)`・`(get dict #(str JsonValue))`・`wire.JsonValue` など。文字列・註・docstring・`#_` で読み捨てた form は数えない。
- **Python**: 字句の名(名前・属性の名・import の名)と、注釈(引数・戻り値・`x: T`)と型の別名(`X: TypeAlias = "…"`・`type X = …`)の
  文字列の中の語(前後が識別子の文字でない物)。docstring・註・f 文字列は数えない。
- 母集団は repo の Hy と Python の file の全部(`.` で始まる隠し dir・`node_modules`・`target`・`__pycache__`・`venv`・`site-packages` は降りない)。

## 違反の形

- module ごとに 1 件。鍵は `<path>::DOEFF120`(細目なし)。位置は最初の使用。
- 説明の主体に、使った名・数・最初の行・ほかの行を書く(例「module app.billing.core.decide — JsonValue を 2 か所で使う(最初 1 行目・ほかに 2 行目)」)。
- `:wire-modules` に当たるが foundation の層の外の module は許さず、説明に訳(「foundation の層(app/foundation/)に無いので許さない」)を書く。
- 重さは error。登録簿に載った既存の分は `[tool.doeff-linter.rules.DOEFF120] registered_severity`(既定 warning)。

## 許す場所の決め方

判定は `src/project/mod.rs` の `json_value_allowance` の 1 か所だけにあります。許す場所の決め方(例: defwire の macro が生む解き手の中だけを
許す形)を変える時は、この関数の中身だけを替えます。母集団・名の数え方・鍵・説明の形は変わりません。

## 設定

```hy
(defarchitecture agora-controllers
  :root "controllers"
  :layers [… (layer foundation …) …]
  :foundation foundation
  ;; JSON の送受信そのものを行う foundation の module(`.` 区切りの module の綴り・`*` は段の中の任意の綴り・`**` は 0 個以上の段)
  :wire-modules ["controllers.foundation.records_client" "controllers.foundation.http.*"])
```

- `:wire-modules` は path(`/`)ではなく module の綴りで書く。空の段・段の中に混ぜた `**`・同じ綴りの 2 度書きは設定の誤り。
- `:wire-modules` を書くには `:foundation` が要る(foundation の無い宣言では何も許せないので設定の誤り)。

```toml
[tool.doeff-linter]
enable = [ …, "DOEFF120"]
[tool.doeff-linter.registry]
dirs = ["scripts/doeff_lint/JSON-VALUE-BREACHES"]     # 既存の分(1 鍵 1 file)
[tool.doeff-linter.rules.DOEFF120]
registered_severity = "warning"
```

## 例

```hy
;; 悪い: app/billing/core/decide.hy — core が JSON を手で分解して読む
(import doeff_hy.wire [JsonValue])
(defk decide [#^ JsonValue payload] {:pre [] :post []}
  (get payload "amount"))

;; 良い: foundation の送受信の module が defwire の型へ parse し、core は型のある値だけを見る
(defk decide [#^ Charge charge] {:pre [] :post []}
  charge.amount)
```

```python
# 悪い: app/billing/protocol/shape.py
from typing import TypeAlias

JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"
```

## 戻し方

規則を外す(`enable` から DOEFF120 を抜く)。型に起こした値はそのままで害は無い。
