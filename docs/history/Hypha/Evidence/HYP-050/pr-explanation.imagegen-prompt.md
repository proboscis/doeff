Tool: imagegen

Prompt:
Use case: infographic-diagram
Asset type: GitHub PR body explanation diagram for HYP-050.

Primary request:
Create a wide 16:9, information-dense 霞ヶ関スタイルの日本語ブリーフィング図 titled exactly: 「HYP-050 PR説明図」. The diagram explains this exact PR behavior: `doeff_ml_nexus.runner.p_run` no longer reads OS environment variables directly; `runner_env.hy` at the launcher/interpreter boundary converts `DOEFF_INPUT` and `DOEFF_OUTPUT` into Program env values; `runner.hy` reads those values through `Ask`.

Hard text constraints for the image:
- All visible headings, labels, callouts, bullets, and prose must be Japanese.
- Use English only for exact technical identifiers: HYP-050, doeff, Docker, Program, Program env, Ask, runner.hy, runner_env.hy, p_run, get-exchange-paths, DOEFF_INPUT, DOEFF_OUTPUT, doeff_ml_nexus.runner.input_path, doeff_ml_nexus.runner.output_path, /tmp/doeff-exchange/program.pkl, /tmp/doeff-exchange/result.pkl.
- Do not include verification numbers such as 8件 or 9件.
- Do not include these English words anywhere as normal prose: focused, pytest, semgrep, guard, pass, before, after, input, output, fixture, path, env.
- For ordinary prose use Japanese terms: 「関連テスト」「静的検査」「通過」「パス」「設定値」「環境変数」.

Layout:
1. Top title band:
   - Large title: 「HYP-050 PR説明図」
   - Summary: 「実行設定の取得を Program 本体から起動境界へ移し、Program 内部は Ask で読む構成に整理」
2. Left panel heading: 「変更前」
   - Flow: 「Docker起動」 -> 「起動境界」 -> 「Program本体」.
   - In Program本体, show `runner.hy / p_run` with a red warning label: 「OS環境変数を直接参照」.
   - Show DOEFF_INPUT /tmp/doeff-exchange/program.pkl and DOEFF_OUTPUT /tmp/doeff-exchange/result.pkl as technical identifiers.
   - Japanese note: 「Docker の外部契約は維持」.
3. Center transition:
   - Large red X and right arrow.
   - Label: 「直接参照を廃止」.
4. Right panel heading: 「変更後」
   - Flow: 「Docker起動」 -> `runner_env.hy` -> 「Program env」 -> `runner.hy / p_run`.
   - `runner_env.hy` card label: 「起動境界で設定値を変換」.
   - Program env card contains doeff_ml_nexus.runner.input_path and doeff_ml_nexus.runner.output_path.
   - `runner.hy / p_run` card: 「get-exchange-paths で取得」「Ask で Program env から読む」.
   - Green note: 「Program 本体は OS 環境変数を読まない」.
5. Bottom left card heading: 「本PRの主な確認点」
   - Bullets: 「Program 本体の直接参照を除去」, 「起動境界で設定値を注入」, 「Docker 外部契約を維持」, 「テストでパスを差し替え可能」.
6. Bottom middle card heading: 「Askキー定義」
   - Two-row table for doeff_ml_nexus.runner.input_path and doeff_ml_nexus.runner.output_path.
   - Japanese descriptions: 「入力 Program ファイルの絶対パス」 and 「結果ファイルの出力先パス」.
7. Bottom right card heading: 「検証」
   - Three Japanese status chips with check icons: 「関連テスト 通過」, 「静的検査 通過」, 「差分確認 通過」.
   - No numeric counts.

Style:
- Japanese government briefing / 霞ヶ関 slide aesthetics: dense grid, navy headers, green success panel, red warning, thin ruled boxes, arrows, icons for Docker container, boundary, document, shield, test flask, and check marks.
- Make labels readable and avoid overlap.
- Do not show pseudo-code or APIs that are not in the implementation: no `Program.resolve()`, no eager dictionaries, no nested `resolved[...]` reads.
- Raster infographic, polished and readable.
