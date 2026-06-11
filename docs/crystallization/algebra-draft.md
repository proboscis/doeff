# doeff エフェクト代数 第一稿(結晶化議論用ドラフト)

作成: 2026-06-11。事後検死(postmortem.md)・制約グラフ(constraint-graph.md)・現行語彙の棚卸し(45エフェクト型)に基づく。
**用途**: フロンティア級セッションでの結晶化議論(反例攻撃→law確定)の入力。等式は候補であり確定ではない。
規格: プリミティブ5〜10個(直交+完全)/合成則の層明示/law(等式)/インタプリタ分離。

---

## 0. 層構造と核心的発見

### 層A: 制御コア(確定済み — 再交渉しない)

OCaml 5から輸入: `perform` / `match_with`(WithHandler) / `resume`(non-tail) / `transfer`(tail) / `fiber_return` + 転送 `pass`。
確定済みlaw:
- **deep-handler等式**: `handle h (E[perform e]) ≡ h(e, λv. handle h (E[v]))` — 捕獲は境界を含み、resumeはハンドラ再設置状態で再開(B1/K2)
- **線形性**: kは線形資源。`Resume(k,v)`後のkは未定義(one-shot violation)(B2/B3)
- **単一所在**: 継続は任意時点でちょうど1箇所(SPEC-VM-021:282、実行時検査=invariants.md I6)
- エラーチャネル: ホスト例外を`ResumeThrow`/`TransferThrow`で輸入(独自エラー生成元は持たない)

### 核心的発見: 「コアエフェクト」はほぼ空集合である

棚卸しの結果、**VM特権を要するエフェクトは事実上存在しない**:
- Ask/Get/Put/Tell の正統ハンドラは**Pythonクロージャ実装**(doeff-core-effects/handlers.py:27,49,71)。VMの状態設備を使わない
- スケジューラ全族(Spawn/Wait/Gather/Race/Cancel/Promise/Semaphore)は再帰WithHandlerのライブラリ(scheduler.py)
- VMの`global_state`/`writer_log`(var_store.rs:10,12)は**呼び出し元ゼロの死設備**(v1時代の残骸。grep検証済み: ブリッジ・vm-core実装に参照なし)
- VMが提供する生きた状態設備は **cells(Var: DoCtrl AllocVar/ReadVar/WriteVar、do_ctrl.rs:105-111)とスコープ束縛のみ**
- 例外は2つ: **Await**(ドライバ協調が要る外界橋)と**GetHandlers**(イントロスペクションDoCtrl — schedulerとLocal/Listenが依存)

したがってプリミティブの地位は「VM特権の有無」では与えられない。**「独立なlaw系を持つ意味論単位」**として与える(§2)。

## 1. 出現した語彙の全列挙

### 1.1 現行(45型、族別)

| 族 | エフェクト | 実装機構 |
|---|---|---|
| Reader | Ask | クロージャ(env dict) |
| State | Get, Put | クロージャ(store dict) |
| Writer | WriterTellEffect(=Tell), Slog | クロージャ(log list) |
| Error | Try | ライブラリ(get_inner_handlers+WithHandler) |
| Scope | Local, Listen | ライブラリ(ハンドラ連鎖の再設置) |
| Async橋 | Await | 外界橋(await_handler、handlers.py:234+ドライバ) |
| 並行 | Spawn, Wait, Gather, Race, Cancel, TaskCompleted | ライブラリ(再帰WithHandler+タスクキュー) |
| Promise | CreatePromise, CompletePromise, FailPromise, CreateExternalPromise | ライブラリ(同上) |
| Semaphore | CreateSemaphore, AcquireSemaphore, ReleaseSemaphore | ライブラリ(同上) |
| Traverse | Traverse, Reduce, Zip, Inspect, Fail, Skip, SortBy, Take | ライブラリ(逐次+Try分離) |
| Cache | CacheGet/Put/Delete/Exists | ライブラリ(storage proxy) |
| Memo | MemoGet/Put/Delete/Exists | ライブラリ(コスト階層routing) |
| HTTP | HttpRequest | 外界 |
| Time | Delay, WaitUntil, GetTime, ScheduleAt, SetTime | 外界(sync/async/sim 3意味論) |

(孤児エフェクト・死ハンドラなし — 棚卸し結果)

### 1.2 歴史的に削除された語彙(検死より — 「ライブラリ行き判定」の実例集)

| 削除されたもの | 行き先 | 根拠コミット | 削除の論理 |
|---|---|---|---|
| Catch/Recover | Safe → 現Try | `4d79f177` | エラー族の統一 |
| Result/Maybe系エフェクト | 値に降格 | `fedfce85`, `8ecc0a6c` | ドメイン値≠エフェクト |
| Parallel | Spawn/Gather | `9b601375` | 合成で書ける |
| GatherDictEffect | Gather+dict再構成 | `50849848` | 合成で書ける |
| Log | Tell | `3b72228d` | 重複 |
| NeedAsync/NeedParallel | Suspended → Await | `3d0088af` | 継続ベースに統一 |
| Delay/GetTime/WaitUntil | doeff-timeへ追放 | `bb41b5e4` | 外界エフェクトはコア外 |
| Suspend/Scheduledクラス | scheduler効果群 | `cbd2eec5` | エフェクト化 |
| DurableCache | Cache統一→Cache/Memo分離 | `69f06005` | 関心の分離 |
| Delegate | Passのみ | step.rs:258-264 | 転送形の一本化 |

