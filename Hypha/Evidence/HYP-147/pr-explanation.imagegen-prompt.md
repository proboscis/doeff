Tool: imagegen

Prompt:

Use case: infographic-diagram
Asset type: GitHub PR explanation diagram saved as a PNG for HYP-147.
Primary request: Create an information-dense Japanese Kasumigaseki-style briefing diagram that explains PR #416 for HYP-147. The theme is repository hygiene: generated lint logs, local databases, doeff-flow traces, agent runtime state, browser automation captures, local Claude skill/settings files, and an accidental root server.js bundle are removed from git tracking and ignored; a repo hygiene guard checks that they do not return.

Required visible Japanese text only, except exact technical identifiers. Use crisp Japanese labels and headings. No English explanatory headings. Technical identifiers that may appear: HYP-147, PR #416, `make lint-doeff`, `--no-log`, `.gitignore`, `make check-repo-hygiene`, `git ls-files`, `.agent-home/`, `.playwright-mcp/`, `.doeff-flow/`, `.doeff-lint.jsonl`, `server.js`.

Composition: A clean bureaucratic policy brief layout, dense but readable, landscape 16:9. White background with thin navy and dark gray rule lines, small accent colors in red/blue/green, table-like sections, numbered review points, arrows, and simple pictograms/icons. Use four main zones:
1. Top title bar: 「HYP-147 リポジトリ整理」 and subtitle 「生成物を追跡対象から外し、再混入を検査」.
2. Left zone titled 「混入していた生成物」 with small icons and grouped labels: 「lint ログ」, 「SQLite / trace」, 「agent 実行状態」, 「画面キャプチャ」, 「ローカル設定・skill」, 「root server.js」.
3. Center zone titled 「変更した境界」 with arrows: `make lint-doeff` -> `--no-log`; `.gitignore` -> generated artifacts ignored; doeff-flow sample -> `.doeff-flow/` local output; tracked artifacts -> removed from git.
4. Right zone titled 「確認観点」 with checklist rows: `make check-repo-hygiene`, `git ls-files` guard, current `main` artifacts covered, UI 変更なし.
5. Bottom strip titled 「レビュアーが見ること」: 「削除対象の分類が妥当か」, 「ignore ルールが広すぎないか」, 「検査が再発を止めるか」.

Style details: professional Japanese government briefing, compact labels, deliberate section hierarchy, small arrows connecting cause to fix to verification, no decorative gradients, no pseudo-code, no fake APIs, no screenshots, no logos, no watermarks.
