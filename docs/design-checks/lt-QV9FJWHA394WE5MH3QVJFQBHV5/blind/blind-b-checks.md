# 盲検 B に渡す検査の実体・設定・検出範囲

| 検査 | 実体 | 設定・対象 | 走る所 | 検出する物 |
| --- | --- | --- | --- | --- |
| 例が走る | `test_example_runs[<file>]`(`packages/doeff-traverse/tests/test_examples_run.py`) | `examples/*.hy` のうち BROKEN の表に無い物。`sys.executable -m hy <file>` を子 process で起こし(env を受け継ぐ・timeout 50 秒)、終了コード 0 を求める | 日次の全体検証の段 packages(`make test-packages` → `pytest packages/doeff-traverse/tests -m "not e2e"`)。手で `uv run pytest packages/doeff-traverse/tests/test_examples_run.py` | 例の compile の誤り(消えた macro の require・defk の契約の欠け)と、実行時の例外で 0 以外で終わる事 |
| 表の例は今も落ちる | `test_broken_example_is_still_broken[<file>]`(同じ file) | BROKEN の表の行ごとに、file が在る事と、走らせて 0 以外で終わる事 | 同上 | 直したのに表に残った行・file の無い行 |
| 変更箇所の品質検査(fast の段) | `agentcli code-quality --scope changed`(ruff と速い規則。型検査は撃たない) | 変えた file | 出荷の前に書き手が手で走らせる | ruff の規則違反など |
| マージの列 | `ai land` | 衝突の印の走査だけ(operator の裁定 land-gate-runs-no-tests — 検を走らせない) | マージの時 | 検の赤は検出しない |

検の母集団: root の ini(`pyproject.toml` の `[tool.pytest.ini_options]`)が package の検にも効く。package は自分の pytest の設定を持たない。
pyright は Hy の例を見ない。
