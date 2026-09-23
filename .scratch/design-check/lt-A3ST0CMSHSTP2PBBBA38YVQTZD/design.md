# 同じ機体の日次の全体検証を 1 本ずつ走らせる — 直列化の所有者を「検証を起こす 1 点」へ移す

- 依頼: `lt-3CXH09FC999PXC6D12RZ9EXZCG`(調査・計画)/ card `acp:kanban-issue:ki-9b728780cfac`
- 書いた会話: `c-PRS4CTCN9F4GZCB6PSS06TZ5PF`(Opus 5.5)・2026-09-23 JST・会社 Mac CA-20038667
- 完了の範囲: **設計まで**(本実装は実装の依頼へ渡す。下の「最小実験」は捨て worktree での実験で、着地しない)
- 基準版: agent-control-plane `064ff5e503f077b66483f1d3d2864f94856f756a` / doeff `89b4285f432ae1f7e74bc334bad8b5e0f1658827`
  (会社 Mac の agentd が今走らせている版 = node 行 `CA-20038667-2` の `spec.agentd.revision` と一致)

## 1. 固定した事実(この設計の入力)

### 1.1 card の赤 3 本は本線で全部直っている

| 赤 | 直した commit | 本線での確認 |
|---|---|---|
| `EngineVocabularySpec`(engine に `"issue"`) | `cef8357bc`(card `ki-cea7291c30be`) | `git grep '"issue"' origin/main -- src/Acp/App/Messaging/Contract.hs` → 0 件 |
| `LogHandlesSpec` 長い行(400/200) | `e9d4ea30d`(上限 2046) | 同 spec の該当検が本線に在り、09-22 の日次で緑 |
| `LogHandlesSpec` 行 buffer(順序依存) | `19f4d1c66`(捕捉の助けが buffer 方式を返す) | `test/Acp/Test/Support.hs:304-310` |

### 1.2 cabal は日次で実際に走っている

`land-verify-acp-29833560`(2026-09-22 03:00 JST・会社 Mac・agentd が起こした)の記録:
`cabal-build: 緑(exit 0・113s)` / `cabal-test: 緑(exit 0・167s)`。28 処理ステージで赤 4(adr-pytest /
semgrep / deploy-drift / cross-repo-contracts — どれも cabal ではない)。

### 1.3 ところが 2026-09-23 は 4 repo の日次が 1 本も測られなかった(実弾)

`~/.local/state/doeff/acp-agentd/verify-runs/` の 4 本の記録(すべて rc 0):

```
land-verify-acp-29835000            予定 03:00  見送り — 別の ai land verify が走行中: pid 93895, 93896, 93905, 93907
land-verify-mediagen-29835090       予定 04:30  見送り — …: pid 93785, 93786, 93895, 93896, 93905, 93907
land-verify-orch-29835190           予定 06:10  見送り — …: pid 93785, 93786, 93869, 93870, 93905, 93907
land-verify-proboscis-ema-29834960  予定 02:20  見送り — …: pid 93785, 93786, 93869, 93870, 93895, 93896
```

経緯(`~/Library/Logs/com.masui.acp-agentd.log`):
- 01:14:31 agentd が起動し、sessionhost の socket を待ち続ける(`still waiting for the sessionhost socket … 6857s`)。
  sessionhost は `undeclared-stopped`(bootout の跡)で 08:15 まで起動されていない(`com.masui.acp-sessionhost.log`)。
- 08:16:36〜39 の **3 秒の間に** agentd が溜まっていた verify の行 4 本を全部 claim し、4 つの script を起こす
  (`verify-command-started` × 4 + `provenance-epoch-sync`)。
- 各 script の `ai land verify --exclusive` が process 表を覗き、互いを見て **4 本とも見送り**(exit 0)。

過去 22 本の記録で排他の見送りはこの 4 本だけ(Mac で日次を回し始めてからの初発)。

### 1.4 今の形(基準版の実コード)

