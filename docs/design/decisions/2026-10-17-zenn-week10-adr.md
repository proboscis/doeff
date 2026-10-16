# 連載の10本目「設計の記録をテストとして走らせる」を、新しい原稿として書く

- 決定日時：2026-10-17（JST）
- 決定担当：記事を月4本公開する取り組みで、連載の10本目の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：記事の取り組みが決めた書き方の決まり9項目と、連載の並べ方（案M・2026-10-17に決まった10本の順）の10本目「設計の記録をテストとして走らせる」。
- 置き場：シリーズ全体の決定は[2026-09-15-zenn-use-cases.md](2026-09-15-zenn-use-cases.md)にある。週ごとの記事が同じ文書の末尾へ同時に書き足すと取り込みで衝突するため、この回の記録は別の文書にした（週9と同じ）。
- 触らない物：公開順の表（`publications/zenn-use-cases-v0/publication-plan.md`）と`render_release.py`。表の10本目の原稿の欄（「これから書く」）は、表を持つ仕事が原稿へのリンクに変える。

## 決定と理由

- 原稿を、`doeff-tooling.md`の書き換えではなく、新しい`doeff-adr.md`にする。開発支援の回は索引・リンター・解析器などの別の悩みを扱い、ADRの節だけでは1本の悩みに足りないため。開発支援の回のADRの節には、新しい回への1行の案内を足した。
- 例を「業務のコードは時刻を直に読まない」というADRにする。規則・性質・反例・実行のテスト・形の検査がそろい、連載3本目の仮想時間とつながるため。
- 時計の依頼はdoeff-timeの`GetTime`、テストの時計は`sim_time_handler`にする。自作の依頼や時計を読者の手本にしないため。
- 規則を守らせる手段を2つ見せる。`deftest`は答えを、Semgrepの規則はコードの形を確かめる。片方だけでは見逃す例（`datetime.now(due.tzinfo)`は10月31日まで`deftest`を通る）を本文に置く。
- Semgrepの規則は`.semgrep.yaml`に置き、ADRの`defsemgrep`には規則の名と例だけを持たせる。CIの`semgrep scan`と、ADRの例の確かめが同じ規則を見るため。
- `verify-wiring`を手順に入れる。`testpaths`からADRが抜けても普段のpytestは通ることを、3つ目の破り方で見せる。
- 試し方は、doeffを取り寄せて`make sync`し、自分のprojectを`uv run --project`で走らせる形にする。2026-10-17の時点でdoeff-adrとdoeff-timeはPyPIに無いため。
- 完全な例は`examples/adr_as_test/`に置き、ADRの名前を`adr_*.hy`にする。`defadr_*.hy`のままだと、doeff自身のADRの集め手が「集められていないADR」として落ちるため。
- 図2枚を、週2・週3と同じ作り方（手で書いたSVG → `visuals/render_svg.mjs`）で作る。
- `verify_publication.py`の記事の数を26本から27本へ上げ、`reviews/main-check.py`の期待する回に`adr`を足す。連載の新しい回がメインの表に載るため。表示する本数は、期待する回の数から数える形にした。

## 戻す手順

本記事・`examples/adr_as_test/`・`reviews/adr-check.py`・`reviews/adr.md`・`reviews/adr-visuals.json`・図2枚とSVG・台帳（captions.json・manifest.json・adr-svg-source.txt・imagegen/README.mdの追記）・README.mdとdoeff-main.mdの表の1行・`doeff-tooling.md`の案内の1行・`feature-examples.md`と`package-coverage.md`の各1行・`reviews/README.md`の1行・`reviews/main-check.py`と`verify_publication.py`の変更・この文書を、この変更のcommitで戻す。製品のコードには触れていない。
