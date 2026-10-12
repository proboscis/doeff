# 設計検証 lt-9E0W15ZW21H6R3SA26CNW1T4FP — 組の同時数の上限の値の持ち主を系の宣言 1 つにする

## この文書の目的

読み終えた人が次の 4 つを言えるようにする。

- doeff-cluster で、task の同時数の上限の値と範囲を、どの module が持ち、どの module が数え、送り手は何を名乗るか。
- その分け方が、送り手の増加・値の変更・保存先・並行の置き・模擬の 5 つの変更に、どこまで狭く収まるか(盲検の前の主張と実測)。
- 盲検 A・B と設計のレビューが示した失敗例と、その再現・直し・確かめ。
- 残る強制の限界と、配備の前に確かめる事。

## 全体の図(直す前の 74ebb50b6 ⇒ 盲検とレビューを受けて直した後)

```
直す前(74ebb50b6 — 本番の coordinator は 2026-10-12T04:01Z からこの版)
  系 alpha の defsystem :task-limits {"llm" {limit 1}} ──system-of──▶ GroupLimit(group scope limit per-part)
  系 beta  の defsystem :task-limits {"llm" {limit 3}} ──system-of──▶ GroupLimit(同じ組・別の値)
        │ declare.apply-declaration / sim の apply-declaration(判断は declaration_requests.group-limit-write)
        ▼ PUT /resources/GroupLimit/llm(spec が違えば読んだ版で書き直す — 誰の値かは見ない)
  coordinator: resource_policy.update-resource ──▶ ClusterState.group-limits["llm"] = 後に宣言した系の値
        ▼ 拍ごと
  cluster_policy.place-tasks ─▶ task-misfit(task …)・limit-holdings(tasks …)・hold-limits(held task …) — task の行を受ける
        ▲ PUT /detached(SubmitDetached → TaskBody → TaskRecord — 兄弟の欄を足せば・既に在る欄を読めば数えに届く)
  送り手(submit-detached-task :limits #((TaskLimit :group "llm")))

直した後(e3a048ed6〜38739915f)
  系 alpha の defsystem ──system-of(組に系の名を付ける)──▶ GroupLimit(… system "alpha")
  系 beta  の defsystem(組を写すと)──▶ GroupLimit(… system "beta")
        │ 同じ道(spec に system が載る)。本番の宣言の書きも sim も、組の書きが 1 つ断られたらそこで止まる
        ▼ PUT /resources/GroupLimit/llm
  coordinator: resource_policy.update-resource ── 持ち主の在る組を別の系が書けば 409(持ち主の無い組は最初に書いた系が持つ)
        ▼ 拍ごと
  cluster_policy.place-tasks ─▶ placed-claims(tasks)— task の行から #(limits worker) を射影する唯一の所
                              ├▶ limit-holdings(claims groups)─▶ hold-limits(held limits worker groups)
                              └▶ task-misfit(limits priority worker room held groups)─▶ limits-misfit(limits worker held groups)
        ▲ PUT /detached(SubmitDetached・TaskBody・TaskLimit の欄の集合と判断の関数の引数は契約の検で固定)
  送り手(組の名と区分だけを名乗る — 変わらない)
```

§0〜§4b は盲検とレビューの後に直した設計を書く。盲検の前の主張は [design-pre-blind.md](design-pre-blind.md)(sha256 236b93a5c9e77d64b78cc55a44b87fea30c64ce3feab76f1dd733c25b148dc74・2026-10-11T23:41:55Z に固定)にそのまま残し、§4 の主張と予想範囲はそれと同じ文にしている(書き換えていない)。

## §0 完了の範囲と commit

完了の範囲: **実装完了まで**(card ki-b0e8028c8c8d の doeff-cluster の側と、盲検・レビューを受けた直し)。上に載る系の側(送り手の名乗りを組の名だけにし、系の宣言に組を書く)は別の card(子 B ki-ab95a4b49e16)で、この検証の範囲外。配備もこの検証の範囲外(§9 の配備の順を頼みに書く)。

| 段 | doeff の commit | 着地 |
|---|---|---|
| 実装(系の宣言の GroupLimit・資源 GroupLimit・組の名だけの名乗り・宣言の値での数え・/state の groupLimits) | 74ebb50b6c3c5de9a652088d11e16371d954abb1 | L2139(main に在り、本番の coordinator が 2026-10-12T04:01Z からこの版で動く — §9) |
| 盲検の前の主張と盲検の入力の固定・盲検の記録 | bd9f2fc6c・e4a19536c・904bc2c48(branch wt/design-check-lt-9e0w) | この branch の着地で入る |
| 直し(盲検 A・B を受けた — §7) | e3a048ed6774085c10be01e1e0c4c4ef807e8914 | 同上 |
| 保存の往復と送る順の検・再検証の記録 | f72737d13fa0cf552d2c45df7c5154808045bf89 | 同上 |
| 型の宣言(.pyi)の作り直し・変異と回帰の記録 | 46fc8f756b8620370a6e9fb084a46efc722d292c | 同上 |
| レビューを受けた直し(判断の関数が task の行を受けない・本番の宣言の書きの止め — §8b) | bd984a3257575f4ea02b2e654ffee8afae55f2fb | 同上 |
| 本番に在る system の無い組を持ち主の無い組として受ける(§7 A1b) | 38739915fb13cb990cc8cf3a2587bfaebfa65d81 | 同上 |

## §1 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | 組ごとの上限の値と数える範囲は、系の宣言(System の task-limits)1 つが持ち、宣言の保存と受け口で値を検める(1 以上の整数・範囲の名) | card の「何をするか」1 |
| R2 | task の上限の名乗り(TaskLimit)は組の名だけ。値と範囲は名乗らない。移行のための読み分けは書かない | card の「何をするか」2 |
| R3 | 置き方(task-misfit・hold-limits)は候補の task の申告ではなく宣言の値で数える。宣言に無い組を名乗る task は受け口で断る | card の「何をするか」3・受入の条 cb0a9c75f |
| R4 | coordinator の /state に、組ごとの宣言の値と置いている数を出す | card の「何をするか」4・受入の条 c7e6872fe |
| R5 | 呼び手ごとの予算(組の名が呼び手ごとに生まれる)も、送り手が値を送らずに持てる | 親の決め R7 と R2・R3 の両立(依頼元へ lt-7DGRE0V0SNF2JVXPY1ATZTARC2 で知らせ、可を得た) |