- 起こす側: doeff `packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy`
  - `receive-bound-jobs`(:4152)が自分に結ばれた Bound の行を**行の順に全部** `claim-job` へ渡す。
  - `claim-job`(:1513)は `charter.kind = verify` なら `claim-verify-job`(:3974)へ分岐する。
    `claim-verify-job` は同じ node で走っている verify の数を見ない。**node ごとの同時数の上限は無い。**
  - Running の verify の拾い直し(`recover-command`)は、同じ `receive-bound-jobs` の中で **Bound の claim の後に** 走る。
- 排他: dotfiles `agentcli/src/agentcli/land.py` `cmd_verify` の `--exclusive` 節(:32842)
  - `other_verify_processes()` で process 表を覗き、同じ署名の別の `ai land verify` が 1 本でも在れば見送り、exit 0。
  - 順位も錠も無い「覗いてから判断」なので、同時に始まった N 本は全員が N−1 本を見て全員見送る(1.3)。
  - pod(1 便 1 pod)では他の便が PID 名前空間の外に居て見えず、二重に走る(既存 card `ki-a55542e854c3`)。
- 配置(ACP `src/Acp/App/Scheduling`): verify の行は charter の `place` を名乗る node へ結ばれる。node が `joined`・
  lease が生きていて・`spec.capacity > 0` なら、**agentd が claim しなかった Bound の行はその node に Bound のまま残る**
  (失われる = 別 node へ置き直されるのは `LostNodeGone` / `LostLeaseExpired` / `LostNodeDraining` 等のときだけ —
  `Contract.hs` の `LostReason`)。時間切れで置き直す規則は無い。
- `company` を名乗る node は現在 `CA-20038667-2` の 1 台だけ(capacity 36)。

## 2. 根

**「同じ機体で全体検証は同時に 1 本」という不変量の所有者が居ない。** 起こす側(agentd)は数を見ずに全部起こし、
起こされた各便が自分の中から process 表を覗いて自己判断する。自己判断の排他は順位を持たないので、
同時に起こされると全員見送る(機体)か、互いが見えずに重なる(pod)。どちらも「日次が測れなかった日」を rc 0 で残す。
card が問題にした「cabal を持つ唯一の母集団(日次)が黙って測らない」は、この形で今日も再発した。

## 3. 設計

### 3.1 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開する形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| `verify-claim-verdict` | 同じ node で今 claim してよい verify の行と、待たせる行を決める(純関数) | 同時数の上限 N・走っている verify の job id・Bound の verify 候補の作成時刻 | 行の取得・書き込み・process の起動 | `(verify-claim-verdict in-flight-ids candidates limit) -> VerifyClaimVerdict{claim, hold}` | なし(純関数) | 拍ごとに呼ばれて捨てられる | `len(in-flight ∪ claim) <= N`・claim は作成時刻の古い順・候補は claim か hold のどちらか 1 つにだけ入る |
| `agentd-receive`(`receive-bound-jobs`) | 行を読み、verdict を 1 回呼び、claim の行だけを `claim-job` へ渡す。hold の行は Bound のまま残し log・計器に 1 行 | この node の Bound / Running の行 | 判定の中身 | 既存 | 行の読み・log・計器 | agentd の process | hold の行に書き込まない(phase も condition も変えない) |
| `agentd-verify-arm`(`claim-verify-job` / `recover-command` / 観測) | 1 本の verify を起こし・観測し・終える | script の置き場・rc file | 同時数 | 既存(変えない) | CommandStart・行の書き | 1 本の verify | 既存どおり |
| `agentd-settings` | node の宣言(上限 N)を読む | `agentd.toml` の値 | — | `AgentdSettings.verify-concurrency: int`(既定 1・1 未満は起動で拒否) | 設定の読み | 起動時 | N >= 1 |
| `placement`(ACP) | verify の行を place で node へ結ぶ | node の宣言・lease・capacity | agentd の保留 | 既存(**変えない**) | 行の書き | engine | 未 claim の Bound は live な node に残る |
| `land-verify-exclusive`(dotfiles の `--exclusive`) | 手で起こした走行との重なりの見送り | process 表 | — | 既存(**この変更では変えない**) | ps | 1 走行 | (既知の欠陥 = card `ki-a55542e854c3` が持つ) |

