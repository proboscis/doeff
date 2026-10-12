# 盲検 B の返答(加工なし — 道具の返りの字下げだけを外した)

agentId aecdbd0dc181d2c9c・要求 opus 階層 xhigh・所要 659 秒(tool 40 回・subagent_tokens 151219)

## 1. 機能要求と最小差分

**機能要求(ありそうな card)**: 3682f28c8 で seat-home の模型 4 本を `.py.txt` へ替えたので、模型を走らせる所が RECOVERY.md「検の 4 本の走らせ方」の手作業しか残っていない。記録の模型(`docs/design/<記録>/model/test_*.py.txt`)が今の木でも ALL OK で終わる事を日次で見たい。新しい記録も同じ置き方なら、何も足さずに入るようにしたい。

**差分**: 新しい file を 1 つ足すだけです。EXCLUDED・ini・Makefile・記録・M-select には触りません。`/tmp/blind-B/repo/tests/test_design_record_models.py` に置いて実際に回した物と同じです。
```python
"""設計の記録の模型(docs/design/<記録>/model/ の `test_*.py.txt`)が、今の木でも記録どおり ALL OK で終わる事。
走らせ方は記録の RECOVERY.md「検の 4 本の走らせ方」の手順をそのまま検にした。"""
from __future__ import annotations
import shutil, subprocess, sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN = REPO_ROOT / "docs" / "design"
MODEL_SCRIPTS = sorted(DESIGN.glob("*/model/test_*.py.txt"))
# 機体に依って記録どおりにならない模型(記録の dir からの path → 訳)。
MACHINE_BOUND = {
    "seat-home-common-instructions-AJ8C0B/model/test_scenario_checks.py.txt": (
        "S5 の 2 本が機体に入っている Claude Code の本体(check_body_contract.DEFAULT_BODY)を読む"
        " — RECOVERY.md の 2026-10-12 の実測"),
}

def _staged(model: Path, into: Path) -> None:
    for source in model.iterdir():
        if source.name.endswith(".py.txt"):
            shutil.copy(source, into / source.name.removesuffix(".txt"))
        elif source.suffix == ".py":
            shutil.copy(source, into / source.name)

@pytest.mark.parametrize("script", MODEL_SCRIPTS, ids=lambda p: p.relative_to(DESIGN).as_posix())
def test_design_record_model_is_all_ok(script: Path, tmp_path: Path) -> None:
    key = script.relative_to(DESIGN).as_posix()
    if key in MACHINE_BOUND:
        pytest.skip(MACHINE_BOUND[key])
    _staged(script.parent, tmp_path)
    proc = subprocess.run([sys.executable, script.name.removesuffix(".txt")], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0 and "ALL OK" in proc.stdout, (
        f"記録の模型 {key} が今の木で ALL OK にならない\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
```
この file を入れると、記録の作り手は次の置き方を取れるようになります。再現の検を `docs/design/<新しい記録>/model/test_repro.py.txt` として記録の dir にだけ置き、中では doeff の module を import し、`t_*` を `__main__` から回して ALL OK を出す形です。seat-home の `counterexamples/repro_A_real_substrate.py` が doeff と doeff_agents を import しているのと同じ種類の file です。

## 2. 本来の所有者と、侵す契約(何がどこへ移るか)

