# 実装依頼書 N(設計 M1)— 設計検証の本文(盲検の後)

依頼: lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH(親 = 計画の依頼 lt-FPBECBQ3W22ESC92VAMCENR5JR・agora-redesign#639)。
完了の範囲: **実装完了まで**。検査・宣言・振る舞いの検はすべて実装済みで、名指しの実行の記録を `evidence/` に置いた。
本線への取り込みは着地の列(doeff の land queue)で行う。予定のまま残した項目は無い(限界は 7 節)。

| 版 | 中身 |
| --- | --- |
| b7f836b0 | 盲検の前に本線へ入れた版(依頼書 N の受入条件を満たした形)。盲検 A・B の対象 |
| 212a6b20・bbc83bda | 盲検の前に固定した主張(`design-before-blind.md`)・盲検の入力・返答・起動の記録 |
| 5a8cf796 | 盲検の反例 2 つを受けた修正 1(材料の分類の宣言・出所を読み直した行まで辿る)。途中の版の記録 = `evidence/at-5a8cf796/` |
| baa00ba4 | 修正 2(退役の書きへ広げる・競合の振る舞いの検)。`evidence/` 直下の after-fix-* はこの版で取った(branch `wt/639-adr012-design-check-evidence` に残す) |
| 62a5f6ce・1e4e10be | 5a8cf796・baa00ba4 を本線の先端へ rebase した版(range-diff は同一)。先端 1e4e10be で名指しの 9 本と台帳を確かめた(`evidence/after-rebase-named.txt`) |

## 1. 盲検の前に固定したもの

`design-before-blind.md`(sha256 62beca27d0d8b5a9be0b32542c058b98d3693e49f30880e01aff1dcd57059b43)。要点:

- 責務 M1 = R49 の巡回の構造の検査(`sweep-violations`)。持つ知識 = usage・entries・responses・row・key の役を
  呼び先の定義から導く読み方。隠す知識 = 呼びの行の字面・折れ方・註・局所変数の名・引数の数。
- 主張 S1(effects の軸): 呼び先が引数を足す・呼びが引数を足す・keyword の既定値・註・改名では M1 を変えずに緑。
  usage・entries・responses に材料を渡す・呼びを消す・一覧の image で書く・記録ではない鍵で読む・呼び先の役の改名だけが
  役の名を言う失敗文で赤。変更の予想範囲 = W か J だけ(M1・M1c は変えない)。
- 並行(S5)を含む他の 5 軸は適用外とした。並行の理由として「巡回の並行の性質は ifGeneration の CAS で、振る舞いの検が持つ」と書いた。

## 2. 盲検 A・B

起動の記録 = `blind/blind-meta.md`。入力 = `blind/blind-a-input.md`・`blind/blind-b-input.md`、返答 = `blind/blind-a-return.md`・
`blind/blind-b-return.md`(加工なし)。どちらも新しい文脈の subagent(Opus 5.5)で、互いの返答と設計者の自己評価は渡していない。
第 1 候補の codex は、この pod では起動口が断ったので第 2 候補へ落とした(理由は blind-meta.md)。effort は起動口が受け付けず
未指定のまま(既定)。

### 盲検 A の反例(変更の波及)

「手番の終わりにだけ数える材料(model ごとの消費)を `turn-record-ended-status` に足す」変更。主張では W か J だけが変わるはずだが、
M1 の中の禁止の表(`SWEEP-FORBIDDEN-MATERIAL`)に役を足さないと、巡回がその材料を渡しても M1 は緑のまま。
「どの役が手番の memory からしか数えられない材料か」という分類が、材料の出所を知る側ではなく検の中に置かれていた(知識の漏れ)。
既に在る `cache-observation` も表に無く、巡回が渡しても緑だった。さらに、同じ規則の要る書き手(退役の書き `end-retired-records`)は
どの検の範囲にも無い、と指摘した。

### 盲検 B の反例(検を通って責務を破る)

巡回が一覧の行で判断して閉じる status を組み、鍵での読み直しは書く直前の generation を得るためだけに使う、という性能改善の形。
M1 は「書く行が AcpGetRow の答え」「その鍵が turn-record-key-of の答え」しか見ないので緑。既存の振る舞いの検 7 本も緑。
ところが一覧と読み直しの間に別の書き手の ended が着地すると、ended の記録へ 2 度目を書き、手番の終わりが書いた usage を消す。

## 3. 反例の再現(修正前の実測)

| 記録 | 版 | 実行 | 結果 |
| --- | --- | --- | --- |
| `evidence/before-fix-A1-sweep-passes-cache-observation.txt` | bbc83bda(= b7f836b0 と同じ源)+ A1 | 対象の検 | 1 passed(巡回が cache-observation を渡しても緑) |
| `evidence/before-fix-A2-model-usage-role.txt` | bbc83bda + A2 | 対象の検 | 1 passed(呼び先が model-usage を足し巡回が渡しても緑) |
| `evidence/before-fix-B-listed-image.txt` | bbc83bda + B | 対象の検 | 1 passed(一覧の行で判断して組む巡回が緑) |
| `evidence/before-fix-R1-retire-passes-usage.txt` | b7f836b0 + R1 | 対象の検 | 1 passed(退役の書きが usage を渡しても緑) |
| `evidence/race-B-double-write-and-usage-erasure.txt` | baa00ba4(巡回の源は b7f836b0 と同じ)± B | 競合の probe(`evidence/race_b_probe.py`) | patch なし: 書き 1 回・usage は残る。B: 書き 2 回・usage が消える |

patch はすべて `evidence/*.patch` に置いた(A1・A2・B は盲検の返答の差分を作業樹へ当てた形、A2d・P1・U・R1 は設計者が作った形)。

## 4. 修正(責務の変更)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
| --- | --- | --- | --- | --- |
| D(新) | 材料の分類の宣言(`effects.TURN_RECORD_END_ONLY_MATERIALS` / `TURN_RECORD_ROW_MATERIALS`・`TurnEndOnlyMaterial`) | `turn-record-ended-status` のどの引数が手番の終わりの書きだけの材料か(空の値と理由)・行から読める材料か | 検の読み方 | Python の定数 2 つ(役の Python の名 → 分類) |
| M1 | R49 の書き手の構造の検査(`sweep-violations`・`end-only-violations`・読み口の defk 群) | 呼び先・effect の定義から役を導く読み方・束ねを辿って出所を読む読み方 | 呼びの字面・折れ方・註・局所変数の名・引数の数・**材料の分類**(D を読む) | `sweep-violations callees materials sweep-body resolve`・`end-only-violations ended-roles materials forms writer` → 失敗文の列 |
| M1c | M1 の反例の検(`SWEEP-ROLE-CASES` 24 例・`RETIRE-ROLE-CASES` 4 例) | 作り物の呼び先・分類・本体と期待する失敗文 | 本物の綴りと本物の宣言 | 各例の期待する失敗文との完全一致 |
| R(新) | 競合の振る舞いの検(`test-a-sweep-that-loses-the-race-to-another-writer-neither-writes-twice-nor-erases-usage`・`RacingAcp`) | 一覧と読み直しの間に別の書き手を着地させる偽の ACP | 巡回の本体の形 | deftest |
| W | 巡回 `sweep-turn-records`・退役の書き `end-retired-records`(agentd.hy) | 変更なし | | |
| J | `turn-record-ended-status`(judgment.hy) | docstring に分類の置き場を書いただけ | | 引数の並び |
| E | effect `AcpGetRow` / `AcpPutStatus` | 変更なし | | dataclass の欄 |

M1 が確かめること(`sweep-violations` の docstring と同じ):

1. `turn-record-ended-status` の全ての引数が D のちょうど 1 つの分類に在る。分類に在る役は全て引数に在る。
2. 巡回と退役の書きの全ての呼びで、手番の終わりだけの材料の役には D の空の値ちょうど(呼びが 1 つ以上)。
3. `AcpPutStatus` の row は `AcpGetRow` の答えで、その key は `turn-record-key-of` の答え。
4. `AcpPutStatus` の status は `turn-record-ended-status` の答えちょうどで、書く前に他の式で使わない。
5. `turn-record-ended-status` の status は、書く行の `status-object-of` の答え。
6. `turn-record-sweep-verdict` の record は書く行。
7. 巡回が呼ぶ補助の defk の中で `AcpPutStatus` を呼ばない。

束ねの読みは `<-` / `setv` に加えて `let` / `for` / `with` / 内包 / 分割 / 関数の引数を拾い、1 つの呼びの答えと言えない束ねは赤にする
(出所の分からない値を緑にしない)。

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 材料を持たない書き手(巡回・退役)が手番の終わりだけの材料を渡さない | D の宣言を読み、呼びの引数を呼び先の定義から役へ写して比べる ADR の検 | `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の `end-only-violations`・対象の検 | 日次の全体検証(root の pytest の母集団)と変更時の名指しの実行 | 静的。書き手の本体の form の中だけを読む(本体の外で組んだ値は見ない) |
| 分類が呼び先の引数と食い違わない | 同じ検の (1) | 同上 `material-class-violations` | 同上 | 分類の中身(どちらに置くか)が正しいかは人が決める |
| 巡回が読み直した行で判断し・組み・書く | 出所を束ねまで辿る ADR の検 | 同上 `sweep-violations` | 同上 | 役の名(`status-object-of`・`turn-record-key-of` 等の関数名)の改名は「出所が違う」の赤で出る(「役が無い」とは言わない) |
| 一覧と読み直しの間の競合で 2 度目を書かない・usage を消さない | 偽の ACP で競合を作る振る舞いの検 | `packages/doeff-agents/tests/sessionhost_acp_turn_events_deftests.hy` の `RacingAcp` と deftest | 日次の全体検証・変更時の名指しの実行。対象の検が名の在ることを確かめる | 偽の ACP の上。本物の engine の書き手判定は通していない |
| 検そのものが正常例で緑・違反例で意図した文の赤 | 反例の検(作り物の呼び先・分類・本体) | 同じ冊 `test-adr-doe-agents-012-sweep-roles-are-read-from-the-callee-not-the-spelling` | 同上 | 作り物の例が表す形だけ |

型で表せる部分: 材料の分類は frozen の dataclass(`TurnEndOnlyMaterial`)と型の付いた定数で持つ。呼び先の引数の並びは defk が残す
form から読むので、型検査ではなく ADR の検が突き合わせる(Hy の引数の並びと Python の定数を 1 つの型で結ぶ手段が無いため)。

## 6. 修正後の検証(baa00ba4・rebase 後の 1e4e10be)

| id | 対応 | 実行 | 期待 | 記録 |
| --- | --- | --- | --- | --- |
| 正常例 | 冊の 2 本 | 対象の検 + 反例の検(-s) | 2 passed・反例の検の 28 例が期待どおり | `evidence/after-fix-named-two.txt` |
| 正常例 | 振る舞いの検 7 本 | R49 の既存 6 本 + 競合の検 | 7 passed | `evidence/after-fix-behaviour-r49.txt` |
| 正常例 | 本物の源 P1 | 呼び先が model-usage を足して D に宣言し、巡回は渡さない | 対象の検が緑 | `evidence/after-fix-P1-model-usage-declared-not-passed.txt` |
| 違反例 | A1 | 巡回が cache-observation を渡す | 「巡回が cache_observation に … を渡している」 | `evidence/after-fix-A1-sweep-passes-cache-observation.txt` |
| 違反例 | A2 | 呼び先が model-usage を足すが D に宣言しない | 「役 model_usage に材料の分類が無い」 | `evidence/after-fix-A2-model-usage-role.txt` |
| 違反例 | A2d | D に宣言し、巡回が渡す | 「巡回が model_usage に … を渡している」 | `evidence/after-fix-A2d-model-usage-declared-and-passed.txt` |
| 違反例 | B | 一覧の行で判断して組む | 「読み直した行から組んでいない」「読み直した行で判断していない」 | `evidence/after-fix-B-listed-image.txt` |
| 違反例 | B(振る舞い) | 競合の検を B の巡回に当てる | 「ended の記録に巡回が 2 度目を書いた」 | `evidence/after-fix-race-test-with-B.txt` |
| 違反例 | U(受入条件 3) | 巡回の呼びの usage を usage-total に | 「巡回が usage に usage-total を渡している」 | `evidence/after-fix-U-usage-total.txt` |
| 違反例 | R1 | 退役の書きが usage を渡す | 「退役の書きが usage に usage-total を渡している」 | `evidence/after-fix-R1-retire-passes-usage.txt` |
| 正常例 | rebase 後 | 1e4e10be で冊の 2 本 + 振る舞いの検 7 本・台帳 | 9 passed・台帳一致 | `evidence/after-rebase-named.txt` |

上の表の結果はすべて期待どおりだった(正常例は緑、違反例は期待した文ちょうどの赤)。

## 7. 予想と実測の比較・限界

- **S1(effects)**: 予想範囲は「W か J だけ」。盲検 A の変更(手番の終わりだけの材料を足す)では、修正前は J と W に加えて M1 の表と
  M1c の並びを直す必要があった(知識の漏れ)。修正後は J と D(宣言)だけが変わり、M1・M1c は変わらない(P1・A2d で実測)。
  D は修正で新しく置いた責務で、予想範囲を後から広げたのではなく、漏れていた分類の知識の置き場を検の外へ移した結果。
  D の宣言を忘れると A2 のとおり「分類が無い」で赤になるので、黙って緑にはならない。
- **S5(並行)**: 盲検の前は適用外とし、並行の性質は振る舞いの検が持つと書いた。盲検 B は既存の振る舞いの検 7 本を通って
  二重に書いたので、この理由は誤りだった。主張の文は変えずに適用に改め、競合を作る振る舞いの検を足した(修正前の巡回 = 今の巡回で緑・
  B で赤)。
- **残る限界**:
  - M1 は書き手の本体の form を静的に読む。本体の外(別 module の関数)で status を組んで渡す形は、(4) の「答えちょうど」の赤で
    止まるが、補助の defk の中の読み方までは辿らない(書き(`AcpPutStatus`)だけは辿る)。
  - 関数名の改名(`status-object-of` → 別名)は「出所が違う」の赤で出て、「役が無い」とは言わない。失敗文の説明が改名を名指ししない。
  - 競合の検は偽の ACP の上の実行。本物の engine が同じ agentd どうしの二重の書きを断るかは確かめていない。
  - 同じ冊に残る「呼びの字面の部分一致」245 か所(依頼書 N の未確定事項 — 数と場所は依頼への報告に書いた)は今回の範囲外。
- 全体の実行はしていない(日次が行う)。code-quality の変更箇所の検査はこの pod では依存が取れず実行していない。
