# doeff-hy の code を読む面 — v5: UI のラベルは args / return type などの英語の技術用語

作成: 2026-09-29 00:0x。v4(`../v4/design.md`)への追加 1 点。v4 までの決まりはそのまま生きる。
見本: `entity.html`(v4 の見本のラベルを差し替えたもの・画面 = `entity-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| ラベルは「引数 / 答え」ではなく **args / return type** | "instead of 引数/答え use args / return type" |

## 2. ラベルの表(面に出る文字。設計の文書の日本語は変えない)

operator が決めたのは args / return type の 2 つ。残りは席が同じ流儀(一般に通じる英語の技術用語・小文字)で揃えた既定で、戻せる。

| 場所 | 旧 | 新 |
|---|---|---|
| 切り替えの欄 | 1 行に出す物 / 引数と答えの型 / effect / tags / 説明の 1 行目 / 呼び手・テストの数 / 置き場 | show in line / **args / return type** / effects / tags / doc (first line) / callers / tests / location |
| 切り替えの欄のボタン | 全部畳む / 全部開く | fold all / unfold all |
| カードの頭のボタン | source / editor で開く | source / open in editor |
| カードの欄 | 使う effect / 解く effect / 解く: | effects / handles / handled by: |
| カードの関係の帯(defk) | 呼び手 / 呼び先 / テスト / 使う型 | callers / callees / tests / types |
| カードの関係の帯(defeffect) | 使う定義 / 解く handler | used by / handlers |
| カードの関係の帯(defrecord) | 返す定義 / 受ける定義 | returned by / accepted by |
| カードの関係の帯(defhandler) | 被せる場所 | installed at |
| 左の軸 | 軸: context / role / effect / 型 / テスト / 置き場 | axis: context / role / effect / type / tests / location |
| 軸の値 | テストあり / テストなし | has tests / no tests |

- 実体の種類のバッジ(defk・defeffect …)と tags の key(context・role)は source の語のまま。
- 説明(docstring)と本体は source のまま(日本語)。

## 3. 戻し方

- ラベルは 1 か所の表(実装では文字列の表 1 つ)。戻すのはその表だけ。
