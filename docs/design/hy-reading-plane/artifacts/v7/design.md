# doeff-hy の code を読む面 — v7: source は editor と同じ highlighting・呼び出しの依存の木

作成: 2026-09-29 00:4x〜00:5x。v6(`../v6/design.md`)への追加 2 点。v6 までの決まりはそのまま生きる。
見本: `call-tree.html`(実物の messaging の入力の処理の呼び出しを木にしたもの・画面 = `call-tree-render.png`)。

## 1. 追加の裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| 読む面で source を出す時(v3 の `source` ボタン)、**editor と同じ semantic highlighting** で描く | "when showing the source in reading pane, it should have the same semantic highlighting enabled" |
| **呼び出しの依存の木**の可視化も欲しい | "it would be great if there's also call dependency tree visualization" |

## 2. source の highlighting

- カードの中に開く元の Hy の文字は、**editor の `.hy` と同じ字句の分けと同じ色**で描く。色の表を面の側に別に持たない。
- 字句の分け = editor が使っている物をそのまま使う(今は拡張の Hy の文法(TextMate)。拡張が semantic token の provider を持てば、それも同じ provider を使う)。
- 色 = VS Code の有効な theme の色(webview では `--vscode-*` の変数と theme の token の色)。theme を替えれば editor と面が同時に変わる。
- 面が自前で色を決めるのは、editor に無い部品(バッジ・チップ・帯・`⇐` の行の薄い背景)だけ。**本体の文字(v3 の val / var・型・呼び)も同じ theme の token の色に寄せる**(席の既定・戻せる)。
- 実装の目安: source の切り出しは v3 のまま索引の位置から。切り出した文字を editor と同じ tokenizer に通して token の列にし、webview へは「文字 + token の種類」で渡す。同じ入力 → editor と同じ token の列、を検で固定する。

## 3. 呼び出しの依存の木

- 材料 = hy-index 版 4(定義の完全修飾名・呼び出しの呼び先・呼び手の逆引き。#910 U7・着地済み)。面が自分で file を歩かない(v1 制約 5)のは変わらず。
- 入口 2 つ: (a) カードの関係の帯の `callers` / `callees` から「木で見る」。(b) 軸の欄の下の `call tree` の面(根の実体を名で選ぶ)。
- 木の形(席の既定・戻せる):

| 項目 | 決まり |
|---|---|
| 向き | `callees ↓`(根が呼ぶ物を下へ)と `callers ↑`(根を呼ぶ物を上へ)の切り替え。既定は callees |
| 節 | 実体の 1 行の畳んだ形(v4)と同じ: 種類のバッジ・名・(v4 の切り替えに従って)型・effect・tags。クリックで開閉、名をクリックでカードを面に積む |
| effect | 節に使う effect の絵を出し、木の根で「この木の下で使う effect の和」を 1 行に出す(呼び先を辿った推論が linter の `:effects` の突合と同じ物になる) |
| 深さ | 既定 3。`+` で 1 段ずつ広げる。同じ実体が 2 度出たら 2 度目は薄く `↺` を付けて開かない(循環と重複) |
| 絞り | 木の中でも軸で絞れる(例: role = judgment の節だけ濃く)。テストの節(deftest)は既定で隠し、切り替えで出す |
| 数 | 節の右に `callees N` / `callers N`。畳んだ節でも数は見える |
| 別 file | file を跨ぐという概念は無い(v1)。節の `location` は v4 の切り替えで出す |

- 木ではなく網(graph)が要る時(菱形の依存・循環の全体)は、今回は木の `↺` の印で示すだけ。網の描画は別の版で決める。

## 4. 戻し方

- highlighting: token の種類 → 色の対応を theme の変数から引く 1 か所を、固定の色の表に戻すだけ。
- 木: 面 1 つと帯のリンク 2 つを外すだけ。索引は変えない。
