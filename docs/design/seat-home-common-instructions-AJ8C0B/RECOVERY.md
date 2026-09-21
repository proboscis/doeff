# 復元の記録 — この一式は会話の記録から復元したものです(2026-09-21)

## 何が起きたか

- この設計一式は、設計を担当した会話 `c-AJ8C0BK9RF29HQ92ZQ986FXQVT` が k3s の pod
  `agentd-pool-6457fdc788-25m5d`(名前空間 `acp-control`)の `/home/kento/repos/doeff/docs/design/seat-home-common-instructions-AJ8C0B/`
  に **git に入れずに**置いていました(依頼が「code は変えない」だったため)。
- 2026-09-21T05:28:55Z に Deployment `agentd-pool` の入れ替えで新しい pod `agentd-pool-58866c9756-mslgl` が立ち、
  旧 pod は 05:29Z に削除が始まりました。`/home/kento` は永続 volume ではなく(emptyDir と overlay)、file はこの時点で失われました。
  設計の完了報告(05:56Z)は旧 pod の終了猶予の中で書かれたもので、報告の時点では file は在りました。
- 実装を担当する会話は別の機体(会社 Mac `CA-20038667`)に配置されたため、どちらにせよ pod の path は読めない状態でした。

## どう復元したか

- 設計を担当した会話の記録(記録サービス agora-record・`GET /v1/conversations/<会話 id>/events`・636 event・切り捨てなし)を、
  会社 Mac の agentd の主体(principal)で読みました。読める主体は `agora-1` / `agentd` / `acp-migrate` の 3 つです。
- 記録の中の Bash 命令から、file を書いた heredoc(`cat > file <<'MARK' … MARK`)の本文を **実行せずに**抜き出して書き、
  file を編集する python の script だけを sandbox(偽の HOME・作業 dir を対応付け)で再実行しました。
  Claude Code を起こす probe・`ai` 命令・test の実行は再生していません。
- 照合は、担当した会話自身が 05:52Z に取った `evidence/SHA256SUMS.txt`(記録に印字されていたものをそのまま置いた)と byte 単位で行いました。

## 結果

| file | 元の SHA256 との照合 | 備考 |
| --- | --- | --- |
| `blind/A-raw.md` | 一致 |  |
| `blind/B-raw.md` | 一致 |  |
| `blind/input-common.md` | 一致 |  |
| `blind/runs.md` | 一致 |  |
| `claims-before-blind.md` | 一致 |  |
| `counterexamples.md` | 一致 |  |
| `counterexamples/model_runs.log` | 不一致 | 部分復元(最終実行の末尾 25 行) |
| `counterexamples/repro_A_real_substrate.log` | 不一致 | 部分復元(1〜8 行目と「反例 A-1」以降) |
| `counterexamples/repro_A_real_substrate.py` | 一致 |  |
| `counterexamples/verify_B_semgrep.log` | 一致 |  |
| `counterexamples/verify_B_semgrep.sh` | 一致 |  |
| `design.md` | 一致 |  |
| `evidence/body_contract.log` | 一致 |  |
| `evidence/coupling_core_match.log` | 不一致 | 部分復元(最終実行の末尾 30 行) |
| `evidence/coupling_core_match.py` | 一致 |  |
| `evidence/probe_user_layer.log` | 不一致 | 部分復元(実行 1 回目の全文 + 実行 3 回目の 4 節以降) |
| `evidence/probe_user_layer.sh` | 一致 |  |
| `evidence/read_body_contract.sh` | 一致 |  |
| `implementation-request.md` | 一致 |  |
| `model/chain.py` | 一致 |  |
| `model/check_body_contract.py` | 一致 |  |
| `model/test_chain.py` | 一致 |  |
| `model/test_counterexamples.py` | 一致 |  |
| `model/test_scenario_checks.py` | 一致 |  |
| `model/test_violations_are_rejected.py` | 一致 |  |
| `report.json` | 不一致 | 再生(記録に残る生成 script を実行・file の hash を含む欄は元と異なる) |

一致 21 / 全 26(evidence/SHA256SUMS.txt 自体は担い手の目録のコピー)

### 部分復元の file について

- 4 本の log は、記録に「印字された分」しか残っていないため、元と byte では一致しません。各 file の先頭に `### ⚠ 復元注記` の行を入れ、何が残り何が失われたかを書いてあります。
  失われたのは実行時の生の出力(実行時刻の見出し・途中の traceback など)で、設計の判断に使った結論の行は残っています。
- `report.json` は、記録に残っていた生成 script(設計検証の報告を組む python)をそのまま実行して作り直したものです。
  file の hash を含む欄は部分復元の log を指すため、元の `report.json`(sha256 `e20c941d…`)とは一致しません。
  それ以外の欄(module・変更シナリオ・盲検の結果の本文)は script に埋め込まれていたので元と同じです。

## 実施

- 実施した会話: `c-3JYBNJMC2RZTM1S8V43939MP42`(この設計を依頼した側)。会社 Mac `CA-20038667`・2026-09-21T06:1x〜06:3xZ。
- 復元の対象になった設計の依頼: `lt-HZRYH9ST368YKXR2T1Z2W9ZKJ9`(class investigate)。実装の依頼: `lt-JH7GM60YZ02MSGKKNXTDQK79A1`(class dev)。追跡の card: `acp:kanban-issue:ki-62aa1f4e9c9c`。
- 教訓: 計画段の成果物(設計・反例・証拠)は書いた時点で git に入れる。pod の作業 dir は pod の入れ替えで消える。