- 「走っている verify」の定義は **memory(`state.commands`)と、この node の Running の verify の行の和**。
  再起動の直後は Running の行の拾い直しが Bound の claim より後に走るので、memory だけで数えると 2 本目を起こす。
  verdict への入力はこの和を `agentd-receive` が組んで渡す(判定は和を受けるだけ)。
- 待たせる順は作成時刻(`created-at-ms`)の古い順。同時刻は行の id の順(決定的)。
- 排水中は既存どおり 1 本も claim しない(verdict は呼ばない)。

### 3.2 公開契約と内部の自由

- 公開契約(実装者が守る): `AgentdSettings.verify-concurrency`(既定 1)・純関数 `verify-claim-verdict` の入出力と
  上の不変条件・「hold の行は書かない」・log / 計器の名 `verify-claim-held`。
- 内部の自由: verdict の返り値の型の綴り、log の文面、hold を毎拍 log するか初回だけか(ただし 1 拍 1 行を超えない)。
- 既存の組み立て点から消える判断: なし(新しく持つ判断が 1 つ増える)。`--exclusive` の覗きは残るが、
  agentd が起こした同じ node の verify どうしでは**もう重ならない**ので、その覗きが見送りを出すのは手で起こした走行と
  重なった時だけになる。覗きの退役は `ki-a55542e854c3` の側で決める(この設計が「座 = 起こす 1 点」を決めたので、
  その card の前提「座をどこへ移すか未定」は解ける)。

### 3.3 変更シナリオと事前の主張(盲検の前に固定)

| id | 軸 | 変える要求 | 主張 | 変わる module | 変わらない module |
|---|---|---|---|---|---|
| S1 | concurrency | 停止の後に溜まった N 本の verify が同じ拍で結ばれている | 同時に起きるのは最大 `verify-concurrency` 本、残りは作成時刻の順に 1 本ずつ起きる。0 本測定にはならない | `verify-claim-verdict`・`agentd-receive` | `agentd-verify-arm`・`placement`・`land-verify-exclusive` |
| S2 | storage | verify が 1 本走っている間に agentd が再起動する(memory が空) | 拾い直しより前の claim でも、Running の行から走っている 1 本を数えるので 2 本目を起こさない | なし(S1 の実装の性質) | 全部 |
| S3 | hardware | 大きい機体で 2 本並べたい | `agentd.toml` の 1 値だけ変える。code は変えない | なし(宣言だけ) | 全部 |
| S4 | distribution | `company` を名乗る 2 台目の Mac が加わる | 上限は node ごと。配置は変えない。node 間の協調は要らない(機体の資源はその機体のもの) | なし | 全部 |
| S5 | effects | 待たされている事実を ACP の行でも見せたい | agentd が書く condition を 1 つ足す = condition の所有の契約(ACP `docs/contracts/condition-ownership.json`)への**意図した契約拡張**。verdict と配置は変わらない | `agentd-receive`・ACP の契約 1 行 | `verify-claim-verdict`・`placement` |
| S6 | simulation | 同時 4 本・再起動・上限 2 を決定的に再現したい | 既存の fake の世界(`tests/sessionhost_acp_verify_deftests.hy` の `World`・`run-tick`)で新しい fake なしに書ける | テストだけ | 実装 |

主張の前提:
- node ごとに agentd は 1 つ(同じ node 名で 2 つの agentd が同時に claim しない)。claim は CAS なので、
  仮に 2 つ居ても同じ行を 2 度起こすことは無いが、上限は各 agentd の memory ごとになる(前提の外)。
- 配置は、live で capacity > 0 の node に結んだ Bound の行を時間切れで置き直さない(基準版の `LostReason`)。
- 待つ時間は `charter.deadlineSeconds` を消費しない(期限の起点は claim した拍 = `started-ms`)。

### 3.4 強制方法