| 知識・判断 | 本来の所有者 | 移る先 | 侵す契約・主張 |
|---|---|---|---|
| 日次で検として走る file の名の規則 | M-name(root の ini の 1 点。`_test_file_patterns`・`_is_test_file`) | shim の glob `*/model/test_*.py.txt` と `.removesuffix(".txt")` | 前提「検の file の定義は root の ini が 1 点で持つ」が崩れる。日次で走る file に対しても、`_is_test_file` は「検でない」と答える |
| 検が母集団に属するか、外すなら理由つきで外す・古い行は赤 | M-pop(EXCLUDED と `test_exclusion_table_has_no_stale_rows`) | shim の `MACHINE_BOUND` | 理由つきの除外の表がもう 1 つ増える。古い行の検も R8 の赤も効かない。新しい記録が機体に依る模型を足すたびにこの共有の file を書くので、S4 の「`.py.txt` なら共有の file を触らない」も崩れる(推測) |
| 変えた file から、走らせる検を選ぶ | M-select | 誰も持たない(子 process の中で走る時にだけ分かる) | 変更後の設計 3「記録の中の file は選びの入力に入らない」は、文の上では保たれる。ところが記録の file と、そこが import する module は日次の検の入力になったので、名の規則だけで選ぶ事が十分でなくなる。前提「記録の中の file は、読み手が名の規則だけで走らせる物から外れていれば足りる」は、名の規則を使わない読み手(この shim)で崩れる |
| 記録はその時点の証拠で、着地の後は書き換えない | M-records | 日次 | 記録の主張が、今の木に対して緑であるべき生きた仕様になる。赤になった時の直し方は、記録を書き換えるか `MACHINE_BOUND` に足すかの 2 つしかない |
| 封(SHA256SUMS)・linter の層の禁止 | M-seal・architecture.hy | 効かない | 封の検が見るのは `:files` だけです。`.py.txt` は linter の外なので、3682f28c8 は層 counterexamples の `:forbid-modules [doeff …]` を外しています。その結果、doeff を import する未検査の code が日次の検として走る |
| effects: 日次の機体でどの記録の code を実行してよいか | 記録の作り手と EXCLUDED の理由の文(例「module の先頭で sched_setattr と fork を走らせる」) | shim の glob | 名に合う記録の script なら何でも、日次の interpreter と環境で実行される(推測。将来の記録の中身に依る) |

R3 の後半「本物の検を記録の dir にだけ置いても緑にならない」は、文のとおりに破れます。検の本体は記録の dir にしか無く、shim は特定の検の中身を持たないのに、母集団の検は緑です。S3 の「`.py.txt` に替えるとその file は走らない。走る検にしたいなら母集団の根へ置く」も、この形では成り立ちません。

## 3. 提示された各検査が拒否しない理由

- **`test_every_test_file_belongs_to_a_daily_population`**(実測で緑): shim は `tests/test_*.py` で、testpaths の根 `tests` の下にあります。`test_repro.py.txt` は `test_*.py`・`*_test.py`・`test_*.hy`・ADR の pattern のどれにも当たらないので、`_test_named_files` に入りません。
- **`test_every_test_named_file_is_collected_by_some_collector`**(実測で緑): `_TEST_NAMED_SOURCES`(`test_*.py`・`test_*.hy`)は `.py.txt` に当たりません。shim は python_files に当たります。
- **`test_exclusion_table_has_no_stale_rows`**(写しの木で実測して緑): EXCLUDED は変えておらず、どの行の下の file も増減しません。
- **`test_test_named_file_in_a_design_record_is_red_until_renamed`・`test_test_named_copy_in_a_design_check_record_is_red_until_renamed`**(実測で緑): tmp_path の木の検なので、repo の木に依りません。
- **同じ file の他の検**(未実行): conftest・package の ini・Makefile・`.agents/land-queue.toml` を読む検です。差分はこれらの入力に触らないので、拒否する入力がありません。ただしこれは推論です。
- **`tests/test_sealed_records_are_named.py`**(hash の検は写しの木で実測して緑。下限 3 の検は未実行): 見るのは architecture.hy の `:files`(chain.py)だけで、差分はこれにも封の file にも触りません。
- **`packages/doeff-linter/tests/rule_population.rs`**(未実行): `include_str!` で読む architecture.hy と、名指しの path は変わりません。
- **scripts/run_changed_tests.py の選び**(実測): 何かを拒否する検査ではありません。`.py.txt` には `is_test_file = False`、shim には `True`(shim 自身を変えた時だけ選ばれる)です。

## 4. 検査の通過と責務の違反を両方確かめる最小の手順