**パターン**: 削除は常に「(a)他の合成で書ける」「(b)値で表せる」「(c)外界はコア外」のいずれかの発見だった。本稿の境界判定基準はこの歴史の明文化である。

## 2. 最小生成元集合への圧縮候補

### 生成元(5個+保留1)

| # | 生成元 | 直交性の根拠 | 状態 |
|---|---|---|---|
| G1 | **Ask**(Reader) | 不変環境への問い。Get/Putで模倣すると不変性lawを失う | 候補確定 |
| G2 | **Get/Put**(State、1ペアで1生成元) | 可変セルの読み書き。Plotkin–Power 4律が閉じる | 候補確定 |
| G3 | **Tell**(Writer) | 追記専用出力。Get/Putで模倣すると非可換追記lawを失う | 候補確定 |
| G4 | **Await** | 外界(async)への唯一の橋。law最薄だが境界として正当(§4) | 候補確定 |
| G5 | **Spawn/Wait**(並行性、1ペアで1生成元) | 制御コアだけでは並行性は出ない(逐次deep handlerのみ)。Gather/Race/Promise/Semaphore/Cancelは導出 | 候補確定 |
| G6? | **Var**(AllocVar/ReadVar/WriteVar) | スコープ付き可変セル。**唯一のVM特権状態** | **保留**(law未確定 — §4-1) |

制御コア5 + 生成元5〜6 = 規格(5〜10個)内。

### 導出物(ライブラリ)の導出経路

- `Try` = エラーチャネル(コア)上のハンドラ
- `Local(e, p)` = `WithHandler(reader(e ⊕ outer_env), p)` + 内側ハンドラ再設置
- `Listen(p, T)` = ローカルWithHandlerで型Tを横取り+Pass
- `Gather(ts)` = `traverse Wait ts`(scheduler内部で導出済み)
- `Race/Cancel/Promise/Semaphore` = Spawn/Wait+キュー状態(scheduler.pyで実証)
- `Cache/Memo` = State law系のstorage上への移送+Try+コストrouting
- `Traverse族` = applicative合成則の具現(§5)
- `Time/HTTP/domain` = 外界エフェクトのパラメトリックな族(Awaitと同格の橋、コア外)

## 3. law候補(生成元ごと)

### G1 Ask
- A1(重複律): `ask k >>= λx. ask k >>= λy. f(x,y) ≡ ask k >>= λx. f(x,x)`
- A2(Local上書き): `local({k:v}, ask k) ≡ pure v`
- A3(Local合成): `local(e₁, local(e₂, p)) ≡ local(e₂ ◁ e₁, p)`(◁=右優先マージ)
- A4(異キー可換): `ask k₁ ⊗ ask k₂` は順序によらない

### G2 Get/Put(Plotkin–Power標準4律+α)
- S1(put-put): `put k v; put k w ≡ put k w`
- S2(put-get): `put k v; get k ≡ put k v; pure v`
- S3(get-put): `get k >>= put k ≡ pure ()`
- S4(get-get): `get k >>= λx. get k >>= λy. f(x,y) ≡ get k >>= λx. f(x,x)`
- S5(異キー可換): `put k₁ v; put k₂ w ≡ put k₂ w; put k₁ v`(k₁≠k₂)
- S6(**状態×継続共有law** — D9でオーナー確定): one-shot再開はput/getの逐次合成と区別不能(捕獲・再開は状態に対して透過)。`perform e; get k`で見える値は、ハンドラがresumeするまでにputした値 — スナップショットは存在しない

### G3 Tell
- W1(準同型): `tell a; tell b ≡ tell (a·b)`(·=ログモノイド追記。**非可換** — 順序が意味を持つことを明記)
- W2(listen抽出): `listen(p ; tell a) ≡ listen(p)のログにa追記`
- W3(非干渉): `tell a; p ≡ p; tell a` は**成立しない**(W1の系。Writerを可換と誤認した最適化を禁止)

### G4 Await
- AW1(単位律): `Await(coro_pure(x)) ≡ pure x`
- AW2(逐次律): `Await(c₁) >>= λx. Await(c₂(x)) ≡ Await(c₁ >>= c₂)`(コルーチンbindとの準同型)
- 注: これ以上のlawは書けない(外界の非決定性)。**外界橋はlawの境界**であり、law-poorはここでは設計曖昧のシグナルでなく境界宣言である

