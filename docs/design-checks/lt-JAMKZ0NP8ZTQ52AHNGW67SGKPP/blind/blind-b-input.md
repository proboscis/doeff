# 盲検 B の入力

材料は同じ dir の blind-common.md(要件・責務・公開契約・将来の変更についての主張・読める実コードの範囲)。

## 検査の実体・設定・検出範囲

- doeff-linter(binary の path は blind-common.md)。DOEFF002 の実装 = packages/doeff-linter/src/rules/doeff002_mutable_attribute_naming.rs。
  DOEFF004(os.environ・os.getenv への参照)の実装 = packages/doeff-linter/src/rules/ の doeff004 の file。
- 日次の lint の段(.agents/land-queue.toml の lint): `sh scripts/gate_tools.sh linter && uv run --no-project python
  scripts/hook_finding_baseline.py --strict check doeff-linter`。severity error の所見だけを、規則 × file の数で基点と比べる。
  対象 = git が追跡する .py / .pyi(symlink を除く)。pyproject.toml の [tool.doeff-linter] の exclude と、file から上へ最も近い
  architecture.hy の層の宣言(:exempt)で母集団から外れる file / 規則がある。
- commit の hook も同じ基点で、stage した file を比べる。
- linter の検: packages/doeff-linter の cargo test(lib と tests/rule_population.rs など)。

## 依頼文

あなたは静的検査を通過する責務違反の失敗例を生成してください。
合理的な機能追加・修正として実装されそうで、提示した検査をそのまま実行しても通るのに、
知識・判断・状態・effectsの所有者や公開契約を侵す実装を1つ具体化してください。
一般的な設計レビューや承認判定は求めていません。

検査の無効化、baselineの改変、対象除外、as any、ignoreやlint抑制、根拠のないcast、
動的importによる回避は使わないでください。小さい既存例に対する抜け道だけでなく、
通常の実装者が機能を実現しようとして書きそうなコードで示してください。

返答には次を含めてください。
1. 現実的な機能要求と、提案する具体的なコードまたは最小差分。
2. 本来の責務所有者と、違反する契約。何の判断・知識がどこへ移るか。
3. 提示された各検査がそのコードを拒否しない理由。未提示の検査は推測しないこと。
4. 同じ設定で検査通過と責務違反の両方を確かめる最小の入力・手順・期待結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
実行していない検査を通ったと断定しないでください。失敗例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。