R1 の「系の宣言 1 つ」は、盲検 A の後に「1 つの組の値を持つ系は 1 つ」と読む事を明らかにした(1 つの coordinator に複数の系が載るのは今の作りの内 — §5)。要件を足したのではなく、R1 の文が守られていなかった所を直した。R2 の「移行のための読み分け」は task の名乗り(TaskLimit)の事で、持ち主の無い組(§7 A1b)は旧い形の読み分けではなく、新しい契約の 1 つの状態(運用者が資源の口に直に作る組も同じ)として扱う。

失敗ケース(実装の前): 送り手が task ごとに (組・値・範囲) を名乗り、coordinator は置く task 自身の申告と升の数を比べた。2 本目の送り手が値を上げると 1 本目の上限を越えて置かれ、同じ 2 本でも置いた順で同時数が変わった。

## §2 責務(module)

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| group-declaration | 系が組の上限を宣言する(System の task-limits・defsystem の頭の `:task-limits`・構成子 system-of)。system-of が組に宣言する系の名を付ける | 組の名 → 範囲・上限の値・区分ごとか(perPart)・宣言した系(system) | 宣言の行の形(資源への書きは declare-writer) | `GroupLimit`(group・scope・limit・per-part・system)・`System.task-limits`・`system-of :task-limits`・defsystem の `:task-limits {"<組>" {"scope" … "limit" … "perPart" …}}`(書き手は system を書かない) |
| limit-rules | 組の宣言の形と task の名乗りの形の規則と JSON の綴り(純粋)。系の構成子・coordinator の受け口・保存の読み直しが同じ規則を読む | 範囲の名(TASK-LIMIT-SCOPES)・1 以上・宣言の欄の閉じた集合(GROUP-LIMIT-KEYS = scope・limit・perPart・system — system は任意で、在れば空でない文字列)・名乗りの欄の閉じた集合(group・part) | JSON の欄の綴り | `group-limit-refusal`・`group-limit-of`・`group-limit-spec`・`task-limit-refusal`・`task-limits-refusal`・`task-limits-of`・`task-limits-json` |
| declare-writer | 宣言を coordinator の資源の口へ差分だけ書く(本番の declare と sim-cluster)。job の無い宣言(組だけ)も書ける。組の書きが 1 つ断られたらそこで止まる | 資源 GroupLimit の作り・版つきの書き直しの本文 | HTTP の要求 | `apply-declaration`・`group-limit-write` |
| coordinator-resource | coordinator が宣言の値を資源 GroupLimit として持つ(作る・版つきの書き直し・消す・一覧・保存と読み直し)。持ち主の在る組への別の系の書き直しを断る | 組の名で引く宣言の表(ClusterState.group-limits — 書き換えない表 Table)と各組の持ち主の系(空 = 持ち主の無い組) | 保存の鍵(grouplimit/<組>)・state file の欄 groupLimits | `/resources/GroupLimit`(spec = {scope limit perPart? system?})・`ClusterState.group-limits` |
| coordinator-placement | task の受け口で名乗りを宣言と照らし、置き方で宣言の値と範囲で数える。task の行から読むのは置いている task の名乗りの射影(placed-claims)と place-tasks の呼びだけで、判断の関数は組の名乗りと宣言の表を受ける | 升の鍵(組・区分・worker)・待ちの理由(TASK-LIMITED・TASK-UNDECLARED) | 升の数の数え方 | `limits-undeclared-refusal`・`limit-slot`・`placed-claims [tasks]`・`limit-holdings [claims groups]`・`hold-limits [held limits worker groups]`・`limits-misfit [limits worker held groups]`・`task-misfit [limits priority worker room held groups]`・受け口 POST /tasks・PUT /detached |
| state-view | 運用者に組ごとの宣言の値・持ち主の系・置いている数を見せる | 画面の形(GroupLimitView・GroupHeld) | — | GET /state の `groupLimits`(group・scope・limit・perPart?・system?・resourceVersion・held・queued) |
| sender | task を送る呼び手(doeff の側の構築関数 submit-detached-task と、上に載る系の送り手) | 自分がどの組の仕事か・区分(呼び手の id など) | — | `TaskLimit(group, part)`(SubmitDetached・受け口の本文 TaskBody の欄の集合は契約の検で固定) |

effect と寿命(報告 JSON の modules に同じ事を書く): group-declaration と limit-rules は純粋(effect なし — module の読み込みの時と受け口の中で呼ばれる値の判断)。declare-writer の effect は HttpRequest と slog だけ(宣言の道具の 1 回の実行の間だけ生きる)。coordinator-resource は coordinator の状態の一部で、保存(state file・耐久の鍵)は coordinator の保存の手続きが持つ。coordinator-placement と state-view は拍ごとの純粋な判断(状態は持たない)。sender は送り手の program の中で SubmitDetached を出す。

## §3 公開契約と所有

