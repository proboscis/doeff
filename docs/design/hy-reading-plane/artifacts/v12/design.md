# doeff-hy の code を読む面 — v12: 実体の名はどこからでも押せば定義へ

作成: 2026-09-29 10:0x〜10:2x。v11(`../v11/design.md`)への追加 1 点。v11 までの決まりはそのまま生きる。

## 1. 裁定(operator・逐語)

| 決めたこと | 出所 |
|---|---|
| effect・class・record などの**実体の名を押せば定義へ飛べる** — 読む面からも source の箱からも、**plugin のあらゆる場所から** | "i want to be able to jump to definition by clicking each entities like effect/class/record etc from reading view and the source view... i mean every location of plugin" |

## 2. 今(0.6.38・実物で確かめた)

| 場所 | 押せるか |
|---|---|
| カードの帯の callers / callees / tests / types の名・呼び出しの木の節の名 | 押せる(`data-reveal` → そのカードへ) |
| カードの頭の args / return type / fields の型のチップ・effects のチップ・bases・methods・used by | 押せない(hover だけ) |
| 本体の文字の型・effect・呼びの名 | 押せない(色だけ) |
| source の箱の記号 | 押せない(色だけ) |
| text editor の `.hy` | 定義へ移動(definition provider)と hover はある |
| 左の欄の軸の値(effect・type の名) | 押すと絞り込み(定義へは行かない) |

材料は既にある: linter の editor-json は signature の型(TypeRef)・effect(EffectRef)・本体の呼びの segment に `definition: {path, range}` を
持ち(組み込みの型と解けない名は null)、索引(hy-index 版 6)は file ごとの `references` / `calls` の target(完全修飾名)を持つ。

## 3. 決まり

| 項目 | 決まり |
|---|---|
| 何が押せるか | **repo の中に定義がある名は全部**: defk / deff / defhandler / deftest / defrecord / defclass / defenum / defeffect / defwire / defn / 最上位の変数。組み込みの型(`str`・`int` …)と repo の外の名は押せない(見た目も link にしない) |
| どこで | カードの頭のチップ(型・effect・bases・methods・used by)・本体の文字(型・effect・呼び・name の役で定義に当たる物)・source の箱の記号・帯・木・hover の中の名。**読む面の中に実体の名が描かれる場所の全部**(新しく足す部品も同じ) |
| 押した時 | その定義のカードへ(同じ file なら scroll して開く・別の file なら v10 と同じ `revealElsewhere` でその file の面を開いてカードへ)。**Cmd / Ctrl を押しながら**なら text editor の定義の位置へ(open in editor と同じ) |
| 解き方 | 1. linter の `definition`(型・effect・呼び)→ 2. 索引の `references` / `calls` の target(source の箱の記号・name の役)→ 3. 名だけの時は索引の定義の名で引く(1 つに決まる時だけ。複数なら候補の一覧を quick pick)。面が自分で file を歩かない(v1 制約 5) |
| 見た目 | link の下線は出さず、hover で下線 + cursor pointer(今の帯の名と同じ)。押せない名は今の色のまま |
| text editor | 今の definition provider のまま。加えて hover の中の名も同じ規則で押せる(Markdown の command link) |
| 左の欄の軸の値 | 絞り込みのまま(定義へは行かない — 軸は集合の入口)。値の右に小さな `→` で定義へ、は作らない(2 つの意味を 1 つのチップに置かない) |

## 4. 実装の目安(戻せる)

- 面: 名を描く関数を 1 つにまとめる(`entityLink(name, resolved)` — resolved が無ければ素の span)。既にある `data-reveal` の受け側をそのまま使い、
  Cmd / Ctrl の判定を webview の click で読んで `type: 'reveal', editor: true` を送る。
- source の箱: U16 の色付けの token 列に、索引の `references` / `calls` の範囲を重ねて link にする(範囲は byte / 行と列で突き合わせ)。
- 検: 拡張の unit test に「型のチップ・effect のチップ・本体の呼び・source の箱の記号に data-reveal が付く / 組み込みの型には付かない」・
  「Cmd + click で editor の位置」・「複数の候補は quick pick」。

## 5. 戻し方

- `entityLink` を素の span に戻す 1 か所。帯と木の link は v7・U6 のまま残る。