| 守る責務 | 強制方法 | 実装箇所(予定) | 実行経路 | 限界 |
|---|---|---|---|---|
| 上限を超えて起こさない | 純関数の `:pre/:post` 型と性質の deftest(候補 0〜6 本 × 走行中 0〜2 本 × 上限 1〜3 の全組み合わせで `len(in-flight ∪ claim) <= N`・claim ∩ hold = ∅・claim ∪ hold = 候補) | `judgment.hy` の `verify-claim-verdict`・`tests/sessionhost_acp_verify_deftests.hy` | doeff の焦点走(触った file の deftest) | 呼ばれなければ守られない → 次行 |
| verify の claim は verdict を通る 1 口だけ | fake の世界の振る舞い検: 同時 4 本を 1 拍で結ぶ → `CommandStart` は 1 回。終わらせる → 次の拍で 2 本目(作成時刻の古い方) | 同 deftest | 同上 | 別の関数から `claim-verify-job` を直接呼ぶ経路を足されると抜ける → 構造検(`claim-verify-job` の呼び手は `claim-job` の 1 か所)を同じ file に置く |
| 再起動で数え落とさない | fake の世界: Running の verify 1 本 + Bound 3 本の行、memory 空で 1 拍 → `CommandStart` 0 回・Running は拾い直し | 同 deftest | 同上 | — |
| hold の行に書かない | fake の世界: hold の拍の前後で行の generation と status が同じ | 同 deftest | 同上 | — |
| 上限は宣言 | 設定の読みの検: 既定 1・0 は起動で拒否 | agentd の設定の読みの既存テスト | 同上 | — |

型で表せない条件: 「同じ node に agentd は 1 つ」(前提)。これは node の lease と join の既存の機構が持つ。

## 4. 最小実験の計画(設計段で実行する)

捨て worktree `~/.worktrees/doeff-wt-verify-serial-exp`(doeff `89b4285f`)で:
1. 現状の再現: 同時 4 本の verify を結んで 1 拍 → `CommandStart` が 4 回(今日の実弾と同じ形)。
2. 最小の verdict を入れて: 同じ入力で 1 回・終わらせて次の拍で 1 回・再起動の形で 0 回・上限 2 で 2 回。
3. 盲検 A・B の反例を同じ世界で試す。

## 5. 範囲の外(この設計では扱わない・持ち主)

- sessionhost が 7 時間止まっていた件と、agentd の verify の腕が sessionhost の socket を待つ件(verify は sessionhost を使わない)。
- 見送り・測れなかった日が rc 0 で agent-job の結末に写る件(日次の「測れなかった」を誰が読むか)。
- `--exclusive` の覗きの退役(`ki-a55542e854c3`)。
- CI の `haskell` job が打ち切りで 9 割結末を出さない件(card のスレッド c-H89Q… の投稿)。

---

## 6. 盲検と最小実験を受けた改訂(上の 1〜5 は盲検の前の版のまま残す)

盲検 A・B の返答は `evidence/blind-A-reply.md` / `evidence/blind-B-reply.md`(未加工)。実験の記録は `evidence/exp*.txt`、
プロトタイプの差分は `evidence/prototype-89b4285f.diff`(捨て worktree `~/.worktrees/doeff-wt-verify-serial-exp`・着地しない)。

### 6.1 反例ごとの判定

