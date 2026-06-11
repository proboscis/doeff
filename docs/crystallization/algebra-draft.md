# doeff エフェクト代数 第二稿(反例攻撃済み)

作成: 2026-06-11。事後検死(postmortem.md)・制約グラフ(constraint-graph.md)・現行語彙の棚卸し(45エフェクト型)に基づく。
**改訂: 2026-06-12 反例攻撃セッション** — G6決着(死設備→全削除、D15)、共有規範確定(D16)、AW2条件付き化(D17)、lawの地位確定(D18)、C系→CC系改名、基盤law CS1/CC5追加。
**用途**: law確定版。生成元はG1〜G5で閉じた。残る保留は§6。
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
- ~~VMが提供する生きた状態設備はcells(Var)とスコープ束縛のみ~~ **2026-06-12訂正: cells(Var)もスコープ束縛も死設備だった**(§3 G6 — Python到達経路なし、使用者はRustテスト1本)。**VM特権状態は文字通りゼロ**
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
| ~~G6~~ | ~~**Var**(AllocVar/ReadVar/WriteVar)~~ | 「唯一のVM特権状態」は誤認 — **死設備と判明、削除決定**(§3 G6、D15) | **決着: 削除**(2026-06-12) |

制御コア5 + 生成元5 = 規格(5〜10個)内。**生成元集合はG1〜G5で閉じた。VM特権状態はゼロ — 「コアエフェクトは空集合」が完全に成立。**

### 導出物(ライブラリ)の導出経路

- `Try` = エラーチャネル(コア)上のハンドラ
- `Local(e, p)` = `reader(e ⊕ outer_env)(p)` + 内側ハンドラ再設置
- `Listen(p, T)` = ローカルWithHandlerで型Tを横取り+Pass
- `Gather(ts)` = `traverse Wait ts`(scheduler内部で導出済み)
- `Race/Cancel/Promise/Semaphore` = Spawn/Wait+キュー状態(scheduler.pyで実証)
- `Cache/Memo` = State law系のstorage上への移送+Try+コストrouting
- `Traverse族` = applicative合成則の具現(§5)
- `Time/HTTP/domain` = 外界エフェクトのパラメトリックな族(Awaitと同格の橋、コア外)

## 3. law(生成元ごと)

### lawの地位: ハンドラ契約(D18 — 2026-06-12確定)

lawは「全ハンドラについての定理」ではなく**ハンドラ契約**である(代数的エフェクトの標準観: 生成元+law=理論T、ハンドラ=Tのモデル/T-代数)。A1〜A4を満たすハンドラだけが「Readerハンドラ」を名乗れる。「Askを数えてTellするハンドラ」はA1の反例ではなく、Readerインタプリタの資格を持たないだけである。この再解釈により、A系・S系・W系への「効果的ハンドラ」型の見かけ反例は全てハンドラ側の義務に転化する。**等式変形(人・codexによるリファクタリング)は、設置されているハンドラが契約準拠であることを前提に正当化される。**

### 基盤law: 協調実行(2026-06-12追加 — scheduler実装から逆算)

- **CS1(協調原子性)**: インターリーブは**scheduler効果・外界効果の発行点でのみ**起こる。スケジューラは単一スレッド協調式(Transfer/ResumeThrowベース — scheduler.py:312-341)で、2つのyield点の間のエフェクト列はアトミック。S1〜S4・W1が「タスク内で」無条件成立する根拠
- **CC5(決定論)**: readyキューはpriority heap+挿入順tie-break(scheduler.py:229,258-262)= FIFO within priority。外界の非決定性は`external_queue`(scheduler.py:235)の1箇所に隔離されており、**外部promise不在ならスケジューリングは入力の決定論的関数**。→ 決定論的シミュレータ(戦略優先3)の存在証明: 外界橋の差し替えのみで決定論実行が得られる

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
- AW2(逐次律・**条件付き** — D17): `Await(c₁) >>= λx. Await(c₂(x)) ≡ Await(c₁ >>= c₂)` — **干渉自由の文脈でのみ成立**。
  - **反例(2026-06-12確定)**: LHSはyield点2つ、RHSは1つ。asyncドライバ×複数タスク下では、c₁完了とc₂開始の間に他タスクが割り込めるか否かが観測可能に異なり、CC5(決定論)の下で**確定的に異なる結果**を生む入力が構成できる
  - 成立範囲: syncドライバ(無条件)/単一タスク(無条件)/複数タスクでも当該区間が干渉自由(共有規範D16準拠)なら成立。**await融合最適化の正当性条件**として保持
- 注: これ以上のlawは書けない(外界の非決定性)。**外界橋はlawの境界**であり、law-poorはここでは設計曖昧のシグナルでなく境界宣言である

