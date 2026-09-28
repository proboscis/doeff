# doeff-hy の code を読む面 — v6: 引数が多い・型が長い時の args / return type の見せ方

作成: 2026-09-29 00:2x。v5(`../v5/design.md`)への追加 1 点。v5 までの決まりはそのまま生きる。
見本: `sig-variants.html`(実物の `controllers/screen/runtime/react.hy:182` の `view-body` を 5 つの形で描いたもの・画面 = `sig-variants-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 実物の view-body(引数 6・union 4 つ)の今の形は「正しいが見やすくない」 | "hmm this is correct but not good to see."(画面つき) |
| 席の推奨(案 3 + 閾での切り替え)を採る | "as you recommend" |

## 2. 決まり

### 2.1 開いたカードの args / return type

| 条件 | 形 |
|---|---|
| 引数が 3 つ以下で、型の文字が短い | 今の 1 行の形: 引数のチップ → 矢印 → return type のチップ(v2 のまま) |
| **引数が 4 つ以上、または型の文字の合計が長い**(閾は席の既定: 引数 ≥ 4、または引数と return type の型の文字の合計 > 60 字) | **案 3 = 縦の表**: 左の列に名、右の列に型。union の候補は 1 つずつ小さなチップに、`\|` は薄く、`None` は破線のチップで弱く。**return type は最後の行に緑の帯と `return type` のラベル** |

- 切り替えは自動(閾で判じる)。手で切り替える操作は付けない(戻せる)。
- 型の候補のチップは、defrecord などの実体ならクリックでそのカードへ(v2 の関係の帯と同じ)。

### 2.2 畳んだ 1 行の args / return type

| 条件 | 形 |
|---|---|
| 引数が 3 つ以下 | `(request: InputRequest) → RunOutcome`(v4 のまま) |
| 引数が 4 つ以上 | **名だけ** `(topic, rows, cache, previous, record, evaluation) → QueueSent \| ConversationSent \| None`。型は hover で。return type は常に出す |

### 2.3 退けた案(見本に残す)

- 案 2(縦の表・union は文字のまま): 案 3 より候補の切れ目が弱い。
- 案 4(2 列の格子): return type は分かれるが union は詰まったまま。
- 案 5(`→` を行頭に): 最小の直しだが 3 行目に埋もれるのは変わらない。

## 3. 戻し方

- 閾(引数 ≥ 4・型の文字 > 60)は数 2 つ。縦の表そのものを外せば v2 の 1 行の形に戻る。
