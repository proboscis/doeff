(設計者が予備の評価 0 に渡した依頼文。2026-10-12・版 ec89495d4。作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-ki-b7c4`。)

あなたは独立の評価者です。日本語で答えてください。次の「主張」を崩す具体例(失敗ケース)を、実測で探してください。見つからなければ「見つからなかった」と、どこまで試したかを書いてください。

場所: packages/doeff-traverse/tests/test_examples_run.py (例の検) と run_example_mutated_expansion.py (子の入口)・examples/ の例・
packages/doeff-hy/src/doeff_hy/macros.hy(traverse・for/do の展開)。走らせ方 `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q --tb=short -rf`。
git の checkout / reset は hook に止められるので、変更は file を書き戻して戻す。作業木の外は触らない。

背景: examples/ の例は doeff-hy の macro を使う読み物で、macro の持ち主は答えを変えずに展開を作り替えることがある。そのたびに例や例の検を赤にしてはいけない。
例の検は、(1) 例が走り標準出力が期待に合う (2) 期待の出力に展開の形の印を置かない (3) 今回足した物: 例を、macro の展開を答えを変えない形に変えた状態(変異)でも走らせ、出力が期待のままである事を求める。

主張: R1 macro が作った値の形(class 名・型・属性)を実行時に印字する例を足すと、例の検がその例を名指して赤になる。R2 答えを変えない doeff-hy の macro の変更では、今の例も例の検も変わらず緑のまま。
R3 今の例 9 本と期待の出力を書き換えずに緑のまま。R4 変異は、例が import する Hy の lib の展開と、bytecode の保存先(DOEFF_HY_CODE_STORE)の中身を変えない。

やってほしい事: 1. R1 を崩す例(検が全部緑のまま、値の形を印字している例)を作り、実際に examples/ に置いて検を走らせ、次に答えを変えない macro の変更を macros.hy に入れて、その例が赤になるかも走らせる。
2. R2 を崩す変更(答えを変えない macro の変更で、今の examples/ か検が赤になる物)を探す。3. R3・R4 を疑って確かめる。4. 検の作りに、他に壊れる所・誤判定があれば実測で示す。

報告の形: 結論 / 崩れた主張ごとに(何をしたか・出力の抜粋・再現手順) / 崩れなかった主張は試した事を 1 行ずつ / 推測と実測を分ける / 作業木の最後の状態(git status --short)。
