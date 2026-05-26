Tool: imagegen

Prompt:

Use case: infographic-diagram
Asset type: GitHub PR explanation diagram, PNG, landscape 16:9
Primary request: Create an information-dense Kasumigaseki-style policy brief diagram for PR HYP-199. All visible headings, labels, and explanatory copy must be Japanese. Only exact identifiers may stay in English: package names, file paths, HYP-199, README, tag name package/vX.Y.Z, and tools/verify_dist_metadata.py. Avoid tiny text and avoid long package lists except the root publish order below.

Visual style: Japanese government / Kasumigaseki briefing sheet, dense but clean, white background, thin navy grid lines, restrained accent colors, small pictogram icons, arrows, numbered stages, tables, callout boxes, compact typography. No decorative blobs, no pseudo-code, no screenshots. Use clear hierarchy and avoid overlapping text.

Visible content:
Title: "HYP-199 公開パッケージ公開契約"
Subtitle: "README の導入案内、runbook、公開処理を同じ公開所有者にそろえる"

Left block heading: "変更前の不一致"
Four bullets: "runbook は 3 件だけを説明", "公開処理は追加の配布物も公開", "README に導入案内が複数存在", "公開順と配布物確認の責任が曖昧"

Center block heading: "今回の実装"
Three numbered lanes with icons and arrows:
1. "分類表を追加" -> labels "root タグ公開", "独立公開", "公開しない"
2. "公開処理を一覧化" -> labels "6 件の Python 配布物を構築", "成果物名を固定"
3. "確認を固定" -> labels "wheel", "sdist", "tools/verify_dist_metadata.py"

Right block heading: "root タグ公開順"
Show one clear ordered chain with exact package names and arrows:
"doeff-vm" -> "doeff-indexer" -> "doeff-hy" -> "doeff-core-effects" -> "doeff" -> "doeff-time" -> "doeff-preset" -> "doeff-agents"

Bottom-left block heading: "独立公開の扱い"
Show four grouped cards: "基盤系 3 件", "運用系 3 件", "provider 系 5 件", "その他 4 件". Add labels "package/vX.Y.Z", "依存順を runbook に明記", "構築と配布物確認は必須".

Bottom-right block heading: "レビュアー確認点"
Checklist: "分類が妥当か", "順序が公開処理と一致か", "README 導入案内が収まるか", "隣接 issue と混ざらないか"

Footer strip with exact identifiers: "tests/test_release_publish_contract.py", "tests/test_doeff_vm_release_contract.py", "Hypha/Evidence/HYP-199/pr-explanation.png"

Constraints: avoid the words workflow, matrix, build, artifact, metadata, release owner, review gate, pass, human-review, UI evidence, core-philosophy as explanatory copy. Exact identifiers must be spelled exactly: doeff-vm, doeff-indexer, doeff-hy, doeff-core-effects, doeff, doeff-time, doeff-preset, doeff-agents, tools/verify_dist_metadata.py, HYP-199. Do not include invented APIs, Program.resolve, lazy_ask, resolved[...], or unrelated code flow.
