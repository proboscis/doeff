Tool: imagegen

Prompt:

Use case: infographic-diagram
Asset type: GitHub PR body explanation image, 16:9 PNG.
Primary request: Create a dense Kasumigaseki-style Japanese briefing diagram explaining PR for HYP-158 in the doeff repository. The image must look like a polished government policy brief: information-dense, precise section hierarchy, compact tables, arrows, icons/pictograms, white paper background, dark navy text, red warning accent, green verification accent, thin gray rules, no decorative blobs.

Visible text must be Japanese, except exact technical identifiers. Use these Japanese headings and labels exactly:
Title: "HYP-158 WithHandler 誤検出修正"
Section 1 heading: "課題"
Section 1 labels: "内部互換処理", "旧形式の外部呼び出し", "Semgrep が同一視"
Section 2 heading: "変更"
Section 2 labels: ".semgrep.yaml", "shim の 1 行だけ除外", "fixture で旧形式を固定"
Section 3 heading: "維持する禁止"
Section 3 labels: "return_clause 指定", "3 番目の位置引数", "doeff_vm.WithHandler 旧形式"
Section 4 heading: "確認"
Section 4 labels: "doeff/program.py は 0 件", "fixture は 4 件検出", "公開 API テスト 8 件通過"
Bottom review box heading: "レビュー観点"
Bottom review labels: "互換処理は削除しない", "HYP-151 とは分離", "規則の意図は維持"

Technical identifiers that may appear as code labels: "_WithHandlerNode(h, body, *args, **kwargs)", "WithHandler(handler, expr, return_clause=...)", "WithHandler(handler, expr, third_arg)", "doeff-withhandler-no-return-clause", "tests/semgrep/fixtures/python/doeff/withhandler_return_clause_sample.py".

Composition: left-to-right flow. Left red warning panel shows the false positive: internal compatibility shim arrow collides with old external call patterns. Center blue change panel shows the semgrep rule narrowed by an exception for exactly the shim forwarding line. Right green verification panel shows accepted outcomes. Bottom horizontal review checklist with three bullets. Include small pictograms: warning triangle, shield, gear, checklist, file icon, arrow icons. Use crisp readable Japanese typography, high contrast, not playful, not marketing. Avoid pseudo-code not present in the implementation. Do not mention Program.resolve, lazy_ask, resolved dicts, or any API not in the PR.
