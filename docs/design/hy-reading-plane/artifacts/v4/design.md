# doeff-hy の code を読む面 — v4: カードを 1 行に畳み、1 行に出す物を切り替える

作成: 2026-09-28 23:5x。v3(`../v3/design.md`)への追加 1 点。v3 までの決まりはそのまま生きる。
見本: `entity.html`(v3 の見本に畳んだ形と切り替えの欄を足したもの。run-input-request だけ開き、他は畳んである・画面 = `entity-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| カードを **1 行の形に畳める**。1 行に**何を出すかを切り替えられる**ので、多くの実体を一目で見渡せる | "in plugin view. i want to be able to fold a card to oneline form where i can toggle what to show so i can glance at" |
| 前提: v3 の本体の文字の形(val / var・when・match)は承認済み | "yeah val var when match is perfect." |

## 2. 畳んだ形(1 行)

- 常に出す: 種類のバッジ・名・開閉のボタン(▸ / ▾)。
- 切り替えて出す(全カード共通の設定・面の上の欄で切り替える):

| 欄 | 1 行での見せ方 | 既定 |
|---|---|---|
| 引数と答えの型 | `(request: InputRequest) → RunOutcome`(defrecord は欄の一覧・defhandler は解く effect) | 出す |
| effect | 絵 + 名(使う effect) | 出す |
| tags | 小さなチップ(key ごと) | 出す |
| 説明の 1 行目 | 先頭の 1 文を省略記号で切る | 出さない |
| 関係の数 | 呼び手 N · テスト N(実体の種類ごとの関係) | 出さない |
| 置き場 | file:行(右端) | 出さない |

- 「全部畳む」「全部開く」で面の全カードをまとめて切り替える。1 枚ずつは頭のボタン。
- 畳んだ状態と 1 行の設定は面が覚える(VS Code の状態として。開き直しても同じ)。
- 軸の切り替えで出たカードの一覧は、既定で畳んだ形にする(一目で見渡すため)。開いたカードは「積んだカード」として上に残す。

## 3. 戻し方

- 畳む機能は部品 1 つ。切り替えの欄は設定 1 つ。どちらも外せば v3 の形に戻る。
