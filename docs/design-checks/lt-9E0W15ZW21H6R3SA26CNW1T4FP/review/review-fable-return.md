<!-- 設計のレビュー(直した後の設計 — design.md と 74ebb50b6..46fc8f756 の差分)の返答。Agent tool の subagent(subagent_type general-purpose・model 引数 fable = Fable 5.1)・agentId a4118c3dd54638429・2026-10-12・564,128 ms・tool の使用 31 回。加工していない(tool の返す字下げだけを外した)。採る・採らないは ../design.md の §8b。 -->

設計文書(§2・§3・§4b・§7・§9)と、74ebb50b6 → 46fc8f756 の差分、周辺の実装(resource_policy・declare・sim/local・cluster_policy・detached_policy・detached_rules・service_build・system_form・state_json・durable_kv・replies)と検 2 file を読みました。file は書き換えていません。検・変異は実行していないので、「確かめた」は code と検の assert を読んで判じた事、「推測」は読みから予想した事です。

## 1. 「1 つの組を宣言する系は 1 つ」(資源の口の 409)の穴

### 指摘 1-a: 持ち主の鍵 `system` は名乗りで、同じ名を名乗る 2 つの宣言の道具を区別しない(確かめた)

- 根拠: `coordinator/core/resource_policy.hy:916-919` は `limit.system != current.system` だけを比べる。系の名を coordinator に登録する表は無く、宣言の行(`shared/entry/service_build.hy:100-110` の rows)にも系の名は載らないので、照らす先が無い。`resource_policy.hy:9-10` の頭注は「名乗りの X-Actor は確かめないので守りにならない」と書いており、`system` も同じ信頼の水準。
- 失敗の仕方: 別 repo・別 branch・別 module に `(defsystem alpha …)` が 2 つ在り(機体の staging の宣言と本番の宣言、または同名の系を写した系)、片方が `llm {limit 1}`・もう片方が `llm {limit 3}` を持つ → どちらの PUT も `system = "alpha"` で通り、盲検 A と同じ「宣言の順で同時数が変わる」が同名の内で残る。
- 設計文書の扱い: §4b の限界は「DELETE してから作れば移る」だけで、§9 にも名乗りである事が無い。盲検 A の筋書き(別の名の系がうっかり写す)には効く強制で、名を合わせた写しには効かない事を書くべき。

### 指摘 1-b: DELETE に持ち主の照合も版も要らない(確かめた)

- 根拠: `resource_policy.hy:930` の `check-version … :required False`、`resource_policy.hy:962-964` の GroupLimit の分岐は在るかだけを見る。`force` の query(同 file 928 行で読んでいる)も GroupLimit には効かない。
- 失敗の仕方: beta の宣言が 409 で止まった後、誰か(人・道具)が `DELETE /resources/GroupLimit/llm` を版も force も無しに送る → 204 相当で消え、次の beta の宣言が POST(`resource_policy.hy:874-876` — 無ければ誰でも作れる)で持ち主を beta に移す。alpha の置いている task は止まらず、待っている task は TASK-UNDECLARED → beta の値 3 で数え直される。
- 設計との差: §3「持ち主を移すのは、運用者が DELETE してから作り直す時だけ」の「運用者」は code に無い。宣言の道具は DELETE を出さない(`declare.hy:122-130` の docstring「系に無くなった組の資源は消さない」)ので、今は手の操作だけがこの道を通るが、強制ではなく約束。

### 指摘 1-c: 「写した系の宣言が止まる」のは後に宣言した方で、持ち主になるのは先に宣言した方(確かめた)

- 根拠: POST は無ければ誰でも作る(`resource_policy.hy:874-876`)。持ち主は「先着」。
- 失敗の仕方: beta の書き手が組 llm を写し(盲検 A の前提)、beta を alpha より先に配備する → beta が持ち主になり、組の値を持つべき alpha の宣言が 409 で止まる(alpha の Service も書かれない — `declare.hy:136-137`)。§3 の「その系は組を自分の `:task-limits` に写さない(写すと宣言が 409 で止まる)」は、止まるのが写した側だと読めるが、実際は順で決まる。見える失敗ではあるが、止まる側が逆になりうる事を §3/§9 に書くべき。

### 指摘 1-d: 本番の apply-declaration は 1 つ目の組が 409 でも残りの組を書き続け、sim は最初で止まる(確かめた)