### G5 Spawn/Wait(C系→**CC系**に改名 — クラスタC1〜C5との衝突解消)
- **共有規範(D16 — 2026-06-12オーナー確定)**: タスク間の通信・同期は**Promise/Semaphore/Waitの結果値経由のみ**。タスクを跨ぐGet/Put共有はlaw保証外(ハンドラクロージャは共有されるため動作はするが、等式変形の前提にしてはならない)
- CC1(往復律): `Wait(Spawn(p)) ≡ p` — **干渉自由の仮定下で成立**。共有規範に従うプログラムでは干渉自由が構成的に保証されるため、規範準拠コードではCC1は適用可能
- CC2(Gather導出): `Gather(t₁..tₙ) ≡ traverse Wait [t₁..tₙ]`(完了順序によらず結果順序はインデックス順)
- CC3(ハンドラ継承): `Spawn`されたタスクはspawn地点の内側ハンドラ連鎖を観測する(scheduler.py:324-327の再設置で実装確認)。**注意: 「再設置」は同一クロージャの共有であって複製ではない** — stateハンドラのstore dictはタスク間共有メモリになる(CC1に条件が要る根本原因。D9の共有意味論と整合)
- CC4(Promise律): `WaitPromise`という効果は存在しない — 実体は`Wait(promise.future)`(waitable_keyがTask|Future両対応、scheduler.py:242-248)。law: `CreatePromise >>= λp. (CompletePromise(p,v) との任意順序で) Wait(p.future) ≡ pure v` — **CompleteとWaitは順序可換**(waiters機構が先行Waitを保留し、完了時にwakeする)。単位律より強いこの順序可換性が本当の内容

### ~~G6 Var~~ — 決着: 全削除(D15 — 2026-06-12オーナー確定)

反例攻撃の第一歩(使用者の確認)で前提が崩壊: **Varは「law未確定の生成元候補」ではなく死設備だった**。
- Python API削除済み: `doeff/__init__.py:163,168` — `AllocVar`/`ReadVar`は`_Removed`スタブ
- PyO3ブリッジ公開なし: `packages/doeff-vm/src`にAllocVar/ReadVar/WriteVarの言及ゼロ → **Pythonから到達不能**
- 生きた使用者はRustユニットテスト1本のみ(vm_tests.rs:174-226)
- 周辺機構も全死: `write_scoped_var_nonlocal`・`read_scope_binding_from`・`root_scope_bindings`は呼び出し元ゼロ、`Frame::LexicalScope`は到達不能経路でのみ生成(step.rs:92-95はskipするだけ)、`EvalReturnContinuation::EvalInScopeReturn`は構築箇所ゼロ

帰結:
- **B14 spec矛盾とI8 tensionは主体の消滅で解消**(SPEC-VM-020:191「owner_segmentは存在すべきでない」が削除により自動成立)
- V2スコープ律問題は消滅(lawを書く対象がない)
- owner_segmentは検死の一般化パターンの再演だった: VarId×owner×override×fallbackの4点補償子セットが、削除で一斉に溶ける
- 削除issue仕様: constraint-graph.md §4-④。将来Rust製ハンドラが状態置き場を要する場合は「**ownerなし純ヒープセル**」として再導入(SPEC-VM-020:191準拠)— issueにADRとして明記

### 観測子(WithObserve/Intercept — 生成元ではなく様相)
- O1(非干渉律): `observe(o, p) ≡_値 p` — 観測子は値意味論で恒等。**この等式が書けることが「観測子」と「ハンドラ」の境界定義**(書けなければそれはハンドラ)

## 4. law-poorフラグ(設計曖昧のシグナル)

| # | 項目 | 症状 | 提案 |
|---|---|---|---|
| 1 | ~~**Var/cells**(G6)~~ | **決着(2026-06-12 D15)**: 死設備と判明 | 全削除(constraint-graph §4-④の削除issue) |
| 2 | ~~**Spawn/Wait×共有状態**~~ | **決着(2026-06-12 D16)**: 規範化 | タスク間共有はPromise/Semaphore経由のみ。CC1は干渉自由仮定下で成立と明記 |
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

## 6. 判定保留リスト(優先順 — 2026-06-12改訂)

~~1. Var意味論の決着~~ → **決着(D15: 全削除)**
~~2. Spawn/Wait×共有状態の交換則条件~~ → **決着(D16: Promise/Semaphore経由のみ規範化)**

残り:
1. GetHandlersのコア公認とlaw(§4-6)
2. Skipのselective層への再定式化(§4-3)
3. Traverse並列意味論(applicative層の実装充足)
4. 残骸削除issue群: VarStore一式(④に統合 — global_state/writer_log含む)、PromptBoundary.types、MaskSpec(constraint-graph §4)
5. **カバレッジ実測**: 過去タスク20個をこの生成元集合(G1〜G5)で書き直し、7割未満なら切り口を疑う(戦略文書の基準 — 本セッション外)
6. CS1/CC5のproperty test化(決定論的シミュレータ構築=戦略優先3の入口)

## 7. インタプリタ分離の現状

項=データ(DoCtrl/Program)、実行=ハンドラ選択は達成済み。複数意味論の実例: doeff-time(sync/async/sim 3実装)、scheduler(priority/realtime)。決定論的シミュレータ(戦略文書の優先3)はsim-timeハンドラ+決定論schedulerの合成として構成可能 — 生成元がG1〜G5に閉じた今、シミュレータは「全外界橋(G4+Time+HTTP)のsim実装差し替え」で得られる。**CC5(決定論law)がこの構成の存在証明である**: スケジューラ自体は既に決定論的で、非決定性はexternal_queue 1箇所に隔離済み。これが本代数の完全性の実用的判定式である。
