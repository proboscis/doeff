Tool: imagegen

Prompt:
Create a 16:9 Japanese Kasumigaseki-style PR explanation diagram as a PNG. Title: 「HYP-151: make lint に Ruff を戻す」. Three columns: 「変更前」 shows 「make lint」 missing 「lint-ruff」 with warning 「root Ruff が未到達」. 「変更後」 shows 「make lint」 -> 「lint-ruff」 -> 「uv run ruff check doeff/ tests/ packages/」 plus 「pyright」「semgrep」「doeff-linter」「subpackage lint」. 「固定した確認」 shows 「tests/test_makefile_lint_contract.py」 and 「make -n lint の出力を検査」. Bottom band 「レビュアー観点」: 「help と AGENTS.md の説明を維持」「subpackage lint だけに頼らない」「既存 Ruff 指摘 1640 件で make lint-ruff は失敗」. Use dense government-report styling, crisp Japanese text, arrows, icons, section hierarchy, white background, no watermark.