| 反例 | 出所 | 実測 | 判定 | 改訂 |
|---|---|---|---|---|
| 上限を 2 にすると、dotfiles の `--exclusive` の覗きが「上限 1」をもう 1 か所に持っているので、同じ拍に起きた 2 本が互いを見て両方見送る(S3 の「設定 1 値だけ」が偽) | 盲検 A | `exp1` 1a: 署名を持つ process 2 本を同時に起こし、実物の `other_verify_processes()` を呼ぶ → 2 本とも見送り。1b: 直列なら 2 本とも走る | **成立** | 改訂 1 |
| 覗きは部分文字列の一致なので、日次と無関係な process(命令の文字列に `ai land verify` を含む shell・grep)が在るだけで日次が見送る | 最小実験の途中で観測(私の shell が一致した) | `exp1` 1c: `bash -c "sleep 4; true # grep -rn 'ai land verify' …"` が在ると覗きは見送り | **成立** | 改訂 1 |
| agentd が lease の猶予を超えて止まると node の行が新しい化身に替わり、配置の `lostRunner` が Running の verify も失われた扱いで置き直す。verify の process は agentd の停止を越えて走り続けるので、新しい agentd が同じ job の 2 本目を起こす(「行から数える」では覆えない) | 盲検 A | ACP `Decide.hs` `lostRunner`: `nodeState /= "joined"` は phase を問わず `LostNodeGone`。`exp2-3` EXP-REPLACED: 基準版で `CommandStart` 2 回・古い pid 生存 | **成立**(基準版に既に在る穴) | 改訂 2 |
| 判定を verify の腕の中で候補 1 本ずつ呼ぶ実装だと、順序が行の鍵の順(= CronJob の名前の綴り)で決まる。3.4 の検は、テストが id を作成時刻の順に置けば通ってしまう | 盲検 B | `exp5`: プロトタイプを B の形へ変えると、鍵の順と作成時刻の順を分けて置いた同時 4 本の検が「最古(鍵の順では最後)から 1 本だけ起こしていない」で落ち、上限 2 の検も落ちる | **成立**(検の書き方しだいで抜ける) | 改訂 3 |
| 綴りの誤った id・script の無い id の行まで上限の枠に数えると、断るはずの 2 本目が 1 拍待たされる(既存の検 `test-unknown-or-missing-verify-id-is-refused-without-starting-anything` が赤) | 最小実験 | `exp4a`: `AssertionError: vj-none` | **成立** | 改訂 4 |
| 観察(A): `kind = verify` には `ai land verify` を呼ばない便(`provenance-epoch-sync`)も混ざる | 盲検 A | — | 反例ではない | 決定 5 |

### 6.2 改訂

1. **上限の定義は agentd の 1 か所だけにする。dotfiles の `--exclusive` の覗きは退役させる。** 12 本の wrapper
   (`cron_management/land-verify-*.sh` のうち `--exclusive` を渡す全部)から旗を外し、`land.py` の `--exclusive` 節と
   `other_verify_processes()` は呼び手が 0 になった時点で消す。覗きは「手で起こした走行との重なり」の対策ではなく、
   同じ不変量の 2 つ目の実装で、壊れ方が 3 つある(同時なら全員見送り・pod では他の便が見えず重なる = card
   `ki-a55542e854c3`・無関係な語句で見送り)。退役で失うもの: 手で起こした `ai land verify` と日次の重なりの見送り
   (人が起こす走行の責任は人に在る — 受け入れる)。launchd の日次は 2026-09-13 に全部退役済み
   (`cron_management/launchd_roles.json`)で、この Mac で日次の script を起こすのは agentd だけ。
2. **この機体で生きている自分の verify の命令は、起こし直さず引き取る。** 判定の前に、Bound の verify 候補ごとに
   その job の pid の file が生きた pid を指し rc の file が無いかを確かめ(既存の effect `FsReadText` / `CommandProbe`)、
   生きていれば Running と手札を書いて観測を引き継ぐ(`CommandStart` しない)。引き取った 1 本は「走っている verify」に数える。
   ⇒ 「走っている verify」= memory ∪ 自分の Running の verify の行 ∪ 引き取った命令。node の化身に依らない
   (pid の file はこの機体の verify の置き場に在る)。
3. **判定は受けの 1 点で拍ごとに 1 回、全候補で呼ぶ。検は鍵の順と作成時刻の順を分けて置く。**
   性質の検に「claim は (作成時刻, id) の順の先頭から上限の空きだけ」を足す。振る舞いの検は 09-23 の 4 本の runKey
   (鍵の順 acp < mediagen < orch < proboscis-ema・作成時刻の順 proboscis-ema < acp < mediagen < orch)をそのまま使う。
   構造の検: `verify-claim-verdict` の呼び手は `receive-bound-jobs` の側の 1 か所、`claim-verify-job` は上限の設定を読まない。
