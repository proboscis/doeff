# 実装依頼書 N(設計 M1)— 盲検の前に固定した実装と主張

依頼: lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH(親 = 計画の依頼 lt-FPBECBQ3W22ESC92VAMCENR5JR・agora-redesign#639)。
対象版: doeff b7f836b01e7a473e5469d2dbb3c65114b4268ec7(本線・着地の列 doeff/L388)。
完了の範囲: 実装完了まで(本線に入った検査と反例の検)。この文書は盲検 A・B を起こす前に書き、sha256 を報告に記録する。

## 固定した要件

1. ADR-DOE-AGENTS-012 の law `turn-records-are-closed-by-the-end-state-not-by-one-write`(R49): 巡回は書く前に記録の行を鍵で
   読み直し、ifGeneration で書く。巡回が閉じた記録に usage を書かない(消費の和は手番の終わりの 1 回だけ)。
2. 依頼書 N の確定した決定: 対象の検査 `test-adr-doe-agents-012-turn-records-are-not-left-to-one-write` のうち巡回の本体の 3 つの
   検査(旧 1554・1556・1558 行 — 呼びの行の字面の部分一致)を、呼び先の定義の引数の並びから役を導いて読む形にする。
   失敗文は役の名で言う。反例の検を同じ冊に足す。
3. 受入条件: 本線で緑・反例の検が正常例で緑と違反例で意図した文の赤・巡回の呼びの usage に値を渡すと赤。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| M1 | R49 の巡回の構造の検査(`sweep-violations` と読み口の defk 群・冊 470〜788 行) | usage・entries・responses・row・key の役を、呼び先(`turn-record-ended-status` の defk の引数の並び・effect `AcpGetRow` / `AcpPutStatus` の dataclass の欄)から導く読み方 | 呼びの行の字面・折れ方・註・局所変数の名・引数の数 | `sweep-violations ended-roles get-roles put-roles sweep-body` → 失敗文の列(空 = 緑) |
| M1c | M1 の反例の検(`SWEEP-ROLE-CASES`・`synthetic-sweep-forms`・冊 1903 行の deftest) | 正常例 5・違反例 7 の作り物の呼び先と巡回の本体・期待する失敗文 | 本物の巡回の綴り | 各例の期待する失敗文の列との完全一致 |
| W | 巡回の腕 `sweep-turn-records`(agentd.hy 5277 行) | 一覧 → 鍵で読み直す → 判断 → 閉じる status を組む → CAS で書く・名乗り | 閉じる status の中身の組み方 | 拍が撃つ defk |
| J | `turn-record-ended-status`(judgment.hy 4945 行) | 手番の終わりの turn-record の status の組み方(entries の追記・usage・responses・conditions) | 欄の綴り | defk の引数の並び |
| E | effect `AcpGetRow` / `AcpPutStatus`(effects.py) | 要求の欄 | 実 I/O | dataclass の欄 |

M1 の effects: なし(defk が残した form を doeff_hy.sexpr の args-of / body-of で読み、effect の dataclass の欄を読むだけ)。寿命: 検査 1 回。

## 事前の主張(変更シナリオ)

- S1(effects・適用): 巡回が turn-record に書く材料が増える変更 — 呼び先が引数を足す(既定値つき・位置の前後どちらも)、
  呼びが引数を足す、keyword で既定値の引数を渡す、呼びの中に註や改行が入る、局所変数を改名する — では M1 を変えずに緑。
  usage・entries・responses に材料を渡す変更、呼びを消す変更、一覧の image で書く変更、記録ではない鍵で読み直す変更、
  呼び先の役を改名する変更だけが、役の名を言う失敗文で赤。変更の予想範囲: W か J だけ(M1・M1c は変えない)。
  前提: 巡回の閉じる status は `turn-record-ended-status` の呼びで組み、`AcpPutStatus :row <読み直した行> :status <組んだ status>` で書く。
- S2(distribution)・S3(hardware)・S4(storage)・S5(concurrency)・S6(simulation): 適用外。M1 は source の form を読む
  静的な検査で、機体・保存・分散・並行・模擬の実行の性質を持たない(検査が読む対象の巡回の並行の性質は ifGeneration の CAS で、
  振る舞いの検 `packages/doeff-agents/tests/sessionhost_acp_turn_events_deftests.hy` が持つ)。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 巡回が usage・entries・responses を書かない・読み直した記録で書く | 巡回の本体の form を呼び先の定義から導いた役で読む ADR の検 | `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の `sweep-violations`・`test-adr-doe-agents-012-turn-records-are-not-left-to-one-write` | 日次の全体検証(root の pytest の母集団)・変更時の名指しの実行 | 静的な検査。巡回の本体(sweep-turn-records の defk の form)の外で組んだ値は見ない |
| 検査そのものが正常例で緑・違反例で意図した文の赤 | 反例の検(作り物の呼び先と本体) | 同じ冊 `test-adr-doe-agents-012-sweep-roles-are-read-from-the-callee-not-the-spelling` | 同上 | 作り物の例が表す形だけ |

## 実行の手順(名指し)

```
uv run pytest "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test_adr_doe_agents_012_turn_records_are_not_left_to_one_write" \
  "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test_adr_doe_agents_012_sweep_roles_are_read_from_the_callee_not_the_spelling" -v -s -p no:cacheprovider
```