- 根拠: `declare.hy:131-137` — `limits-written False` にした後も for を回し、全部回ってから `return False`。sim は `sim/local.hy:2573` の `answered-body` が 300 以上で raise(`local.hy:1229-1234`)し、最初の 409 で止まる。
- 失敗の仕方: beta の `:task-limits` が `{llm(写し) beta-own(自分の)}` → 本番では llm は 409・beta-own は作られ、Service は書かれず終了 1。sim では beta-own は作られない。害は小さい(beta-own は beta の物)が、§3「宣言の道具(本番と sim)は 409 を失敗として止まる」と §4b の「手続きの食い違いは検の筋書きで見る」に対して、検の筋書きに無い食い違いが 1 つ在る。

### 無しと判じた物

- system の空文字: `detached_rules.hy:89-90` が空を断り、資源の口(`resource_policy.hy:847-851` の checked-group-limit)・保存の読み直し(`state_json.hy:119-126`)・系の構成子(`service_build.hy:63-69`)の 3 か所が同じ規則を通る。`system-of ""` は named の system が "" になり同じ規則で ValueError。
- PUT の 409 の順: 形の検め(400)→ 持ち主(409)→ 版(409)の順(`resource_policy.hy:914-920`)。版が古く持ち主も違う PUT はどちらにせよ 409 で、宣言の道具は GroupLimit の 409 を読み直さない(`declare.hy:104-119` は 1 回だけ)。穴は無い。
- 保存の読み直し: `state_json.hy:237`・`durable_kv.hy:255` がどちらも `group-limit-from-json` → `group-limit-refusal` を通り、system は `group-limit-spec`(`detached_rules.hy:100-104`)で往復する。検 `test_a_declared_group_and_its_system_survive_…` が state file と耐久の鍵の両方を通す。
- 読んでから書くまでの競合: 版の照合が止める。同じ系の 2 つの道具が同時に書けば後の方が 409 で止まり、再試行しない。

## 2. 数えの関数の狭めと欄の集合の固定で「送り手の値が数えに入る」変更を止められるか

結論: **新しい欄を足す形は止める(確かめた)が、既に在る送り手の欄を読む形は止めない(確かめた — 検の assert の対象から)。** 現実的な変更を 1 つ示します。

### 指摘 2-a: 既存の欄 `environ` を重さに読む変更は、固定の検を全部通る

- 変更要求(現実的): 「重い task は升を 2 つ使う。送り手は `:environ {"DOEFF_SLOTS" "2"}` を置くだけ」— 盲検 B の slots と同じ振る舞いを、欄を足さずに作る。
- 実装(3 か所・どれも固定の外):
  1. `coordinator/core/cluster_policy.hy:903-912` の `limit-holdings [tasks groups]` — task の行の dict を受けており、引数は固定されていない。`(for [_ (range (slots-of t))] (hold-limits held t.limits t.worker groups))` と回す。`hold-limits` の signature は変えない。
  2. `cluster_policy.hy:928-940` の `task-misfit [task worker room held groups]` — TaskRecord を受けたまま。`limits-misfit` に渡す前に `held` を `(slots-of task) - 1` 回 `hold-limits` で膨らませる。`limits-misfit` の signature は変えない。
  3. `cluster_policy.hy:1127` の place-tasks の after-hold も同じく回す。
  `slots-of` は `TaskRecord.environ`(`coordinator/intent/cluster_model.hy:1005` — 送り手の `SubmitDetached.environ` → `TaskBody.environ` → `detached_policy.hy:98-103` でそのまま行に載る)から読む。
- 固定の検への当たり: `tests/test_group_limits_owner.hy:523-536` の assert は、TaskLimit・SubmitDetached・TaskBody の欄の名の tuple と、`hold-limits`・`limits-misfit` の引数の名の list だけ。上の変更はどれも変えないので緑のまま。盲検 B の違反(宣言 2・slots 2 の送り手は 1 本、送った順で割れる)がそのまま起きる(推測 — 実行していないが、数えの入口が同じ `held` である事は code で確かめた)。
- 設計の主張との差: §9「task-misfit は task の行を受けるので、そこで送り手の欄を読む変更は、欄を足した時点で SubmitDetached・TaskBody の固定が赤になる事で捉える」は、欄を**足す**時だけ正しい。`environ`・`name`・`needs`・`key`・`priority` は既に送り手の欄で、読む変更は捉えない。§4b 2 行目の限界の欄も同じ前提で書かれている。

### 指摘 2-b: `part` は設計上、送り手の値が升の鍵に入る唯一の欄で、照合されない(確かめた)

