# 検収の証拠(計画段 c-XYP9ZM3W0GG8GPB4J60GK2AF49・2026-09-22 11:5x JST)

対象 = 実装の依頼 lt-NNYZX3NH6TVWVDZST64CVW1W70 の報告 lt-NG1M2YS3XVD41AX4RG0M5TTVW1。
検収の返事 = lt-5YFN5891YFBH3D0F81WRKB4AJV(kind accept)。

## 断面

- 着地 = doeff `4e4d3eb1e27b89363f685601c41e327e30fbb0cd`(列 doeff/L282 landed 11:04)
- 測った先端 = `origin/main = 4d20a578f757187bd983fd0939608316a1e09c7f`
  (着地の後に 1 便 = `memory_fold_census.py` + その test の新規 2 file +520 行。
   `git diff --stat 4e4d3eb1 origin/main` で、再照準した 5 か所のどれにも当たらないことを確認済み)
- `git merge-base --is-ancestor 4e4d3eb1 origin/main` = YES
- 隔離の作業樹 `~/.worktrees/doeff-wt-adr012-kenshu`(`--detach 4d20a578`)。主 checkout は 1 byte も触っていない
- 走行の口 = 設計の作業樹の venv の python + PYTHONPATH を kenshu の 17 src へ。
  `doeff_agents.__file__` / `doeff_adr.__file__` が kenshu を指すことを実測(実装の報告の註が再現した)

## 実測(すべて当席の手・報告の値の再測)

| 受入 | 命令 | 結果 |
|---|---|---|
| 1 | `pytest <冊> -k "attachment_spelling… or headless_first_turn… or stop_drains… or turn_credential…"` | **4 passed, 55 deselected in 38.22s**(冷)/ 変異の後始末の確認で **17.61s** |
| 2 | `pytest <冊>` | **59 passed in 0.90s** |
| 2 | `pytest tests/test_enforcement_ledger.py` | **1 passed in 0.13s**・`enforcement-ledger.json` の差分 **0 行** |
| 5 | `git show 4e4d3eb1 \| grep ":statement"` | **0 行** |
| 5 | `git diff --stat 4e4d3eb1^ 4e4d3eb1 -- …/sessionhost/` | **空** |
| 5 | `git show --name-only 4e4d3eb1` | 冊 1 file ちょうど |
| 7-1 | `grep -n "spelling-pins-proxy-for-shape" <冊>` | 212 行の 1 行 / `(law spelling-pins` の定義 = **0 件** |
| 7-2 | 再束縛の針の註 | 「元から在った・盲検 B が見つけた・この便で塞いだ」の 3 行が在る |

## 針の噛み具合(当席が壊して撃った 5 件・各件のあと自分の編集の逆当てで復元し `git status --porcelain` が空を確認)

| 壊し方 | 撃った検 | 結果 | 文言 |
|---|---|---|---|
| host.hy に 5 つ目の関所の口 `"session.rehydrate"`(判断は再利用) | turn_credential | **赤 0.47s** | `session_env の関所を呼ぶ口が名簿と違う(R30 (3)・R51 (1))—— … SESSION-ENV-ADMISSION-MOUTHS へ『動詞と、その口が何か』を 1 行宣言する: 実測 ['"join.seat_env"','"session.launch"','"session.rehydrate"','"session.send"','method'] / 名簿 [… 4 つ]` |
| agentd.hy で呼びの後に `(setv text …)` を条件つきで足す(盲検 B) | headless_first_turn | **赤 9.67s** | `agentd.hy が合成した文を呼びの後で作り直している —— 手番の文を組む座は judgment.mail-turn-text-of の 1 点(R16)` |
| worker_loop.hy の `control.draining` → `False` | stop_drains | **赤 10.36s** | `loop は drain の合図を**読んで** settings.draining に写す(R39)—— 定数を書くと level-triggered が壊れ、排水が下ろせない` |
| judgment.hy の resume の名簿を module 定数 `RESUME-CARRIED-KEYS` へ括り出す(継承は保つ・盲検 A) | attachment_spelling | **緑 11.74s** | — 偽陽性が消えている |
| 上の定数から `MESSAGE-ATTACHMENTS-KEY` を落とす(盲検 A + 本物の違反) | attachment_spelling | **赤 13.34s** | `resume の params に添付が乗らない(R31)—— charter の欄を写す名簿へ添付を足す。 実測 None` |

最後の 2 行が対 = 盲検 A の緑は「無条件の緑」ではない(設計の R5 3 行目の実演)。

## 併せて読んだ点

- 針は**既存の** helper `call-args-of`(冊 323 行・1405 / 1414 / 2964 行等でこの便より前から使われている)を
  使い、数える足場を新設していない。
- 関所の口の名簿は冊の 1 か所(`SESSION-ENV-ADMISSION-MOUTHS`)で、針の中に第 2 の名簿が無い。
  赤の文言が次の便の手(「名簿へ 1 行宣言する」)を名指すので、数を書き換える形に戻らない。
- (f) は file を名指さず `acp/` の `*.hy` と `*.py` を両方掃くので、次に loop が移っても落ちない。
- 型検査の 5 件(acp/join.hy)は、この便の変更 file が冊 1 本ちょうどである事実だけで帰属が切れる。

## 未測(1 件だけ・隠さずに置く)

受入 6 の後半 = **明日 03:30 JST の日次の verify.failed から 4 本が消えること**は未来の事象なので測っていない。
測れる半分(先端 4d20a578 で 4 本とも緑)は上の受入 1 の行。日次が赤を返したら計画段が受け止める。
この 1 件のために待ちの処理ステージは作らない(完了 = 本線到達)。
