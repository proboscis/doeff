# 盲検の前に固定した主張(設計者)

基準版 9df950b5・design.md と同時に書き、盲検 A・B の返答を読む前に sha256 を SHA256SUMS.txt に記録する。
以後この file は書き換えない(反例を受けた結論は counterexamples.md に書く)。

module id は design.md §3.1: `gate-full-stages` / `make-test-packages` / `make-test-rust` / `root-testpaths` /
`population-pin` / `land-mechanism`(外部)。

## S0 実弾の再現(axis: effects — テストの process の赤という外部の結果)

- 変化: root の pytest が赤(F3 と同じ)。
- 主張: `packages` と `rust` の処理ステージは実行され、台帳に処理ステージごとの結果が残る。宣言以外は何も変えない。
- 前提: 日次は `land.run_full_stages(stop_on_first_failure=False)` を使う(F5)。
- 予想の範囲: 変更なし(設計の効果そのもの)。変わる module = なし。変わらない = 全部。

## S1 日次の宿が zeus から別の機体へ移る(axis: hardware)

- 変化: `--node zeus` を別の node へ、または遠隔をやめて手元で走らせる。
- 主張: `gate-full-stages` の各処理ステージの前置きだけが変わる。Makefile の 2 target・testpaths・固定のテストは変わらない。
- 前提: 固定のテストは node 名・label を検めない(母集団の分け方だけを検める)。
- 予想の範囲: `gate-full-stages` のみ(4 つの run の前置き — 同じ file の中の繰り返し)。

## S2 保存の方式の変更(axis: storage)— 不適用

- 理由: 結果の保存(台帳の処理ステージごとの記録・log の置き場)は dotfiles の着地の道具が持ち、この設計は保存の形に触れない。doeff の側に保存の判断は無い。

## S3 新しい母集団が増える(axis: effects)

- 変化 (a): 新しい package `packages/doeff-x/tests/test_*.py` が足される。
  主張: どの宣言も編集せずに `packages` の処理ステージの母集団に入り、完全性のテストも緑のまま。
  予想の範囲: 変更なし。
- 変化 (b): `packages/doeff-agents/conformance/` を日次へ入れる。
  主張: `gate-full-stages` に処理ステージを 1 つ足す(+ 必要なら Makefile に target を 1 つ)と、`population-pin` の除外の表から該当の行を消すだけ。`make-test-packages`・`root-testpaths` は変わらない。
  予想の範囲: `gate-full-stages`・`population-pin`(除外の表と「母集団の命令」の一覧)・Makefile に新しい target。
- 変化 (c): 誰かが新しいテストを母集団の外(例: `packages/doeff-agents/scripts/test_x.py`)に置く。
  主張: 完全性のテストが赤になり、置き場を直すか除外の表に理由を書くまで緑にならない。

## S4 処理ステージを別々の機体へ分ける(axis: distribution)

- 変化: `rust` の処理ステージだけを別の node で走らせる。
- 主張: その処理ステージの run の前置きだけが変わる。各処理ステージが自分で木の同期と `make sync` をやり直すので、他の処理ステージの build に依存しない。
- 前提: 並列実行(同時に走らせる)は着地の道具の方策で、この設計の範囲外(`run_full_stages` は順に走らせる)。
- 予想の範囲: `gate-full-stages` のみ。

## S5 同じ作業 dir の取り合い・一部の処理ステージだけ手元へ倒れる(axis: concurrency)

- 変化: 処理ステージの合間に、同じ label の遠隔 dir が別の走行に使われる / `packages` の処理ステージだけが遠隔に届かず手元で走る。
- 主張: 各処理ステージは自分の木を同期し(または手元の木で)`make sync` からやり直すので、測るのは常に宣言した木。どの module も変えない。
- 前提: `remote_check` は呼ばれるたびに木を同期し、錠が取れなければ木ごとの一意の dir へ退避する(remote_check.py の註)。`make sync` は冪等。
- 予想の範囲: 変更なし。

## S6 決定的な模擬(axis: simulation)

- 変化: 母集団の振る舞い(全部を訪ねる・失敗を名指す・完全性)を実テストを走らせずに確かめたい。
- 主張: 偽の runner(`PACKAGE_UV_RUN`)で Makefile を実際に走らせれば、実 pytest を 1 本も起こさずに数秒で決定的に確かめられる。処理ステージの方策は `run_full_stages` に偽の命令を渡して確かめられる。
- 予想の範囲: 変更なし(試験の口は既存の `PACKAGE_UV_RUN`)。
