# 盲検 A の返答(加工なし — 道具の返りの字下げだけを外した)

agentId a404cf1c8e6ae92ef・要求 opus 階層 xhigh・所要 499 秒(tool 32 回・subagent_tokens 134520)

**1. 現実的な変更要求と、その要求が必要になる理由**

要求: 新しい設計の記録に、出荷中の実物の関数を import して撃つ測りの script を `<名>_test.py` の名で入れます。その script は閉じた評価の記録(assessment の JSON)が path と sha256 で名指します。そのため S2 のとおり改名できず、EXCLUDED に 1 file の path の行を 1 つ足します。その後、通常の開発でその実物の module を変えたり、関数を改名・移動したりします。

理由: この 2 つの慣行は、3682f28c8 の木にすでに別々の記録として在ります。1 つの記録で重なるのは自然です。
- seat-home の `evidence/symlink_install_race_real_fn.py` は「出荷されている関数そのものを測る」ために、`from doeff_agents.sessionhost.substrate import ensure_symlink_outcome` を書いています。
- worker-pod-cpu-share は、測りの script を `fork_test.py`・`reset_test.py` と名付けています。閉じた記録がそれを path と sha256 で名指します。
- `substrate.hy` の `ensure-symlink-outcome` は張り替えの「1 点」(D8)で、改名や移動は普通のリファクタの範囲です。

**2. 関係する元の主張・前提と、予想に反して変わる責務・モジュール・契約**

関係する主張:
- S2: 「EXCLUDED に 1 行を足す。M-select・M-seal・M-name は変えない」。
- 変更後の設計 3: 「記録の中の file は名の規則に当たらないので、選びの入力に入らない」。
- EXCLUDED の頭の注釈: 「残る穴は make test-changed の選び 1 つ: その file を変えると…0 本集まって『未測』— 赤にはならない」。
- 前提: 「記録の中の file は、名の規則だけで走らせる物から外れていれば足りる」。

前提は守っています。この例は「適用」の軸 S2 の条件(閉じた記録が名指す `*_test.py`、改名できない)をそのまま満たします。足したのは「その script が repo の module を import する」点だけで、S2 も前提もこれを除いていません。したがって契約の適用範囲の中です。R3(日次)は破れていません。日次が緑なのは正しい結果です。

予想に反する点は 3 つです。
- (a) 引き金は「その file を変える」ことではありません。「その file が import する生きた module を変える」ことです。閉じた記録は書き換えないので、実際に起きる引き金はほぼこちらだけです。
- (b) 結末は「未測」だけではありません。import が壊れると、集める時に落ちます。`_classify_run` はこれを赤と判定し、rc 1 になります。
- (c) 記録の側では直せません(改名できず、封があるので書き換えもできない)。赤を消すには、M-select(`scripts/run_changed_tests.py`)か linter の `--affected-tests` が「この検の名の file は検ではない」と知る必要があります。

その結果、変わる物は次のとおりです。
- M-select と、その中の M-name の写し。
- M-pop の表の置き場と形。
- 場合によっては doeff-linter(注釈によれば linter を使う repo は 4 つ)。

S2 の予測(M-pop の表の 1 行だけ)はここで外れます。

**3. どの知識・判断がどこへ漏れるために変更が波及するか**

漏れる判断は「この検の名の file は検ではない(走らせない)」です。これは本来、M-name の知識です(前提では「検の file」の定義は root の ini が 1 点で持つ)。S2 の行はこの判断を M-pop の EXCLUDED に置きます。EXCLUDED は test module の中の dict です。そのため「検の file か」の定義が ini と EXCLUDED の 2 点に割れ、ini しか読まない M-select と食い違います。

さらに、M-pop が隠すはずの「どの file を数えないか」を、逆依存の側はどこにも持っていません。linter の母集団は `git ls-files` の全ての `.py`・`.hy` で(affected_tests.rs の `known_files`)、docs/design も含みます。

4 つの区別に当てると次のとおりです。
- **公開契約の拡張としては設計できる:** 「検の名だが検ではない file」の一覧を M-name の 1 点(例: pyproject)に置き、M-pop と M-select の両方が読む形です。ただし今の設計はこれを M-pop に置き、波及は表の 1 行と予測しているので、この例は予測の外の変更です。
- **配線の変更では足りない:** 表は doeff を import する test module の中にあります。stdlib だけで書かれた `run_changed_tests.py` はそれを import できません。ast で読ませれば、test module の内部の形に結びつきます。
- **表の形も変わる:** EXCLUDED には 2 つの意味の行が混ざっています。「本物の検だが日次の外」(`packages/doeff-agents/conformance/`)と、「検の名だが検ではない」(fixture の module・例示の script・測りの script)です。M-select に要るのは後者だけなので、表に種類の欄を足す必要があり、これは M-pop の表の形の変更です。
- **共同の不変条件が明示されていない:** 「M-select が検として走らせる file ⊆ 日次の母集団 ∪ 本物の検」を、M-pop と M-select が共に守る条件としてどこにも書いていません。