### G5 Spawn/Wait
- C1(往復律・条件付き): `Wait(Spawn(p)) ≡ p` — **ただしpが共有Var/外界に触れない場合に限る**。共有状態下では交換則が成立せず、成立条件の明文化が必要(⚠§4-2)
- C2(Gather導出): `Gather(t₁..tₙ) ≡ traverse Wait [t₁..tₙ]`(完了順序によらず結果順序はインデックス順)
- C3(ハンドラ継承): `Spawn`されたタスクはspawn地点の内側ハンドラ連鎖を観測する(GetHandlersによる再設置 — scheduler.pyの実装が定義)
- C4(Promise単位律): `CreatePromise >>= λp. CompletePromise(p,v); WaitPromise(p) ≡ pure v`

### G6? Var(保留)
- V1(セルとしてはS1〜S4と同型)
- **V2(スコープ律): 書けない** — owner fiber解放後のVarの意味論が未定(invariants.md I8で実行時に観測: owner解放後のVarIdはグローバルヒープキーに退化し、`read_scoped_var_from`のフォールバック(vm/var_store.rs:144)が暗黙に「Var ≡ グローバルセル+スコープオーバーライド」の2層意味論を定義している)。SPEC-VM-020:191(ヒープセル化)とSPEC-VM-019 Rev 2(segments are the scope)の矛盾も未決着
- → **law不能フラグ: 設計曖昧シグナル**。結晶化議論の最優先議題

### 観測子(WithObserve/Intercept — 生成元ではなく様相)
- O1(非干渉律): `observe(o, p) ≡_値 p` — 観測子は値意味論で恒等。**この等式が書けることが「観測子」と「ハンドラ」の境界定義**(書けなければそれはハンドラ)

## 4. law-poorフラグ(設計曖昧のシグナル)

| # | 項目 | 症状 | 提案 |
|---|---|---|---|
| 1 | **Var/cells**(G6) | スコープ律が書けない。B14スペック間矛盾+I8実行時観測 | ヒープセル化(owner廃止)か、スコープ律の等式化かを決める。最優先 |
| 2 | **Spawn/Wait×共有状態** | C1往復律が条件付きでしか書けない。条件(「共有に触れない」)の判定法が未定義 | 共有Varの並行アクセス意味論を等式化 or 「タスク間共有はPromise経由のみ」を規範化 |
| 3 | **Skip**(_SKIPPEDセンチネル) | `is _SKIPPED`の同一性チェックはlaw化不能のad hoc | Selective functor(When/Skip)として§5のselective層に再定式化 |
| 4 | **SetTime** | sim実装は動作、real実装はno-op — ハンドラ間でlawが割れる(静かな違反) | realでは明示エラーにする(no-op禁止) |
| 5 | **Cancel** | キャンセル点の意味論(どこで停止が観測されるか)が等式で書けない | キャンセルは「次のscheduler効果で観測」等の規範を等式化 |
| 6 | **GetHandlers** | リフレクション。「scheduler=純ライブラリ」(B10)の但し書きであり、抽象を破る観測 | コアDoCtrlとして公認し「WithHandler構造の純関数」law(GH1: `get_inner_handlers(k) ≡ kの境界からprompt境界までのハンドラ列`)を明文化 |
| 7 | **global_state/writer_log** | 死設備(呼び出し元ゼロ) | 削除issue(検証条件: grep+全テスト) |

## 5. 合成則の層宣言

| 層 | 合成子 | 現状 | law |
|---|---|---|---|
| **applicative**(構造が静的) | Traverse, Zip, Gather | traverse族は逐次実装のみ(並列化はGatherへの書き換えで可能 — 未実装) | traverse恒等・自然性・合成律、Zipのインデックス整合、失敗運搬=Either-applicativeの蓄積律 |
| **selective**(分岐候補が静的列挙可能) | When/Skip(再定式化後)、Race? | 未整理 — Skipのad hocセンチネルが該当層の空白を示す | selective laws(Mokhov)導入候補 |
| **monadic**(動的) | @do bind, flat_map, map | 主合成面 | 制御コアの等式に従属 |

宣言: パイプラインはapplicativeで書けるものをmonadicに落とさない(静的検査可能性の保存)。

## 6. 判定保留リスト(次セッションの議題、優先順)

1. Var意味論の決着(§4-1 — B14+I8。最優先)
2. Spawn/Wait×共有状態の交換則条件(§4-2 — C4×C5の再来)
3. GetHandlersのコア公認とlaw(§4-6)
4. Skipのselective層への再定式化(§4-3)
5. Traverse並列意味論(applicative層の実装充足)
6. 残骸削除issue群: global_state/writer_log、PromptBoundary.types、MaskSpec(constraint-graph §4)
7. **カバレッジ実測**: 過去タスク20個をこの生成元集合で書き直し、7割未満なら切り口を疑う(戦略文書の基準 — 本セッション外)

## 7. インタプリタ分離の現状

項=データ(DoCtrl/Program)、実行=ハンドラ選択は達成済み。複数意味論の実例: doeff-time(sync/async/sim 3実装)、scheduler(priority/realtime)。決定論的シミュレータ(戦略文書の優先3)はsim-timeハンドラ+決定論schedulerの合成として構成可能 — 生成元がG1〜G6に閉じていれば、シミュレータは「全外界橋(G4+Time+HTTP)のsim実装差し替え」で得られる。これが本代数の完全性の実用的判定式である。
