Tool: imagegen

Prompt:
Use case: infographic-diagram
Asset type: GitHub PR body explanation diagram for HYP-050.
Primary request: Create a wide, information-dense Kasumigaseki-style Japanese briefing diagram titled "HYP-050 PR説明図" for a doeff PR. The diagram must explain that `doeff_ml_nexus.runner.p_run` no longer reads OS environment variables directly; the launcher/interpreter boundary converts `DOEFF_INPUT` and `DOEFF_OUTPUT` into Program env values, and `runner.hy` obtains paths through `Ask`.

Visual structure:
- Top summary band: `doeff_ml_nexus.runner.p_run` は OS環境変数を直接参照しない実装へ変更。起動側で与えられたパスを Program env に変換し、Ask 経由で取得する。
- Left section heading: `変更前`. Show Docker起動, 起動境界, Program本体, `runner.hy / p_run`, and a red warning that the Program directly references OS environment variables. Include `DOEFF_INPUT /tmp/doeff-exchange/program.pkl` and `DOEFF_OUTPUT /tmp/doeff-exchange/result.pkl`. State that Docker外部契約は維持.
- Center transition: a large red X and arrow labeled `直接参照を廃止`.
- Right section heading: `変更後`. Show Docker起動 still providing `DOEFF_INPUT` and `DOEFF_OUTPUT`, then `runner_env.hy` converting them into Program env, then `runner.hy / p_run` calling `get-exchange-paths` and reading `Ask("doeff_ml_nexus.runner.input_path")` and `Ask("doeff_ml_nexus.runner.output_path")`.
- Lower left section: `本PRの主なポイント` with Japanese check-mark bullets for no direct OS env read in `runner.hy`, `runner_env.hy` converting at the boundary, `Ask` path retrieval, Docker external contract retained, and improved testability/maintainability.
- Lower middle table: `Askキー定義（Program env）` with keys `doeff_ml_nexus.runner.input_path` and `doeff_ml_nexus.runner.output_path` and their default path meanings.
- Lower right section: `検証` with Japanese cards for focused pytest pass and semgrep guard banning `runner.hy` OS env references.

Style constraints:
- Use only Japanese explanatory text except exact technical identifiers such as HYP-050, file names, key names, command names, and env var names.
- Use deliberate section hierarchy, dense but readable layout, arrows, boxes, check marks, shields, beaker icons, document icons, and Docker pictograms.
- Avoid pseudo-code, `Program.resolve()`, eager resolved dictionaries, Mermaid, SVG, HTML/CSS, PPT, or any hand-rendered layout language.
- Match the implementation: `runner_env.hy` is the interpreter/launcher boundary, Docker's `DOEFF_INPUT` / `DOEFF_OUTPUT` external contract remains, and `runner.hy` reads Program env through `Ask`.