- 値と範囲の持ち主は group-declaration だけ。`TaskLimit` の欄は `group` と `part` だけで、送り手は値を持てない(型に欄が無い・本文の欄の集合は閉じている)。
- **1 つの組を宣言する系は 1 つ**(直し A1)。system-of が組に系の名を付け(別の系の名が付いた GroupLimit は断る)、資源 GroupLimit の spec の `system` に載る。coordinator の資源の口は、持ち主の在る組を、今の持ち主と違う系(と system の無い本文)が書き直す PUT を 409 で断る。
- 持ち主は先着: 資源の口の作り(POST)は無い組なら誰の宣言でも作るので、先に宣言した系が持つ。別の系が組を写して先に配備すれば、組を持つべき系の宣言の方が 409 で止まる(どちらにせよ見える失敗 — §9)。
- **持ち主の無い組**(直し A1b): spec に system の無い組(74ebb50b6 の宣言の道具が書いた組・運用者が資源の口に直に作った組)は受け、保存から読み直せる。最初に書き直した系(system を載せた PUT)がその組を持つ。
- `system` は X-Actor と同じ信頼の水準の名乗りで、照らす系の名簿は無い。別の名の系が組を写した時(盲検 A)を止める物で、同じ名を名乗る 2 つの宣言(同名の系の写し)は区別しない。
- 消す(DELETE)は持ち主を照らさない(版も force も要らない)。宣言の道具は組を消さないので、持ち主を移すのは手の操作だけ — 約束であって強制ではない(§9)。
- 別の系の送り手が同じ組を名乗るのはよい(組の名で名乗るだけ)。その系は組を自分の `:task-limits` に写さない。組を持つ系が宣言される前に送ると、受け口が 400 で断る(§9)。
- 宣言の道具(本番と sim)は、組の上限の書きが 1 つ断られたら(409・400)、残りの組も Service の行も書かずに失敗で止まる(直し 1-d)。
- 組の宣言の形の規則は limit-rules の `group-limit-refusal` 1 つ。呼ぶのは system-of(系の値を作る時)・resource_policy の `checked-group-limit`(資源の口)・state_json の `group-limit-from-json`(state file と耐久の鍵の読み直し)。doeff-hy の defsystem の形の読み(system_form)は doeff-cluster を import できないので範囲の名と欄の集合の写しを持ち、写しが規則と揃う事(書き手の欄の集合 = 規則の集合 − system)を検で確かめる(直し A3)。
- 受け口(cluster_policy の submit-task・detached_policy の submit-detached)は、形の規則(task-limits-refusal)の後に `limits-undeclared-refusal` で宣言と照らす: 宣言に無い組・perPart の組で part を名乗らない・perPart でない組で part を名乗る → 400。
- 置き方は升の鍵を `limit-slot` で作る: #(組 区分 worker)。区分は宣言が perPart の時だけ task の part、worker は宣言の範囲が worker の時だけ。比べる値は宣言の limit で、1 本は升ごとに 1 と数える。升の数は拍ごとに置いている task から数え直し、同じ拍で置いた分を足す(hold-limits)。
- `part` は送り手の名乗りで、coordinator は呼び手の id と照らさない。送り手の値が升の鍵に入るのはここだけ(R5 の設計 — 1 つの呼び手が task ごとに違う part を名乗れば升が増える。§9)。
- task の行(TaskRecord — 送り手の欄を全部持つ)から読むのは、置いている task の名乗りの射影 `placed-claims`(#(limits worker) を 1 本に 1 つ)と、place-tasks が待っている task の limits・priority を判断の関数へ渡す呼びだけ。判断の関数(limit-holdings・hold-limits・limits-misfit・task-misfit)は task の行を受けない(直し B とレビュー 2-a — 送り手の他の欄を数えに入れさせない)。
- 送り手の名乗る欄の集合(SubmitDetached・TaskBody・TaskLimit)と判断の関数の引数は契約の検で固定する。欄を足す時・引数を変える時は、組の上限の数えに送り手の値を入れない事を確かめてから、その表を直す。
- 受けた後に宣言から消えた組を名乗る task は、置いている物は止めず、待っている物は TASK-UNDECLARED で待つ。
- 資源 GroupLimit の版は spec が変わった時だけ進む(status は空)。置いている数は /state の groupLimits に出し、版に入れない。
- 保存の旧い形の task の行(値と範囲を名乗る limits)は、まだ終わっていない行を理由つきの failed にする(読み分けて値を捨てて数える道は作らない)。
- 停止・再開・解放: 資源 GroupLimit は宣言の道具が書き、DELETE で消える。coordinator の再起動では保存から読み直す(持ち主の系も往復する)。置いている task の止めは今までどおり(この変更は止めを持たない)。

## §4 変更シナリオと盲検の前の主張

主張と予想範囲は design-pre-blind.md と同じ文(盲検の後に書き換えていない)。実測は §8。

| id | 軸 | 変更 | 主張 | 予想する変更範囲 | 変えない module |
|---|---|---|---|---|---|
| S1 | distribution | 同じ組を名乗る送り手が増える(別の Pod・別の会話・別の環境変数) | 送り手は値を名乗れないので、送り手の数や環境が違っても組の上限は宣言の値 1 つで数えられる。送り手を足すのに doeff の module は変わらない | sender(新しい送り手が組の名を名乗るだけ) | group-declaration・limit-rules・declare-writer・coordinator-resource・coordinator-placement・state-view |
| S2 | effects | 組の値を変える(例 6 → 8)・組を足す | 値の変更と組の追加は系の宣言の literal を変えて宣言し直すだけ。declare-writer が資源 GroupLimit を書き直し、置き方は次の置きから新しい値で数える。送り手と coordinator の code は変わらない | group-declaration(使い手の系の宣言の literal だけ) | limit-rules・declare-writer・coordinator-resource・coordinator-placement・state-view・sender |
| S3 | storage | coordinator の保存先で組の宣言を保存・読み直す(state file と耐久の鍵。保存先を足す時も同じ) | 保存の綴りと読みの検めは limit-rules の group-limit-spec と group-limit-refusal 1 つを通る。保存先を足す時に足すのはその保存先の鍵の組だけで、規則は写さない | coordinator-resource の保存の module だけ | group-declaration・limit-rules・declare-writer・coordinator-placement・state-view・sender |
| S4 | concurrency | 同じ拍に同じ組の task が複数待つ・宣言し直しと置きが重なる・送る順が変わる | 升の数は拍ごとに置いている task から数え直し、同じ拍で置いた分も足すので、同じ拍でも宣言の値を越えない。送る順は値に関わらない(値は宣言だけ)。値を下げても置いている task は止めない | なし(今の置き方のまま守られる) | 全部 |
| S5 | simulation | 宣言から置きまでを sim-cluster(本物の coordinator と worker)で確かめる | sim の宣言の書きは本番の declare と同じ判断 group-limit-write を通り、本物の coordinator の受け口と置き方を通るので、sim の検が本番の道を覆う。足すのは検の file だけ | 検の file だけ | 全部の実装の module |
| X-hw | hardware | 機体ごとに値を変える | 不適用: 親の決め R9 で先送り(long-command が置かれる機体は 1 台)。要る時は GroupLimit に機体の欄を足す公開契約の拡張になる(予想: group-declaration・limit-rules・coordinator-placement) | — | — |

前提: 送り手は doeff-cluster の受け口(POST /tasks・PUT /detached)を通して task を出す。系の宣言は宣言の道具(apply-declaration)で coordinator へ書く。coordinator は 1 つ(複数の coordinator が別々の宣言を持つ形は前提の外)。

## §4b 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 値と範囲の持ち主は宣言だけ(R1・R2) | 型: `TaskLimit` の欄は group と part だけ。受け口の規則: `task-limit-refusal` の欄の閉じた集合 TASK-LIMIT-KEYS(他の欄を 400) | `shared/intent/detached_model.hy` の TaskLimit・`.pyi`・`shared/core/detached_rules.hy` | pytest `tests/test_group_limits_declared.hy::test_a_task_naming_an_undeclared_group_or_sending_a_value_is_refused` | 型の欄の無さは、兄弟の欄を足す変更を止めない(盲検 B)— 下の 2 行で補う |
| 送り手の値を数えに入れない(R2・R3 — 盲検 B・レビュー 2-a) | 型の上の狭め: 判断の関数(limit-holdings・hold-limits・limits-misfit・task-misfit)は TaskRecord を受けず、組の名乗り・優先・worker・置いている数・宣言の表だけを受ける。task の行からの射影は placed-claims 1 つ。契約の検: 送り手の欄の集合(SubmitDetached・TaskBody・TaskLimit)と 5 つの関数の引数の並びを固定。振る舞いの検: placed-claims は environ・名・key・優先を変えても答えが同じ | `coordinator/core/cluster_policy.hy`・`.pyi`・`tests/test_group_limits_owner.hy` の `test_the_sender_facing_fields_and_the_counting_inputs_are_pinned`・`test_the_placed_claims_are_one_per_placed_task_whatever_else_the_sender_sent` | pytest(変えた所の検・4 時間ごとの全体の回) | 検の表を直せば通る(静的な禁止ではない)。placed-claims と place-tasks の呼びの 2 か所は task の行を読むので、そこで既に在る欄(environ など)を読む変更は、振る舞いの検が置いた欄の値を読む時だけ捉える |
| 組を宣言する系は 1 つ(R1 — 盲検 A) | 資源の口の照合: 持ち主の在る組の spec の system が今の持ち主と違う PUT を 409。system-of が系の名を付け、別の名を断る。規則が system を空でない文字列に限る | `coordinator/core/resource_policy.hy` の update-resource・`shared/entry/service_build.hy` の system-of・`shared/core/detached_rules.hy` の group-limit-refusal | `tests/test_group_limits_owner.hy` の 4 本(純粋な資源の口・持ち主の無い組・sim の Redeclare・system-of) | system は名乗り(同名の 2 つの宣言を区別しない)。持ち主は先着。DELETE は照らさない。組の名の空間は coordinator 全体で 1 つ |
| 組の宣言の規則は 1 つ(R1) | 規則の関数 1 つ(group-limit-refusal)を 3 か所が呼ぶ。defsystem の形の読みの写し(範囲の名・欄の集合)は検で規則と比べる | `detached_rules.hy`・`service_build.hy` の system-of・`resource_policy.hy` の checked-group-limit・`state_json.hy` の group-limit-from-json・`doeff-hy/src/doeff_hy/system_form.hy` | `test_a_malformed_group_declaration_is_refused_by_the_resource_intake`・`test_defsystem_form.hy`・`test_the_defsystem_form_reads_the_same_scopes_and_keys_as_the_rule` | 1 以上・「/」の禁止の写しは比べていない(定数でなく式なので) |
| 宣言に無い組を断る(R3) | 受け口の 2 か所で `limits-undeclared-refusal` | `cluster_policy.hy` の submit-task・`detached_policy.hy` の submit-detached | 同上の検 | 3 つ目の受け口を足す時に呼び忘れを止める規則は無い |
| 宣言の値で数える(R3)・同じ拍で越えない(S4) | 升の鍵と比べる値を宣言から引く(`limit-slot`・`limits-misfit` が GroupLimit を受ける)。同じ拍で置いた分を足す | `cluster_policy.hy` の place-tasks | `test_two_senders_naming_one_group_are_counted_by_the_one_declared_value`・`test_the_same_tasks_sent_in_either_order_place_the_declared_number_in_one_tick`・`test_the_per_part_group_counts_each_caller_by_the_declared_value`・`tests/test_task_priority_limits.hy` | — |
| 保存で値と持ち主が往復する(S3) | 保存の綴りと読みは group-limit-spec・group-limit-of を通り、読みは規則で検める | `coordinator/protocol/state_json.hy`・`durable_kv.hy` | `test_a_declared_group_and_its_system_survive_the_state_file_and_the_durable_keys`・`test_an_ownerless_group_is_claimed_by_the_first_system_that_declares_it` | — |
| sim と本番の宣言の書きが同じ(S5) | 同じ判断 group-limit-write を呼ぶ。本番の apply-declaration は job の無い宣言でも書け、組の書きが 1 つ断られたら sim と同じくそこで止まる | `shared/entry/declare.hy`・`sim/local.hy` | `test_the_production_declare_writes_the_limits_of_a_declaration_without_jobs`・`test_the_production_declare_stops_at_the_first_refused_group_like_the_sim`(本番の道を代役の HTTP で)・sim の検 2 本 | HTTP の要求を組む手続きは本番と sim で 2 つ在る(判断は 1 つ)。手続きの食い違いは検の筋書きで見る |
| /state に出す(R4) | 画面の型 GroupLimitView・GroupHeld と綴り(spec は group-limit-spec — system も出る) | `cluster_model.hy`・`cluster_policy.hy` の group-limit-views・`replies.hy` | `test_the_state_shows_each_group_declared_value_and_held_count_on_sim`・`test_the_second_system_declaring_a_group_is_refused_on_sim` | — |
| defsystem の形は静的 | 展開の時の SyntaxError(read-task-limits — 書き手が system を書けば「受けない欄」) | `doeff-hy/src/doeff_hy/system_form.hy` | `test_defsystem_form.hy` の 2 本 | — |
| module の依存の向き | code-quality の module の契約の allow | `.agents/code-quality.d/doeff-cluster-1541.json` | code-quality の fast の段(commit の前の手の実行・日次) | 直しは module の間の import を足していない |
| 業務の語を持たない | semgrep `doeff-packages-have-no-application-vocabulary` | `.semgrep.yaml` | commit の hook・日次 | — |

検査の実行の命令(doeff の根で): `uv run pytest packages/doeff-cluster/tests/test_group_limits_owner.hy packages/doeff-cluster/tests/test_group_limits_declared.hy packages/doeff-cluster/tests/test_task_priority_limits.hy packages/doeff-cluster/tests/test_defsystem_form.hy packages/doeff-cluster/tests/test_declare_apply.hy`。どれも packages/doeff-cluster/tests の下の pytest の file なので、変えた所の検と 4 時間ごとの全体の回から呼ばれる。型の検査(pyright・Hy の型の検査)はこの検証で回していない(未検証 — §9)。

## §5 盲検の結果

起動の記録(入力・識別子・model と effort・機体・時間・fallback の理由)は [blind/blind-meta.md](blind/blind-meta.md)。第 1 候補(codex の gpt-6-astra・low)はこの Pod に codex の資格が無く 401 で落ち、第 2 候補(Agent tool の subagent・Opus 5.5・xhigh)で A・B を別々の新しい文脈に同時に起こした。1 回目は API の週の上限(429)で止まり、会話が起き直した後に 1 回だけ起こし直した。返答は [blind/blind-a-return.md](blind/blind-a-return.md)・[blind/blind-b-return.md](blind/blind-b-return.md)(加工なし)。

### 盲検 A — 変更が複数の責務へ波及する例(S1・S2・S5 に対応)

- 変更要求: 系 alpha が宣言した組 llm を、別に宣言する系 beta の送り手も名乗る。beta の書き手は自分の sim と初回の配備で断られないように、組を自分の `:task-limits` にも写す。
- 主張: 組の値をどの系が持つかを記録する module が無い。declare-writer は「spec が違えば書き直す」と黙って決めるので、後に宣言した系の値が勝ち、宣言の順で同時数が変わる(alpha → beta で 3 本・beta → alpha で 1 本)。写さなければ beta の sim で beta の送り手が 400。組だけの宣言は本番の apply-declaration で IndexError(sim は通る)。付随: system_form が規則の写しを持つ。
- 4 つの区別での読み(設計者の判定):
  - 配線: beta の送り手が組の名を名乗る事自体は S1 の想定どおり。
  - 意図した公開契約の拡張: 資源 GroupLimit に持ち主の欄を足す事は契約の拡張(§7 でこれを入れた)。
  - 共同の不変条件: 「各組を宣言する系は 1 つ」を、どの module も強制していなかった — **欠陥**。
  - 隠すはずの知識の漏洩: 「組の値をどの系が持つか」が使い手の書き手と配備の順へ漏れた — **欠陥**。
  - 本番と sim の分かれ(組だけの宣言の IndexError)— **欠陥**(S5 の主張が崩れた)。

### 盲検 B — 検査を通りながら責務分離を破る例(S1・S2 に対応)

- 実装: 送り手が組の枠を何本分使うか(Airflow の pool_slots の形の `slots`)を、SubmitDetached・TaskBody・TaskRecord の兄弟の欄として足し、hold-limits を `(+ held task.slots)`、task-misfit を `(> (+ held task.slots) limit)` に変えた(13 file)。
- 主張された検査の通過: 提示の pytest 24 passed・semgrep 0 findings・doeff-linter は前後で同じ 1 行(前からの越え)・code-quality は 3 組に分けて passed。
- 違反: 効く同時数が「宣言の値 ÷ 送り手の slots」になり、同じ 3 本でも送った順で 2 本と 1 本に割れ、宣言の値を上げても slots の送り手には半分しか届かず、/state の count の意味が黙って変わる。R2・§3・S1・S2 の主張を破る。
- 4 つの区別での読み: 送り手の値が数えに入るのは**隠すはずの知識(上限の値)の漏洩**。型の欄の無さ(TaskLimit)と閉じた欄の集合(TASK-LIMIT-KEYS)は無傷のまま、兄弟の欄から値が入った — §4b の「型に欄が無い」は強制として足りなかった。

## §6 再現(設計者が実際に試した)

| 失敗例 | 命令 | 対象の版 | 終了状態 | 出力の要点 | 記録 |
|---|---|---|---|---|---|
| A(宣言の順で割れる・黙った書き直し・組だけの宣言の IndexError・写さない beta の 400) | 盲検 A の木で `uv run pytest packages/doeff-cluster/tests/test_blind_a_shared_group.hy -s -q` | 74ebb50b6 | rc 0・5 passed(失敗が起きる事を主張する検) | alpha → beta: limit 3・held 3・queued 0。beta → alpha: limit 1・held 1・queued 2。group-limit-write は `{"resourceVersion": 7, "spec": {"scope": "cluster", "limit": 3}}` を返す。組だけの宣言は `IndexError: list index out of range` | [evidence/repro-A-base.log](evidence/repro-A-base.log) |
| B(slots の兄弟の欄で送り手の値が数えに入る) | 盲検 B の木(差分を当てたまま)で提示の検と B の検 | 74ebb50b6 + slots の差分 | rc 0・提示の検 24 passed・B の検 4 passed(13 files changed・28 passed) | 同じ 3 本が送った順で 2 本と 1 本・宣言 2 → 4 で slots 2 の送り手は 2 本 | [evidence/repro-B-base.log](evidence/repro-B-base.log) |

どちらも主張どおりに成立した(提示の検査を通り、違反が振る舞いとして起きる)。

## §7 直し

| # | 直し | 実装箇所 |
|---|---|---|
| A1 | GroupLimit に `system`(宣言した系の名・既定 "")を足し、system-of が付ける(別の名が付いていれば断る)。資源 GroupLimit の spec に `system` を載せ(GROUP-LIMIT-KEYS・group-limit-refusal・group-limit-of・group-limit-spec)、update-resource が持ち主の在る組への別の系の PUT を 409 で断る | `shared/intent/detached_model.hy`・`shared/core/detached_rules.hy`・`shared/entry/service_build.hy`・`coordinator/core/resource_policy.hy`・`.pyi` |
| A1b | system の無い spec を持ち主の無い組として受ける(e3a048ed6〜bd984a325 は system を必須にしていたが、本番の coordinator に system の無い組が 3 つ在ると分かった — §9)。group-limit-spec は持ち主の無い組で欄を置かず、持ち主の無い組は最初に書き直した系が持つ | `shared/core/detached_rules.hy`・`coordinator/core/resource_policy.hy`(38739915f) |
| A2 | 本番の apply-declaration は、job の行が在る時だけ版の識別を読む(組だけの宣言で IndexError にしない) | `shared/entry/declare.hy` |
| A3 | defsystem の形の読み(doeff-hy)の範囲の名と欄の集合が規則と揃う事を検で比べる(書き手の欄 = 規則の欄 − system) | `tests/test_group_limits_owner.hy` |
| B | 判断の関数を狭めた: `hold-limits [held limits worker groups]`・組の比べを `limits-misfit [limits worker held groups]` に分け、レビュー 2-a を受けて `task-misfit [limits priority worker room held groups]`・`limit-holdings [claims groups]` も task の行を受けない形にし、task の行からの射影を `placed-claims [tasks]` 1 つにした。送り手の名乗る欄の集合と 5 つの関数の引数を契約の検で固定 | `coordinator/core/cluster_policy.hy`・`.pyi`・`tests/test_group_limits_owner.hy` |
| 1-d | 本番の apply-declaration は、組の上限の書きが 1 つ断られたら残りを書かずに偽(sim と同じ所で止まる) | `shared/entry/declare.hy`(bd984a325) |

storage(state_json・durable_kv)と state-view(replies)は変えていない。spec の綴りと読みが group-limit-spec・group-limit-of を通るので、spec に system を足しても(任意の欄にしても)保存と画面の code は変わらなかった(S3 の主張の実例)。

検の順: 直しの検 [tests/test_group_limits_owner.hy](../../../packages/doeff-cluster/tests/test_group_limits_owner.hy) を先に書き、直す前の版で狙いの理由で赤になる事を確かめてから直した。
- A1・A2・A3・B: 74ebb50b6 の木に写して 6 本とも赤([evidence/fix-red-on-base.log](evidence/fix-red-on-base.log) — `'GroupLimit' object has no attribute 'system'`・`別の系の書き直しを断らなかった`・sim の refused が None・`IndexError`・hold-limits の引数が task を受ける・欄の集合の食い違い)。
- レビュー 2-a・1-d: 46fc8f756 で 3 本赤([evidence/review-red-before-fix.log](evidence/review-red-before-fix.log) — 2 つ目の組も POST した・placed_claims が無い)。
- A1b: bd984a325 で赤([evidence/ownerless-red-before-fix.log](evidence/ownerless-red-before-fix.log) — `組 llm の system … は空でない文字列: None` で 400)。

最終の版(38739915f)で関係の検 56 passed([evidence/fix-green.log](evidence/fix-green.log))。置き方・資源の口・保存の検 6 file は 46fc8f756 で 161 passed([evidence/regress-placement.log](evidence/regress-placement.log) — その後の直しの道は関係の検 56 本が覆い、6 file は回し直していない)。lint(scripts/lint-doeff-cluster.sh)は変えた file で、cluster_policy.hy と resource_policy.hy の DOEFF172 の基点越え(31 → 32・16 → 17)が 74ebb50b6 と同じ数で、この直しによる増加は無い(前からの越えは card ki-689aa0fb0aaa)。semgrep は変えた file で 0 findings。型の宣言(.pyi)は doeff_hy.static_stub の --check で古い物が無い。

## §8 再検証と、事前の予測との差

### 盲検の失敗例は直した版で止まるか

- A: 盲検 A の検をそのまま直した版で回した(e3a048ed6 と最終の 38739915f の 2 回)。alpha → beta・beta → alpha のどちらも、2 つ目の系の宣言が `409 … GroupLimit llm は系 alpha が宣言した組 — 系 beta は書き直せない` で止まる。組だけの宣言は IndexError にならない。A の検は 4 failed(失敗が起きなくなった)・1 passed(写さない beta の送り手の 400 — 見える失敗として残す・§9)。[evidence/reverify-A-on-fix.log](evidence/reverify-A-on-fix.log)
- B: 盲検 B の差分を直した版(e3a048ed6)に `git apply --3way` で当てた。cluster_policy の 2 hunk は、数えの関数が task の行を受けないので当たらない。攻め手の最小の合わせ方(数えの関数に slots の引数を足す — [evidence/resolve_b_on_fix.py.txt](evidence/resolve_b_on_fix.py.txt))で解くと、B の振る舞いは作れる(B の検 4 passed)が、固定の検が赤になる。固定の各行を 1 つずつ比べると、SubmitDetached・TaskBody・hold-limits・limits-misfit の 4 行がどれも足された `slots` を捉える([evidence/pin_probe.py.txt](evidence/pin_probe.py.txt))。最終の版では task-misfit・limit-holdings も task の行を受けないので、B の task-misfit の hunk は当たる所が無い。B を捉えた 2 つの欄の集合の固定は最終の版でも同じ。[evidence/reverify-B-on-fix.log](evidence/reverify-B-on-fix.log)

### シナリオごとの正常例と失敗例

失敗例(変異)は、実験の木 doeff-wt-ki-b0e8-mut を最終の版(38739915f)にして 1 つずつ入れ、狙いの検を回し、元に戻した([evidence/mutations.py.txt](evidence/mutations.py.txt))。

| シナリオ | 正常例 | 失敗例 |
|---|---|---|
| S1 | 2 つの送り手が同じ組を名乗り、宣言の値 1 で数えられる。置いている task の名乗りの射影は送り手の他の欄で変わらない(fix-green.log) | 送り手の兄弟の欄(盲検 B の slots)を固定の検が捉える(reverify-B-on-fix.log)。基準の版では B の振る舞いが通っていた(repro-B-base.log) |
| S2 | 持ち主の系の宣言し直しで値が変わる(資源の口の書き直し・宣言 1 → 2 で次の置き)。持ち主の無い組は最初に宣言した系が持つ(fix-green.log) | 別の系の書き直しは 409(reverify-A-on-fix.log)。変異 M-S2-owner(持ち主を比べない)で狙いの検 2 本が赤(mutation-M-S2-owner.log)。変異 M-S2-form(形の読みの範囲の名だけを変える)で検が赤(mutation-M-S2-form.log)。基準の版では宣言の順で割れていた(repro-A-base.log) |
| S3 | 組の上限と持ち主の系が state file と耐久の鍵で往復し、持ち主の無い組も読み直せ、壊れた保存の値は理由つきで断る(fix-green.log) | 変異 M-S3-store(耐久の鍵が組を綴らない)で往復の検が赤(mutation-M-S3-store.log)。system を必須にした版では本番に在る形の組を断った(ownerless-red-before-fix.log) |
| S4 | 同じ 3 本をどちらの順で送っても 1 拍で宣言の値の 2 本だけ置く(fix-green.log)。置き方の周りの検 161 本(regress-placement.log) | 変異 M-S4-tick(同じ拍で置いた分を足さない)で順の検と 2 つの送り手の検が赤(mutation-M-S4-tick.log) |
| S5 | sim の検(/state の groupLimits・2 つ目の系の 409)と、本番の declare が組だけの宣言を書き、組の失敗で sim と同じ所で止まる検(fix-green.log) | 変異 M-S5-sim(sim の宣言が組を書かない)で sim の検 2 本が赤(mutation-M-S5-sim.log)。基準の版では本番の declare の検が IndexError で赤(fix-red-on-base.log)。46fc8f756 では本番の宣言が断られた後も残りの組を書いた(review-red-before-fix.log) |

変異 M-S2-owner は、f72737d13 での 1 回目に、変異した module の組み立てが負荷で時間切れになり(HyMacroExpansionError・pytest-timeout)、狙いの理由の赤ではなかった。時間の上限を広げて回し直し、最終の版でも回し直した。記録は最終の版の物(狙いの理由: `別の系の書き直しを断らなかった`・sim の refused が None)。

### 予想範囲と実測範囲の差

| シナリオ | 予想 | 実測(直した設計で、そのシナリオの変更をした時) | 差の原因 |
|---|---|---|---|
| S1 | sender | sender。ただし送り手が組を持つ系の外に在る時は、その系の sim の検が持ち主の系も宣言する(Redeclare)必要があり、配備は持ち主の系を先に宣言する | 盲検 A。基準の版では送り手の系が組を写し(group-declaration の literal が 2 つ)、値が宣言の順で割れた — 共同の不変条件の欠けと知識の漏洩。直した後は写しが 409 で断られ、doeff の module は変わらない。順の依存は 400 で見える形で残る(§9)。盲検 B とレビュー 2-a は、送り手の値を数えに入れる実装が検査を通る事を示した — 判断の関数を task の行を受けない形に狭めて直した |
| S2 | group-declaration の literal | group-declaration の literal 1 つ(持ち主の系の物だけ) | 盲検 A。基準の版では写しの数だけ literal があり、片方を変えると値が往復した。直した後は 2 つ目の literal を宣言できない |
| S3 | coordinator-resource の保存の module | 保存の鍵の組だけ。spec に system を足した直し(必須にした時も任意にした時も)で保存の code は変わらなかった | 予想どおり |
| S4 | なし | なし(順の検・同じ拍の検が直しの前後で通る) | 予想どおり。盲検 B の順の割れは送り手の値が数えに入った事(S1 の漏洩)で、置き方の並行の扱いの欠けではない |
| S5 | 検の file | 検の file。ただし盲検 A とレビュー 1-d が sim と本番の宣言の書きの分かれ(組だけの宣言・断られた後の続け)を見つけ、declare-writer の本番の側を直した | 判断(group-limit-write)は 1 つでも、HTTP の要求を組む手続きが 2 つ在り、本番の側だけが rows[0] を読み、断られた後も続けていた。直した後、本番の道を代役の HTTP で回す検を 2 本足した |

## §8b 設計のレビュー(直した後の設計)

直した後の設計(この文書と 74ebb50b6..46fc8f756 の差分)を、設計に関わっていない新しい文脈の subagent(Agent tool・model 引数 fable = Fable 5.1・agentId a4118c3dd54638429)に読ませた。返答は [review/review-fable-return.md](review/review-fable-return.md)(加工なし)。採否:

| # | 指摘 | 採否 | どこで |
|---|---|---|---|
| 2-a | 欄の集合の固定は、既に在る送り手の欄(environ など)を数えに読む変更を止めない | 採る(code と文書) | §7 B(task-misfit・limit-holdings を狭め、placed-claims を足し、5 つの関数の引数と射影の振る舞いを検で固定)・§4b・§9 |
| 1-d | 本番の宣言の書きは組の 409 の後も残りを書き、sim は最初で止まる | 採る(code) | §7 1-d・検 `test_the_production_declare_stops_at_the_first_refused_group_like_the_sim` |
| 3-a | system_form の欄の集合の検が、書き手の欄に system が入っても緑 | 採る(検) | §7 A3 |
| 3-b | system の無い保存の行が在ると読み直しが断る。「未配備」は確かめる前提 | 採る — 確かめたら本番に在った(§9)。直し A1b で受ける形にした | §7 A1b・§9 |
| 1-a | system は名乗りで、同名の 2 つの宣言を区別しない | 記録 | §3・§9・resource_policy.hy の頭注 |
| 1-b | DELETE に持ち主・版・force の照合が無い | 記録(強制を足さない — 宣言の道具は消さず、運用者が持ち主を移す道を残す) | §3・§9・頭注 |
| 1-c | 持ち主は先着。写した側が先に配備すると、持つべき系の宣言が止まる | 記録 | §3・§9 |
| 2-b | part は送り手の名乗りで、呼び手の id と照らさない | 記録(R5 の設計どおり) | §3・§9 |
| 4-a | 判断の関数を TaskRecord を受けない形に | 採る(2-a と同じ) | §7 B |
| 4-a の semgrep の規則 | 呼びの引数を正規表現で縛る | 採らない — 脆く(引数の名を替えれば外れる)、引数の固定の検と射影の 1 か所で足りると判じた | — |
| 4-c | 名乗りより強い照合の仕組みは無い | 記録(1-a と同じ) | — |

## §9 限界と残る事・配備の前に確かめる事

- **本番は既に 74ebb50b6 で動いている**: 本番の coordinator(namespace agent-worker・`/state` の coordinatorCommit 74ebb50b6c3c5de9a652088d11e16371d954abb1)には、上に載る系の宣言の道具(createdBy agora-declare)が 2026-10-12T04:01:45Z〜04:19:35Z に書いた system の無い資源 GroupLimit が 3 つ在る(daily-verify・long-command・long-command-conversation — kubectl の API の proxy で `/resources/GroupLimit` を読んだ)。この版(38739915f)はそれらを持ち主の無い組として読み直す(検 `test_an_ownerless_group_is_claimed_by_the_first_system_that_declares_it`)。
- **配備の順**: coordinator(doeff-cluster の系)を先に入れ替え、その後で上に載る系の宣言の道具の doeff の版を上げる。逆の順だと、新しい宣言の道具が送る spec の `system` を 74ebb50b6 の coordinator が「知らない欄」で 400 にして、宣言が止まる。入れ替えの後、74ebb50b6 の宣言の道具は持ち主の無い組を spec が同じと見て書かない(止まらない)。版を上げた宣言の道具の最初の宣言し直しが、3 つの組を自分の系の物にする。その後に古い宣言の道具が書き直そうとすると 409 になる。
- **順の依存(S1)**: 別の系の送り手が組を名乗るには、組を持つ系が先に宣言されている必要がある。宣言の前に送ると受け口が 400(`limits の組 llm は系の宣言に無い`)で断る。黙った割れではなく見える失敗として残す。上に載る系で組を複数の系の送り手が共有するかは確かめていない(盲検 A も未確認とした)。
- **持ち主は名乗り・先着・消しは照らさない**: system は X-Actor と同じ信頼の水準の名乗りで、同じ名を名乗る 2 つの宣言(同名の系の写し)は互いに書き直せる。持ち主は先に宣言した系(写した側が先に配備されれば、組を持つべき系の宣言が 409 で止まる)。DELETE は版も持ち主も照らさないので、消して作り直せば持ち主は移る — 宣言の道具は消さないので、この道は手の操作だけ。
- **part は送り手の名乗り**: perPart の組の区分は送り手が名乗り、coordinator は呼び手の id と照らさない。1 つの呼び手が task ごとに違う part を名乗れば升が増え、組全体の同時数は宣言の値を越える(R5 の設計 — 区分ごとの予算は送り手の協力が前提)。
- **固定の検は静的な禁止ではない**: 送り手の欄の集合と判断の関数の引数の固定は pytest の契約の検で、表を直せば通る。assert の文が「数えに入れない事を確かめてから」と求めるが、その確かめは人の判断。task の行を読む 2 か所(placed-claims と place-tasks の呼び)で既に在る欄を読む変更は、placed-claims の振る舞いの検が置いた欄の値を読む時だけ捉える。
- **規則の写し**: doeff-hy の system_form は範囲の名と欄の集合を写して持つ(doeff-cluster を import できない)。写しの食い違いは検で捉えるが、1 以上・「/」の禁止の写しは比べていない。
- **組だけの系**: defsystem は job の無い系を展開で断る(盲検 A の観測 5 の後半)。組を持つ系は job を持つ系のどれかにする。組だけの系が要る時は defsystem の形の拡張になる。
- **未検証**: 型の検査(pyright・Hy の型の検査)は回していない。全体の検は 4 時間ごとの全体の回に任せた(この検証で回したのは関係の検 56 本と、46fc8f756 での置き方・資源の口・保存の検 161 本)。本物の cluster(実際の HTTP・永続化・再起動)では確かめていない(sim と純粋な判断と代役の HTTP で確かめた。本番の保存の形は資源の口の読みで確かめた)。
- **公開契約の変更の知らせ**: 資源 GroupLimit の spec に system(任意)が載る。上に載る系(子 B ki-ab95a4b49e16)は defsystem の `:task-limits` で書けば system-of が付けるので書き方は変わらない。資源 GroupLimit を直に書く道具が在れば、system を書かなければ持ち主の無い組になる。子 B の card・依頼元・配備の頼みに、上の配備の順と合わせて知らせる。
