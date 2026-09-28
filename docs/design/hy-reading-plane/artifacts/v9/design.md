# doeff-hy の code を読む面 — v9: defclass のカードを defrecord と同じ扱いにする

作成: 2026-09-29 00:4x〜00:5x。v8(`../v8/design.md`)への追加 1 点。v8 までの決まりはそのまま生きる。
見本: `defclass-card.html`(画面 = `defclass-card-render.png`)。実物は agora-controllers の
`controllers/agora_sim/screen_invariants.hy` の PlacedVersion・LedgerVersion・HandedVersion・InputVersions。

## 1. 裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 0.6.32 の defclass のカード(畳んだ 1 行が欄の名だけ `(ref, text, request-id, sent-at, at)`・型も doc も無い)は足りない | "this is too simple"(画面つき) |
| 見本の案(defrecord と同じ扱い + decorator のバッジ + used by)で進める | "class viz lgtm" |

## 2. なぜ名だけだったか(実物で確かめた)

- source には全部ある: `(defclass [(dataclass :frozen True)] PlacedVersion [] "doc…" (#^ str ref) (#^ str text) (#^ (| str None) request-id) (#^ int sent-at) (#^ int at))`。
- 索引(doeff-indexer `hy_index/analyze.rs`)は **defrecord の欄の型は読む**(`record_def` が `fields.rs` の読み手で `#^ T x` を `param_types` に積む)のに、**defclass の欄は `class_member` が名だけ**で `Field` を積み、注釈の型を捨てる。decorator(`[(dataclass :frozen True)]`)は `skip_decorators` で読まずに飛ばす。
- 面(`entity.ts`)の欄のチップ `fieldChips` は `paramTypes` があればそれを使い、無ければ欄の名だけを出す — だから defclass だけ名になる。直す場所は索引の 1 か所で、面の欄の部品は既にある物が使える。

## 3. カードの形(見本の 2 節・3 節)

| 部品 | defclass の扱い |
|---|---|
| 頭 | 種類のバッジ `defclass`・名・**decorator のバッジ**(`dataclass :frozen True` → `frozen dataclass`。`dataclass` だけなら `dataclass`。他の decorator は書かれたとおりの綴り)・source / open in editor |
| 畳んだ 1 行 | `(ref: str, text: str, request-id: str \| None, sent-at: int, at: int)` — defrecord と同じ名: 型の形(show in line の args / return type に従う)。doc の 1 行目(doc (first line) に従う) |
| 開いた形の fields | 名と型のチップ。**v6 の閾**(欄 ≥ 4 か型の文字 > 60)で縦の表 |
| doc | 全文 |
| bases / methods | ある時だけ(今の既定の欄のまま) |
| **used by** | この型を**引数に取る**定義(arg of)・**返す**定義(returns)・**欄に持つ**型(field of)・**作っている**定義(made in — 索引の呼び出しで、この名を呼ぶ defk / deff)。材料 = linter の signature(引数と答えの型)+ 索引の欄の型 + 索引の呼び出し。型の軸(v1 の表・U10)と同じ材料で、カードの側から見た形 |
| 帯 | callers / callees / tests / types / location(v7・U6 のまま) |

- 型のない欄(`(setv x …)` だけ・注釈なし)は名だけ(defrecord と同じ)。
- defclass が dataclass でない(method が主の class)時も同じ欄の並びで、fields が無ければ fields の欄を出さない。

## 4. 索引の変更(実装側の目安・戻せる)

- `class_member` の欄の読みを defrecord と同じ読み手(`fields::record_field_targets` 相当)に通し、注釈の型を `param_types` に積む(defrecord と同じ欄に載せるので契約の版は上げない — `param_types` は版 5 で既にある)。
- decorator は定義に `decorators: [string]`(書かれたとおりの綴り)を足す。これは**契約への欄の追加**なので版を 6 に上げ、拡張の `contract.ts` を同時に直す(版の突合は bundle-indexer の検が守る)。
- 検: 索引のテストに `(defclass [(dataclass :frozen True)] PlacedVersion [] "doc" (#^ str ref) (#^ (| str None) request-id))` を足し、`param_types` に `ref: str`・`request-id: str | None`、`decorators` に `dataclass :frozen True` が積まれること。拡張の unit test に defclass のカードの畳んだ 1 行と縦の表・バッジ・used by。

## 5. 戻し方

- 面: `entity.ts` の defclass の枝を今の既定(名だけ)に戻す。索引の `param_types` は defrecord と同じ欄なので残しても害はない。
- 索引の decorator の欄を外すなら契約の版を戻し、`contract.ts` の欄も同時に外す。