- 根拠: `cluster_policy.hy:895-900` の `limit-slot` は perPart の組で `limit.part` を鍵に使う。受け口(`cluster_policy.hy:976-995`)は part の有無だけを宣言と照らし、値は検めない。
- 失敗の仕方: perPart の組 `heavy-caller {limit 2}` に、1 つの呼び手が task ごとに違う part(乱数など)を名乗る → 升が増え、同時数は宣言の値を越える。R5(呼び手ごとの予算)の設計どおりだが、§3 と §9 に「part は送り手の名乗りで、coordinator は呼び手の id と照らさない」が無い。送り手の値を信じる所が 1 つだけ在る事は書くべき。

### 止められる物(確かめた)

- 兄弟の欄を足す形(盲検 B そのもの): `SubmitDetached`・`TaskBody` の tuple の比べが赤(`test_group_limits_owner.hy:525-531`)。`evidence/pin_probe.py.txt` の方法で 4 行が捉える事も読んだ。
- `hold-limits` / `limits-misfit` に引数を足す形: 引数の名の比べが赤(同 533-536)。

## 3. 設計文書の主張と実装の食い違い

- §3「宣言の道具(本番と sim)は 409 を失敗として止まる」: 本番は残りの組を書いてから偽を返す(指摘 1-d)。
- §3「持ち主を移すのは、運用者が DELETE してから作り直す時だけ」: DELETE に照合が無い(指摘 1-b)。§4b の限界の欄「運用者の明示の操作として残す」も同じ。
- §9 の固定の検の限界の文: 既存の欄を読む変更を捉えない(指摘 2-a)。§2 coordinator-placement の「数えの関数は task の行を受けず」は hold-limits・limits-misfit の 2 つに限れば正しいが、`limit-holdings`(tasks の dict を受ける — `cluster_policy.hy:903`)も数えの入口で、固定の外。
- §3「写すと宣言が 409 で止まる」: 止まるのは後に宣言した側(指摘 1-c)。
- §3/§4b「写しが規則と揃う事を検で確かめる(直し A3)」: `test_group_limits_owner.hy:543` の assert は `(| LIMIT-KEYS #{"system"}) = GROUP-LIMIT-KEYS` で、`system_form.LIMIT-KEYS` が将来 `"system"` を含んでも緑(和集合は吸収する)。「書き手は system を書かない」をこの検は確かめていない — 確かめているのは `tests/test_defsystem_form.hy:293`(`"system" "other"` → 受けない欄)。§4b の実行経路の表は両方を挙げているので読み手は辿れるが、owner の検の註(540-541 行)は実態より強く書いてある。`(= (frozenset LIMIT-KEYS) (- GROUP-LIMIT-KEYS #{"system"}))` にすれば 1 行で揃う。
- §3「system の無い旧い行の読み分けは書かない(壊れた行として理由つきで断る)」と §9「本物の cluster では確かめていない」: `state_json.hy:237`・`durable_kv.hy:255` は gfor の中で raise するので、system の無い `grouplimit/<組>` の鍵が 1 つでも在れば coordinator の起動の読み直しが止まる(推測: 呼び手が例外を握らない事は追っていない)。前提「74ebb50b6 の行は配備していない」は文書の主張で、配備の頼みの前に本番の `/state` の groupLimits(または耐久の鍵)が空である事を確かめる 1 行が要る。
- 食い違い無しと判じた物(確かめた): §2 の公開の口の signature と `.pyi`(`cluster_policy.pyi:85-92`)。§3「版は spec が変わった時だけ進む」(`resource_policy.hy:590-594` の row が spec と空の status)。§4b「宣言に無い組を断る」の 2 か所(`cluster_policy.hy:1983-1984`・`detached_policy.hy:77`)。§4b「defsystem の形は静的」(`system_form.hy:41` の LIMIT-KEYS に system が無く 152-158 行で SyntaxError)。§9「組だけの系は defsystem が断る」(`system_form.hy:252-253`)。§7「storage と state-view は変えていない」(差分の stat に無く、`replies.hy:136-141` は group-limit-spec 経由で system が出る)。§7 A2(`declare.hy:139` の `(if declaration.rows …)`)。

## 4. 責務の分け方への異論(既存の仕組みで、より狭く・強くできる所)

