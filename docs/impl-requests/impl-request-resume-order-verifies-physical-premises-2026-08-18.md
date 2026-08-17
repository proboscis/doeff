# 実装依頼書 — 再開発注の物理前提を発注時に検査する(resume 全滅の上流原因根治)

発注元 task: route-de257d16f2(fleet-stall-steward v143 起票)
方向宣言: resume-order-verifies-physical-premises(開発ボード登記済み・seat-proposal)

## 結合核

該当。ADR-DOE-AGENTS-011 冒頭の記載どおり、起動 gate と attempt 境界は
ACP core-04(liveness closure)の入口側閉包 + core-13(invocation execution
model)の結合核に属する。さらに ADR-DOE-AGENTS-006(会話の resume / fork 契約)
の admission 面に触れる。ゆえに最上位モデル + 人間審査の車線で、法(不変量)・
反例・テスト・実装を 1 まとめで出荷する。

## 確定した決定

1. 根因(実測で確定・2026-08-18 調査):
   - ACP が再開発注する時点で蘇生元の work_dir(`.acp/workspaces/inv_wi_*`)が
     既に削除されている。
   - doeff-agents は work_dir の実在を検査せず tmux に渡し、tmux は不在
     start-directory を黙って $HOME に差し替える。
   - `claude --resume <uuid>` は cwd を鍵に会話を探すため(transcript の家 =
     `projects/<mangle(realpath cwd)>/`)、transplant が正しく敷設した transcript が
     見えず `No conversation found with session ID` で rc=1 即死。
   - 起動 gate はこの画面に分類名を持たず、120 秒待って no-agent-frame /
     prompt_undelivered で終端する。
   - 証拠: agentd.sqlite の resumed_from_session_id 非 NULL 105 行(done 0)。
     no-agent-frame 失敗 91 行の全件で証拠 frame に「HOME の shell prompt +
     No conversation found」を確認。work_dir 実在の再開 5 行は provider-limit
     という別系失敗で、work_dir が在れば会話解決は成立する(gate 通過 2 行あり)。
2. 修理の向き = 発注時の入場検査で fail-loud(typed reject)。silent な
   新規会話への縮退は採らない(resume の意味論に反し、owner 指示の
   fallback 禁止にも反する。継ぐか否かの政策判定は ACP engine 側の家 —
   方向宣言 resume-on-failover-a64d9d と整合)。
3. 検査は 2 点: (a) work_dir の実在(新 error_code `workdir_not_found`)、
   (b) 会話 transcript の解決可能性(same-home でも検査 — 既存 code
   `transcript_not_discoverable` を使う。cross-home は既存 transplant 検査)。
4. 防御の第 2 層: 起動 gate の閉語彙に `conversation-not-found` class を追加し、
   CLI の会話解決失敗画面(claude / codex の逐語)を予算を待たず即 loud に
   分類する(単なる時間短縮ではなく分類の正直化 — TOCTOU と未知経路の備え)。
5. launch 側にも work_dir 実在の入場検査を足す(resume は launch program を
   再利用するため backstop になり、fresh launch の同型崩壊も塞ぐ)。

## 未確定事項

- codex の same-home 検査は conversation_json に rollout_path が記帳されている
  行に限る(無い行は CLI の自 home 走査で解決し得るため検査せず通す)。
- ACP 側(別 repo)の上流欠陥 — workspace を削除した invocation へ resume
  failover を発注する lifecycle 不整合 — は本依頼の範囲外。typed reject を
  受けた engine の扱いは ACP 側の票(attend-identity-consumed 系)と
  resume-on-failover-a64d9d 宣言の管轄。報告で明示する。

## 手順

1. 赤(失敗するテスト)を先に書く: resume の workdir_not_found reject /
   launch の work_dir 不在 loud reject / same-home transcript 不在 reject /
   gate の conversation-not-found 即時分類。既存 pin(same-home は FS 検査
   なし)は契約改訂として反転する。
2. effects.hy に語彙(RESUME-ERR-WORKDIR-NOT-FOUND)・PaneObservation field・
   FsDirExists / FsFileExists effect、substrate.hy に実 impl、LaunchWorld に
   台本 impl を足す(不活性な配管 — 赤 commit に同梱)。
3. 緑: launch.hy(resume-session / launch-session の入場検査・gate 分岐)、
   policy.hy(閉語彙 + 分類)、impls/markers.hy(marker)、
   impls/claude_code.hy / impls/codex.hy(same-home 検査)。
4. ADR 改訂: defadr_doeff_agents_006 に R10 + 法(反例 = 本 incident)、
   defadr_doeff_agents_011 の閉語彙 rule と埋め込み enforcement を更新。
5. doeff-agents 全テスト + conformance 直接束縛系を実走(land gate は
   doeff-agents を検証しないため席側で明示実行)。

## 受入条件

1. 蘇生元 work_dir 不在の session.resume / session.fork が、副作用ゼロ
   (行不生成・tmux 不接触・symlink 不敷設)で error_code `workdir_not_found` の
   typed reject になる。
2. work_dir 不在の session.launch が side effect より前に loud に失敗する。
3. same-home の resume で transcript(claude)/ 記帳済み rollout(codex)が
   不在なら `transcript_not_discoverable` の typed reject になる。
4. 起動 gate が会話解決失敗画面を観測したら、予算(120s)を待たず
   `[conversation-not-found]` class で終端する。
5. 既存の resume / launch / gate テストが全て green(契約改訂で反転した
   pin は改訂後の意味で green)。`uv run pytest packages/doeff-agents/tests
   -m "not e2e"` green + make lint clean。
6. 本番の故障形の再現(missing workdir + No conversation found 画面)が
   テストとして固定される。

## 著者席

operator(機械鋳造 — 起票面の依頼者 = fleet-stall-steward v143 経由の
route-de257d16f2)。依頼書の執筆 = 本席(task agent・調査実施者)。

## 著者モデル(authorModel)

claude-fable-5
