# doeff-hy の code を読む面 — v8: カードの間隔と、上に留まる header

作成: 2026-09-29 00:5x〜01:0x。v7(`../v7/design.md`)への追加 2 点。v7 までの決まりはそのまま生きる。
出所: operator が実物(doeff-runner 0.6.30・画面 `~/experiments/hy-reading-plane/shots/u10-axes-conversation-input.png`)を見ての指示。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| **カードの間隔を詰める** | "great, can you reduce the spacing between cards?" |
| 読む面に**絞りを扱う header を置き、scroll しても上に留める(sticky)** | "the reader view should have a header that can set filter" → "i mean sticky header" |

## 2. カードの間隔(席の既定・数は戻せる)

畳んだ 1 行の列を一覧として読めるように、カードの外の余白と、頭の行・畳んだ行の内の余白を詰める。中身(v4 の畳んだ形・v5 のラベル・v6 の縦の表・v7 の帯)は変えない。

| 部品 | 前(0.6.32) | 後(0.6.33) |
|---|---|---|
| カードの下の余白(`.card` の margin) | 12px | 4px |
| 頭の行(`.hd` の padding) | 9px 16px | 5px 14px |
| 畳んだ 1 行(`.line` の padding) | 0 16px 9px | 0 14px 6px |

開いたカードの中の余白(signature・effects・doc・本体・帯)は触らない。

## 3. 上に留まる header

- header = 面の上の欄 2 行: 1 行目 = file 名(または repo 全体)・定義の数・`clear filter`。2 行目 = `show in line` の切り替えと `fold all` / `unfold all`。
- この 2 行を 1 つの箱(`.top`)に包み、`position: sticky; top: 0` で本文の scroll に追随させる。背景は面の背景と同じ色にし、カードは header の下へ潜る。
- 絞りの軸(kind・tags・effect・type・tests・location)と名の検索は左の欄にあり、左の欄は既に画面の高さに固定(sticky)されている。header に絞りの部品を複製しない — header から扱える絞りは `clear filter`(全部解除)で、値の選択は左の欄。
- 呼び出しの木(`#tree`)は header に含めず、本文と一緒に scroll する。

## 4. 戻し方

- 間隔: `ide-plugins/vscode/doeff-runner/src/read/render.ts` の `PAGE_STYLE` の `.card` / `.hd` / `.line` の値を上の表の「前」に戻す。
- header: 同じ file の `.top` の規則を消し、`.main` の padding を `14px 22px 40px` に戻す(HTML の `.top` の包みは残しても害はない)。
