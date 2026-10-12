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

## 材料と作業の場所

- 固定した要件・責務・公開契約・将来変更についての主張・検の実体と走らせ方: `/work/toolset/worktrees/doeff-wt-blind-b-eafa/BLIND_INPUT.md`(まず読む)。
  提示する検査 = BLIND_INPUT.md の「検の走らせ方」の検(`packages/doeff-hy/tests/test_bytecode_guard.py`)と、uv.lock を変えた便でそれを選ぶ表
  (root の `pyproject.toml` の `prefix = "uv.lock"` の行・`scripts/run_changed_tests.py` の `select_targets`・`tests/test_contract_table_follows_packages.py`)。
  対象範囲 = Hy の版を上げる便(uv.lock の hy の行の変更)の登記の前の `make test-changed`。
- 実コード: 作業木 `/work/toolset/worktrees/doeff-wt-blind-b-eafa`(doeff の 0de52de264f40cbe43e01d4638e54e0cfa9f9af1・venv は作ってある)。
  この作業木の中は試しに一時に書き換えてよい(終わったら `git -C /work/toolset/worktrees/doeff-wt-blind-b-eafa checkout -- .` で戻す)。
- 自分の作業 dir(仮の wheel・script・log を置く所): `/work/agent-workspaces/a-PX52E1C685CV4A7JJJC2F3A6GD/blind-b/`。
- 他の作業木・共有の checkout(`~/repos/doeff`)は書き換えない。git の歴史と commit の文は読まない。
- Python は `uv run` を通して走らせる(機体の python3 は使わない)。pytest は file か検の名を絞って走らせ、repo 全体は走らせない。
- 返答の最後に、実際に走らせたコマンドとその結末(rc・要約の行)を並べる(走らせていない物は「未実行」と書く)。
