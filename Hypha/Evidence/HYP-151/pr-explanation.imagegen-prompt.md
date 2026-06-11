Tool: imagegen

Prompt:
Use case: infographic-diagram
Asset type: GitHub PR explanation diagram saved as a PNG, 16:9 landscape.

Primary request: Create a 霞ヶ関-style, information-dense Japanese briefing diagram for PR #417 / HYP-151. The diagram explains a narrow non-UI Makefile/test change: root `make lint` now runs `lint-ruff`, and a focused test locks that contract. Do not include a fixed count of existing Ruff errors.

Critical text-language rule: All visible headings, labels, captions, and explanatory text must be Japanese. English is allowed only for exact technical identifiers shown in code-like boxes: `HYP-151`, `PR #417`, `make lint`, `lint-ruff`, `uv run ruff check doeff/ tests/ packages/`, `tests/test_makefile_lint_contract.py`, `Makefile`, `AGENTS.md`, `Ruff`, `Pyright`, `Semgrep`, `doeff-linter`. Do not use English explanatory labels such as "before", "after", "root", "subpackage", "help", "CI", "check", "package lint", or "review". Use Japanese equivalents.

Canvas/layout: white government-report page, dark navy headers, thin ruled grid, compact sections, arrows, numbered badges, simple icons/pictograms. Make it readable and deliberate, like an official technical briefing sheet.

Exact visible structure and text:
Title: 「HYP-151: `make lint` に Ruff 検査を戻す」
Small subtitle: 「PR #417 / 非 UI 変更 / 画面証跡は不要」

Left panel heading: 「変更前」
Show `make lint` with a broken dotted arrow that skips `lint-ruff`.
Warning callout: 「ルート Ruff 検査が未到達」
Small explanation: 「`doeff/`・`tests/`・`packages/` の Ruff 指摘を入口で検出できない」

Center panel heading: 「変更後」
Show a vertical flow: `make lint` → `lint-ruff` → `uv run ruff check doeff/ tests/ packages/`.
Then branch to four smaller boxes with Japanese labels plus identifiers:
「型検査: Pyright」
「構造検査: Semgrep」
「規約検査: doeff-linter」
「パッケージ別検査」
Green result band: 「全 lint の入口という契約に戻す」

Right panel heading: 「固定した確認」
Show file icon plus `tests/test_makefile_lint_contract.py`.
Show magnifying glass callout: 「`make -n lint` の展開結果を検査」
Bullets in Japanese:
「`lint-ruff` が経路に含まれる」
「`uv run ruff check doeff/ tests/ packages/` が表示される」
「実行せずに Makefile の経路だけ確認する」

Bottom band heading: 「レビュアーが見る点」
Three compact cards:
1. 「`Makefile` の依存順が妥当か」
2. 「`AGENTS.md` と説明の契約が一致するか」
3. 「既存 Ruff 指摘は別作業に分ける判断でよいか」

Footer: 「この PR は lint 経路の契約修正だけを扱い、既存 Ruff 指摘の一括修正は含めない。」

Avoid: fixed counts of lint errors, English explanatory words, pseudo-code not present in the repo, invented APIs, handwritten SVG look, watermark, blurry text, decorative gradients, stock-photo background.