4. **資格は上限の前に判じる。** 綴り(`verify-plan-of` が文字列を返す)・script の在処(`FsFileExists`)・置き場
   (`place-mismatch`)で断る行は枠を使わず、同じ拍の `claim-job` で閉じる(配置の側の原則「資格が空きより先」
   — card `ki-946438de136a` と同じ形)。
5. **決定: 上限は `kind = verify` の命令すべてを数える。** agentd は script が何をするかを知らない(知ると script の中身の
   知識が agentd へ漏れる)。`provenance-epoch-sync` も 1 本として数える。

### 6.3 改訂後の主張と実測(プロトタイプ・捨て worktree)

| シナリオ | 改訂後の主張 | 正常例(実測) | 反例(実測) |
|---|---|---|---|
| S1 同時 N 本 | 最古から上限の本数だけ起き、残りは Bound のまま・書かれない | `exp4` PROTO-BURST: 1 拍目 `['land-verify-proboscis-ema.sh']`・他 3 本は Bound で generation 不変・終わらせた後 `…, 'land-verify-acp.sh'` | `exp2-3` EXP-BURST(基準版): 1 拍で 4 本 / `exp5`(B の形): 鍵の順の `acp` を起こして検が赤 |
| S2 再起動・化身の交代 | Running の行と生きた pid から数え、同じ job の 2 本目も別の日次も起こさない | `exp4` PROTO-RESTART: 1 本のまま / PROTO-REPLACED: 引き取って Running・`CommandStart` 1 回のまま・別の日次は Bound | `exp2-3` EXP-REPLACED(基準版): 同じ job の 2 本目 |
| S3 上限 2 | agentd の設定 1 値 + 覗きの退役(改訂 1)の後は、設定 1 値だけ | `exp4` PROTO-LIMIT2: `['land-verify-proboscis-ema.sh', 'land-verify-acp.sh']` | `exp1` 1a: 覗きが残ると 2 本とも見送り |
| 資格(改訂 4) | 断る行は枠を使わない | `exp4`: 既存の検 `…refused-without-starting-anything` 緑 | `exp4a`: 改訂前は `vj-none` で赤 |

`exp4` の全体: 11 passed / 1 failed。赤の 1 本 `test-lost-command-and-exceeded-deadline-close-the-job-with-a-condition`
(「消えた命令に結末を発明した」)は **基準版 `89b4285f` のままで赤**(`exp0`: 7 passed / 1 failed)で、この変更とは無関係。

### 6.4 実装で置く強制方法(予定 — 設計段では未実装)

| 守る責務 | 規則 | 置き場 | 実行経路 |
|---|---|---|---|
| 上限・分割・順序 | 純関数 `verify-claim-verdict` の性質の検(候補 0〜6 × 走行中 0〜2 × 上限 1〜3・順序の条件を含む) | doeff `judgment.hy`・`tests/sessionhost_acp_verify_deftests.hy` | doeff の焦点走(触った file の検) |
| 受けの 1 点 | 振る舞いの検(09-23 の 4 本・鍵の順 ≠ 作成時刻の順)+ 構造の検(判定の呼び手 1 か所・腕は上限を読まない) | 同 | 同 |
| 化身に依らない数え方 | 振る舞いの検 PROTO-RESTART / PROTO-REPLACED の形 | 同 | 同 |
| 資格が先 | 既存の検 `…refused-without-starting-anything` を上限 1 のまま緑に保つ | 同 | 同 |
| 定義は 1 か所 | dotfiles の検: `cron_management/land-verify-*.sh` のどれも `--exclusive` を渡さない・`land.py` に `other_verify_processes` の呼び手が無い | dotfiles `agentcli/tests` か `cron_management` の既存の検 | dotfiles の焦点走 |

限界: 「同じ node 名に agentd は 1 つ」は前提(node の lease と join の機構が持つ)。`company` を名乗る node が 2 台になり、
置き直された行が別の機体へ結ばれると、元の機体の生きた命令と重なり得る(今日は 1 台なので起きない — その時は配置の側の問題)。
