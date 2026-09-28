# doeff-hy の code を読む面 — v10: 違反(linter)の一覧の項目から読む面 + source へ

作成: 2026-09-29 00:5x〜01:0x。v9(`../v9/design.md`)への追加 1 点。v9 までの決まりはそのまま生きる。

## 1. 裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 違反(linter)の一覧の項目を押したら、その `.hy` の読む面を、source つきで開く | "clicking linter issue browser's item must open the corresponding hy files' reading view with source" |

## 2. 今の動き(実物で確かめた・doeff-runner 0.6.34)

- 一覧 = view `doeff-lint-violations`(「違反(linter)」)。違反 1 つの項目の command は `src/lint/panel.ts` の `openViolation` = `vscode.open`(違反の範囲を選択して **text editor** を開く)。
- 読む面には既に 2 つの口がある: `PlaneNavigator.revealElsewhere(filePath, { qualifiedName, line })`(0.6.34 の「file:line からカードの該当の行へ」— その file を読む面で開き、行を含む定義のカードへ scroll)と、webview の `card` の message の `showSource`(カードの source の箱を開く)。違反はカードの本体の行にも既に出る(U5)。

## 3. 決まり

| 項目 | 決まり |
|---|---|
| 押す物 | 違反(linter)の一覧の**違反 1 つの項目**(規則・file の見出しの項目は今どおり開閉) |
| 開く物 | その違反の file の**読む面**(`.hy` を開いた時の既定と同じ custom editor)。開いていれば前に出す |
| カード | 違反の行を含む定義のカードを**開いた形**にし、**source の箱も開く**。本体の該当の行(`data-src-line`)と source の該当の行へ scroll(中央)。本体に無い行(頭・契約)ならカードの頭へ |
| 定義に属さない行 | module の最上位(定義の外)の違反は、その file の面を開いて先頭(カードは選ばない)。message で「定義の外の行」と 1 行 |
| 読む面を設定で切っている時 | 今までどおり `vscode.open`(editor) |
| text editor へ | 読む面のカードの `open in editor`(v5) |
| 層の地図(linter)の項目 | 変えない(file と層の項目 — 定義 1 つを指さない) |

## 4. 実装の目安(戻せる)

- `RevealTarget` に `showSource: boolean` を足し、`PlanePanel.reveal` が `card` の message に `showSource` を載せる(webview の受け側は既にある)。
- `src/lint/panel.ts` の `openViolation` を、読む面が有効なら拡張の内部の command(例 `doeff-runner.read.revealViolation` — 引数 = path・行)に付け替える。定義の解決は 0.6.34 の `locate(parseLocation(...))` をそのまま使う(file を歩かない — v1 制約 5)。
- 検: 拡張の unit test に「違反の path:行 → 定義のカードの id と行、showSource が真」・「定義の外の行 → 面の先頭」、`lint/panel.ts` の項目の command が読む面の command であること(設定で切った時は `vscode.open`)。

## 5. 戻し方

- `openViolation` を `vscode.open` に戻す(1 関数)。`RevealTarget.showSource` は残しても害はない。