補足として、同じ形は記録の外の既存の行にも在ります(読んだだけで、走らせていません)。`packages/doeff-agentic/examples/` の `test_event_logging.py`・`test_mock_workflow.py` は `from doeff_agentic import …` と `def test_*` を持ちます。そのため doeff_agentic の変更で逆依存に選ばれ、検として走る見込みです。

**4. 最小の具体的な入力・変更例と再現手順、観測すべき結果**

実測した模型は `/tmp/blind-A/harness.py` と `/tmp/blind-A/tree/` です。
- 3682f28c8 の `tests/test_daily_test_population.py` の関数を使いました(doeff の import だけを stub にした)。
- `scripts/run_changed_tests.py` の写しから、`collector_patterns`・`select_targets`・`extend_with_reverse`・`batches_of`・`--as-pytest` の ReportPlugin・`_classify_run` を使いました。
- linter の答えは、affected_tests.rs の `module_key`・`resolve`・幅優先を写した小さな模型で作りました。

模型の木:
- `packages/pkg/src/pkg/substrate.py`(`ensure_symlink_outcome()` を持つ)
- `packages/pkg/tests/test_substrate.py`
- `docs/design/rec-1/evidence/symlink_race_test.py`(先頭で `from pkg.substrate import ensure_symlink_outcome`、module の先頭で印の file を書く)
- `records/assessment.json`(path と sha256 を持つ)

実測の結果:
- **M-pop:** S2 の行の前は赤、行を足すと緑(設計どおり)。改名の差分の後も緑のままです。
- **差分 1(substrate.py の中身だけを変更):** 記録の script が「逆依存 距離 1」で選ばれました。束 0(session `.`)として、package の検の束より先に走りました。module の先頭も走り、印の file が書かれました。判定は「未測 — 集めた検が 0 本」です。
- **差分 2(関数を改名し、生きた使い手の検も直す):** 記録の script は「赤 — 集める時に落ちた」(pytest rc 1)でした。package の検は緑なので、make test-changed は rc 1 になります。

本物の repo での手順(未実行):
1. 3682f28c8 の作業木に `docs/design/lt-REPRO/evidence/symlink_race_test.py` を置きます(中身は `import hy` と `from doeff_agents.sessionhost.substrate import ensure_symlink_outcome`)。EXCLUDED に S2 の 1 行を足して commit し、これを base とします。
2. `pytest tests/test_daily_test_population.py -k belongs_to_a_daily_population` が緑であることを確かめます。
3. `substrate.hy` の `ensure-symlink-outcome` を改名し、本物の使い手を直します。
4. `uv run --script scripts/run_changed_tests.py --base <1 の commit>` を走らせます。

観測すべき結果:
- 記録の script が「逆依存 距離 1: …/sessionhost/substrate.hy → docs/design/lt-REPRO/…」で選ばれ、判定が「赤 — 集める時に落ちた」になる。
- 最後の行が `land-focus-counts: … red=1 … rc=1` になる。
- 中身だけの変更では、未測が毎回 1 つ増える。
- `docs/` は `packages/` より path の順で前に来るので、記録の束が package の束より先に走る。

**5. 未確認の前提、不足する情報**

- **実測:** 4 の模型の結果です。
- **推測(源を読んだだけ):** 本物の doeff-linter(Rust)が同じ答えを返すことです。根拠は、`known_files` が docs/design を含むこと、`rec-1`・`2026-10-12` のような識別子でない dir が source の根になること、`*_test.py` が `is_test_file` に当たることです。binary が PATH に無く、走らせていません。
- **未確認:** `substrate` の直接の使い手が `max_dependents`(40)以下かどうか。40 を越えると hub として辿らず、この例は起きません。`doeff`・`doeff_vm` のような hub だけを import する記録の script でも起きません。
- **未確認:** 本物の root conftest(機体の検の枠)、`uv run --no-sync` の環境、hy の import hook の振る舞い。
- **推測:** 重い測りの script が先頭の束で 60 秒の予算を使い、本物の検が「未測」へ押し出されること。模型の script は軽いものでした。
- **探索の範囲:** 材料の本文と、3682f28c8 の次の file を読みました。`run_changed_tests.py`・`test_daily_test_population.py`・`affected_tests.rs`・seat-home の `architecture.hy` と `SHA256SUMS.txt`(変更の前後)・cpu-share の測りの script 2 本・seat-home の evidence の import・root の pytest ini・`examples/` の import。file の場所を探すために、1 度だけ `git grep` を 3682f28c8 の木に使いました(読み取りのみ)。
- **選ばなかった候補(検証していない):**
  - (i) `.py.txt` は linter の母集団からも外れます。3682f28c8 では、seat-home の `counterexamples` 層(`:forbid-modules`)が消えました。名の 1 つの決定が「検ではない」と「Python の source ではない」を同時に決めています。
  - (ii) 赤の文は `.py.txt` だけを案内します。一方 M-name は `defadr_*.hy`・`test_*.hy` も検の file に数えるので、記録に executable ADR の下書きを置くと案内が合いません。
  - (iii) `_NOT_WALKED`(`build`・`dist`・`target`・`.` で始まる dir)や `fixtures` の下に置いた本物の検は、M-pop に数えられません。R3 の穴の候補ですが、記録に固有ではありません。
