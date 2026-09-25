あなたは静的検査を通過する責務違反の反例を生成してください。
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
実行していない検査を通ったと断定しないでください。反例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。

## 材料(固定済み)

対象 repo: doeff(Python / Hy の effect system)。対象版: b7f836b01e7a473e5469d2dbb3c65114b4268ec7。
あなた専用の読み取り用の作業樹: /home/kento/.worktrees/doeff-wt-639-adr012-blind-b(この版の checkout。この作業樹の中の file は反例を試すために一時的に書き換えてよいが、
commit・push・他の作業樹への書き込みはしない。全数の検査〔引数なしの pytest〕は走らせない)。

### 固定した要件

1. ADR-DOE-AGENTS-012 の law `turn-records-are-closed-by-the-end-state-not-by-one-write`(R49 — 冊 `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の 1221 行):
   agentd の巡回(sweep)は、走っている turn-record のうち対の agent-job が終端か行ごと無い記録を ended にする。巡回は書く前に
   記録の行を鍵で読み直し(一覧の image で書かない)、ifGeneration で書く。巡回が閉じた記録に usage を書かない
   (手番の開始 offset は memory にしか無く、0 から数え直すと温かい session の前の手番の消費まで足す発明になる。消費の和は手番の終わりの 1 回だけ)。
2. 検査の要求: 巡回の構造の検査は、行の字面ではなく、呼び先の定義(defk の引数の並び・effect の dataclass の欄)から導いた
   引数の役で巡回の本体を読む。失敗文は役の名で言う。反例の検を同じ冊に置く。
3. 利用状況: 検査は日次の全体検証(root の pytest の母集団)と、変更時の名指しの実行で走る。

### 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| M1 | R49 の巡回の構造の検査(冊 470〜788 行の `CalleeRoles`・`roles-of-params`・`defk-roles`・`defk-forms`・`effect-roles`・`call-of?`・`calls-in`・`bindings-of`・`all-calls-of?`・`call-roles`・`SWEEP-FORBIDDEN-MATERIAL`・`sweep-violations`) | usage・entries・responses・row・key の役を、呼び先(`turn-record-ended-status` の defk の引数の並び・effect `AcpGetRow` / `AcpPutStatus` の dataclass の欄)から導く読み方 | 呼びの行の字面・折れ方・註・局所変数の名・引数の数 | `sweep-violations ended-roles get-roles put-roles sweep-body` → 失敗文の列(空 = 緑) |
| M1c | M1 の反例の検(冊の `SWEEP-ROLE-CASES`・`synthetic-sweep-forms`・1903 行の deftest) | 作り物の呼び先と巡回の本体・期待する失敗文 | 本物の巡回の綴り | 各例の期待する失敗文の列との完全一致 |
| W | 巡回の腕 `sweep-turn-records`(`packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy` 5277 行) | 一覧 → 鍵で読み直す → 判断 → 閉じる status を組む → CAS で書く・名乗り | 閉じる status の中身の組み方 | 拍が撃つ defk |
| J | `turn-record-ended-status`(同 `judgment.hy` 4945 行) | 手番の終わりの turn-record の status の組み方 | 欄の綴り | defk の引数の並び |
| E | effect `AcpGetRow`(3464 行)/ `AcpPutStatus`(3531 行)(同 `effects.py`) | 要求の欄 | 実 I/O | dataclass の欄 |

M1 の effects: なし(defk が残した form を `doeff_hy.sexpr` の `args-of` / `body-of` で読み、effect の dataclass の欄を読むだけ)。

### 将来の変更についての主張(事前)

- 巡回が turn-record に書く材料が増える変更 — 呼び先が引数を足す(既定値つき・位置の前後どちらも)、呼びが引数を足す、
  keyword で既定値の引数を渡す、呼びの中に註や改行が入る、局所変数を改名する — では M1 を変えずに緑。
- usage・entries・responses に材料を渡す変更、呼びを消す変更、一覧の image で書く変更、記録ではない鍵で読み直す変更、
  呼び先の役を改名する変更だけが、役の名を言う失敗文で赤。変更の予想範囲: W か J だけ(M1・M1c は変えない)。
- 前提: 巡回の閉じる status は `turn-record-ended-status` の呼びで組み、`AcpPutStatus :row <読み直した行> :status <組んだ status>` で書く。
- 機体・保存・分散・並行・模擬の軸は M1 に適用外(M1 は source の form を読む静的な検査。巡回の並行の性質は振る舞いの検
  `packages/doeff-agents/tests/sessionhost_acp_turn_events_deftests.hy` が持つ)。

### 検査の実体・設定・検出範囲

- 検査 1: 冊 1851 行 `test-adr-doe-agents-012-turn-records-are-not-left-to-one-write`(1878〜1886 行が M1 を呼ぶ部分。同じ検の他の行は
  effects / judgment / agentd の綴りの pin で、M1 の範囲外)。
- 検査 2: 冊 1903 行 `test-adr-doe-agents-012-sweep-roles-are-read-from-the-callee-not-the-spelling`(M1c)。
- 検出範囲: `sweep-turn-records` の defk の本体の form だけ。呼び先の役は `turn-record-ended-status` の defk の引数の並びと
  `AcpGetRow` / `AcpPutStatus` の dataclass の欄から導く。
- 実行コマンド(作業樹の root で・初回は uv が .venv を作る):
  `uv run pytest "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test_adr_doe_agents_012_turn_records_are_not_left_to_one_write" "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test_adr_doe_agents_012_sweep_roles_are_read_from_the_callee_not_the_spelling" -v -s -p no:cacheprovider`
- ほかに R49 を守る振る舞いの検(`packages/doeff-agents/tests/sessionhost_acp_turn_events_deftests.hy` の
  `test-a-turn-record-left-running-by-a-refused-write-is-ended-by-the-sweep` など 6 本)が在る。名指しで走らせてよい。