- 4-a(指摘 2-a の直し): `task-misfit` を `[limits priority worker room held groups]` に狭め、`limit-holdings` を `#(limits worker)` の列を受ける形にすれば、置き方の判断の関数はどれも TaskRecord を受けなくなり、既存の固定の検(引数の名の比べ)がそのまま効く。TaskRecord から射影する行は place-tasks の 2 か所(`cluster_policy.hy:1093` の呼びと `:1127`)に集まる。§9 の限界を「place-tasks の射影の 2 行だけが task を読む」に縮められる。static な禁止にしたいなら、`.semgrep.yaml` は generic の `pattern-regex` で .hy を既に見ている(`:16-30`・`:333-345`)ので、`cluster_policy.hy` に限って `(hold-limits`・`(limits-misfit` の limits の引数が `task.limits`/`t.limits` 以外なら赤、という規則は書ける(脆いが、既存の経路)。
- 4-b(指摘 1-b の直し): GroupLimit の DELETE に、既存の `force` の query(`resource_policy.hy:928`)か `?system=<持ち主>` の一致を要る。「運用者の明示の操作」を code にする最小の形で、宣言の道具は DELETE を出さないので影響が無い。
- 4-c(指摘 1-a): 名乗りより強い持ち主の照合は、X-Actor を確かめない今の作りでは既存の仕組みに無い(Service の owner も同じ扱い — `resource_policy.hy:9-10`)。異論ではなく、`resource_policy.hy` の頭注と §3 に「system は X-Actor と同じ信頼の水準の名乗りで、別の名の系の写し(盲検 A)を止める物」と書く事を求める。
- 4-d: 責務の表(§2)の分け方そのものには異論無し。limit-rules 1 つを 3 か所が呼ぶ形、declare-writer の判断 1 つ(group-limit-write)、保存が綴りと読みを通す形は、差分で保存の code が変わらなかった事(§7)が裏づけている(確かめた: diff の stat に state_json・durable_kv・replies が無い)。

## 一覧(重さ)

| # | 指摘 | 根拠 | 重さ |
|---|---|---|---|
| 2-a | 固定の検は既存の欄(environ など)を読む変更を止めない。§9・§4b の限界の文が「足す時」だけを前提にしている | cluster_policy.hy:903-912・928-940・1127、test_group_limits_owner.hy:523-536、cluster_model.hy:1005 | **直すべき**(文書の主張は必ず。code は 4-a を推奨) |
| 1-a | `system` は名乗りで、同名の 2 つの宣言の道具は互いに書き直せる(§9 に無い) | resource_policy.hy:916-919・9-10、service_build.hy:100-110 | 記録で足りる(§3・§9・頭注に信頼の水準を書く) |
| 1-b | DELETE に持ち主・版・force の照合が無い。「運用者が」は code に無い | resource_policy.hy:930・962-964 | 記録で足りる(4-b を入れるなら小さい直し) |
| 1-c | 持ち主は先着。写した側が先に配備すると、組を持つべき系の宣言が止まる | resource_policy.hy:874-876、declare.hy:136-137 | 記録で足りる(§3 の文の精度) |
| 1-d | 本番の apply-declaration は 409 の後も残りの組を書き、sim は最初で止まる。§3「止まる」と本番・sim の手続きの差 | declare.hy:131-137、sim/local.hy:2573・1229-1234 | 記録で足りる(揃えるなら本番に break 1 行) |
| 2-b | `part` は送り手の値が升の鍵に入る唯一の欄で照合されない(R5 の設計だが §3/§9 に無い) | cluster_policy.hy:895-900・976-995 | 記録で足りる |
| 3-a | owner の検の system_form の assert は LIMIT-KEYS が system を含んでも緑。実際に守るのは test_defsystem_form.hy:293 | test_group_limits_owner.hy:539-543 | 記録で足りる(assert の 1 行の直しは任意) |
| 3-b | system の無い保存の行が 1 つでも在ると読み直しが raise。「配備していない」は配備の前に本番で確かめる前提 | state_json.hy:237、durable_kv.hy:255 | 記録で足りる(配備の頼みに確かめの 1 行) |
| 4-a | task-misfit・limit-holdings を TaskRecord を受けない形に狭めれば既存の固定がそのまま効く | cluster_policy.hy:903・928・1093・1127 | 記録で足りる(推奨。2-a の code 側) |
| 4-c | 持ち主の照合を名乗りより強くする既存の仕組みは無い | resource_policy.hy:9-10 | 不要(1-a の記録で足りる) |
| 1 無し | POST の 409・system の空文字・PUT の順・保存の往復・読み書きの競合 | 上記「無しと判じた物」 | — |
| 3 無し | §2 の signature・版の進み・受け口 2 か所・defsystem の静的の断り・§7 の storage 不変 | 上記 | — |

WAIT: work — 利用者の次の入力