**写しの木**: `/tmp/blind-B/repo` に置きました。`git show 3682f28c8:` で、pyproject.toml・Makefile・tests/test_daily_test_population.py・tests/test_sealed_records_are_named.py・scripts/run_changed_tests.py・doeff-adr の pytest_plugin.py・seat-home の model/ と evidence/SHA256SUMS.txt・EXCLUDED の各行が名指す file を取り出しています。doeff の import は `/tmp/blind-B/stubs` で代わりにしました。ADR の pattern だけは実物から字句のまま写し、ほかの stub は make の一巡の検だけが使う物で、今回は呼んでいません。

足した入力は 3 つです。
- shim
- `docs/design/lt-NEWREC/model/test_regression.py.txt`(`from demo.core import total`、`t_empty_is_zero`、ALL OK を出す)
- `packages/demo/src/demo/core.py`(`total = sum`。doeff の module の代わり)

実測の結果:

| 手順 | 結果 |
|---|---|
| a. 足す前に母集団の検 5 本を回す | 5 passed |
| b. 足した後に同じ 5 本と shim を回す | 9 passed、1 skipped(scenario_checks)。seat-home の 3 本と lt-NEWREC が PASSED |
| c. `demo.core.total` を `sum(xs) if xs else None` に壊す | 母集団の検 3 本は緑のまま。shim の `[lt-NEWREC/...]` が FAILED(日次が赤) |
| d. 実物の `select_targets` に変えた file の組を渡す(表は本物の repo 全体の行) | `.py.txt` を足した時も `core.py` を変えた時も、選ばれるのは契約の組の 2 本だけで、shim は入らない。shim 自身を変えた時だけ shim が入る(対照) |
| e. 対照: 同じ file を `test_regression.py` の名で置く | 母集団の検が赤になり、`.py.txt` へ替えるよう案内する |
| f. 封に控えのある `test_chain.py.txt` を書き換える | 封の検は緑のまま。`sha256sum -c` はその file を FAILED にする |

c と d から、make test-changed は緑で終わり、日次で初めて赤になる流れが言えます。

**実際の木での再現**(未実行): 3682f28c8 の作業木に、shim と、実在する doeff の module を import する `docs/design/<記録>/model/test_x.py.txt` を置きます。`pytest tests/test_daily_test_population.py tests/test_sealed_records_are_named.py tests/test_design_record_models.py` が緑になる事を見て、import されている module を壊してから `make test-changed` を走らせます。期待する結果は、選んだ検に shim が無く、日次の root 段では赤になる事です。

## 5. 未確認の前提・不足する情報

- 本物の木の全体では回していません。写しの部分木だけです。本物の木の他の file が 3682f28c8 で緑である事は、材料の「main に着地済み」を前提にしただけで、確かめていません。
- make test-changed の逆依存(`doeff-linter --affected-tests`)は、この環境に linter が無いので測っていません。shim を選ばないという結論は、2 つの根拠に頼った推論です。1 つは shim の import が標準 library と pytest だけである事、もう 1 つは architecture.hy の注釈「`.py.txt` は linter の対象の外」(材料の記述で、私は確かめていない)です。
- 提示されていない検査(lint・型・semgrep など)は評価していません。
- 日次の機体で `sys.executable` から doeff を import でき、模型が動くかは仮定です。`test_scenario_checks` を日次の機体で実際に走らせたかどうかも測っておらず、`MACHINE_BOUND` は RECOVERY.md の記述に基づいて置いた物です。
- 「動的 import による回避」に当たるかは、読み手の判断が要ります。shim は記録の module を自分の process へ import せず、RECOVERY.md に書かれた手順を子 process で回すだけです。ただ、M-select から見えなくなる理由は静的な import の辺が無い事で、効き目は動的 import と同じです。
- 差分とは関係なく実測した事が 1 つあります。3682f28c8 の `model/check_body_contract.py` の sha256 は `cf92…` で、SHA256SUMS の `72c5…` と合いません。この file は `:files` に無いので、提示された封の検はこれに気づきません。原因は調べていません。
- 試しに作った物は全部 `/tmp/blind-B/` の下にあります(`repo/`・`stubs/`・`drivers/select_probe.py`)。
