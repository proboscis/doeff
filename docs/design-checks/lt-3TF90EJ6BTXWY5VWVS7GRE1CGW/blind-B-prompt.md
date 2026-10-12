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

## 材料

固定済みの要件・責務・公開契約・将来変更についての主張・提示する検査(実体・命令・検出範囲)・実コードの場所は、次の file に全部書いてあります。まずこれを読んでください。
/home/ubuntu/.worktrees/doeff-wt-dc-lt3tf/docs/design-checks/lt-3TF90EJ6BTXWY5VWVS7GRE1CGW/blind-input.md
(sha256 c30f3ced4e4f8202006fdd02ad3653120336e4121a45f62e3b9d5a714bbe1d8f ・ 対象の版 = doeff main の c225cef6f2d688b3d2133a7d88a89a68984becfe)

## 作業の決まり(blind-input.md にも書いてあります)

- 共有の source を書き換えない。実験が要るなら一時 dir(mktemp -d)へ packages/doeff-linter と packages/doeff-indexer を写して、写しの上で行う。
- Rust の build の置き場も一時 dir(CARGO_TARGET_DIR=$(mktemp -d))。CARGO_PROFILE_DEV_OPT_LEVEL=2 CARGO_PROFILE_DEV_DEBUG=0 を付けると速い。binary を走らせる検は DOEFF_LINTER_CHECK_SLOT=1 を付ける。
- 本物の置き場(/work/toolset/cache/doeff-linter・~/.cache/doeff-linter)に触らない。cache の置き場は DOEFF_LINTER_CACHE_DIR=<一時 dir> で替える。
- python は uv run を通す。機体の python3 を直に呼ばない。
- 提示された検査を実際に写しの上で実行して、通る事を確かめてください(実行していない検査は「通った」と書かない)。実験した事と読んだだけの事を、返答で分けて書いてください。
