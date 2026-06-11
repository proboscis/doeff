# doeff VM 暗黙の不変条件カタログ

作成: 2026-06-11。実装: `packages/doeff-vm-core/src/vm/invariants.rs`(cargo feature `invariant-checks`)。
これらは本セッション以前、**コードの形としてしか存在しなかった**整合性条件である。各条件に「どのコードがこの条件を黙って仮定/吸収しているか」の根拠を付す。

## 実行方法

```bash
cd packages/doeff-vm-core
PYO3_PYTHON=$(git rev-parse --show-toplevel)/.venv/bin/python \
  cargo test --features "python_bridge invariant-checks"
```

- `VM::step()`の**全遷移後**に`assert_invariants_after_step()`が走る(違反は全件列挙してpanic)
- feature無効時はゼロコスト(コンパイルされない)
- `VM::check_invariants()`(致命的違反のみ)と`VM::invariant_report()`(違反+既知の緊張)を直接呼ぶこともできる
- 注意: vm-coreの全モジュールは`python_bridge` featureの後ろにあるため、`invariant-checks`単独ではチェッカーはコンパイルされない

## 重大度の規約

- **violation(致命)**: VM自身の操作が「絶対に起きない」と仮定している構造破壊。発火=バグ。panicする
- **tension(緊張)**: スペックとコードの既知の矛盾で、現コードがフォールバックで吸収しているもの。報告のみ。解消は結晶化の判断であってチェッカーの判断ではない

## 条件カタログ

### I1. arenaスロット衛生(violation)
free_listは重複なし・範囲内・全エントリがNoneスロットを指す。
**根拠**: `arena.rs alloc/free` — free_listの整合はallocの正しさの前提。`attach_chain`はfree_list混入を実行時エラーにしている(arena.rs:74-79)が、free_list自体の衛生は誰も検査していなかった。

### I2. current_segmentの実在(violation)
`current_segment = Some(id)` ならidはarena内の生きたfiber。
**根拠**: `step.rs`の全経路が`self.segments.get_mut(seg_id)`を成功前提で使う。失敗時は個別の`else`分岐が散在。

### I3. 親鎖の非循環・非ダングリング(violation)
arena内の任意のfiberからparentを辿ると、循環せず、存在しないfiberを指さずに停止する。
**根拠**: `vm/var_store.rs:22`の`seen.insert`ガードと`:19 else break`は、循環とダングリングを**黙って吸収**する形でこの条件を仮定している。`collect_traceback`(vm.rs:82)も同様にbreakで吸収。違反すればスコープ解決・トレースバックが静かに不完全になる。

### I4. ハンドラ境界の整合(violation)
(a) 境界fiberのHandlerはprompt/intercept/maskの**ちょうど1役**。(b) placeholderマーカー(0)はarenaに設置されない。(c) Markerはarena内で一意。
**根拠**: `segment.rs:64-113`の3コンストラクタは1役のみ設定する(構造上の意図)が、フィールドはpubで多役構成も作れる。Marker一意性は`find_prompt_boundary_by_marker`(vm/handler.rs:83)の正しさの前提。

### I5. 分離鎖の内部整合(violation)
VMから見える全DetachedFiberChain(pending_handler_chain_backup、Frame::Program.chain_backup、VarStore内のValue::Continuation)について: head/last_fiberが所有fibers内に実在、id重複なし、headからparent鎖でlast_fiberに到達し全所有fiberを被覆、tailのparentはNone。
**根拠**: `arena.rs detach_chain`(:40-62)が構築時にこれを保証するが、`DetachedFiberChain::append`(continuation.rs:88)等の後続操作後の維持は検査されていなかった。

### I6. 単一所在law(violation)— K1結合核の機械化
**「継続(が所有するfiber)は任意の時点でちょうど1箇所に存在する」**(SPEC-VM-021:282の実行時検査)。具体的に: 分離鎖が所有するfiberのarenaスロットは`VacantReserved`(空だがfree_listに無い=予約中)であり、同一fiberを2つの鎖が所有しない。
**根拠**: `attach_chain`(arena.rs:74-88)が再接続時に部分的に検査するが、保持中の継続については誰も検査していなかった。これはR3リビルドの根本原因(postmortem.md §3)をlawとして固定したもの。

### I7. EvalReturn参照の実在(violation)
arena内fiberの`EvalReturn{ResumeToContinuation|ReturnToContinuation|EvalInScopeReturn}`フレームが参照する`head_fiber`は、arena内またはVM可視の分離鎖内に存在する。
**根拠**: `vm/var_store.rs:31-41`がこの参照を辿ってスコープを解決するが、欠損時は黙ってスキップする(=静かにスコープが欠ける)。

### I8. Varセル所有者の生存(**tension** — 既知の緊張B14)— 決着済み、撤去予定
`var_store.cells`の各VarIdの`owner_segment`は解放(VacantFree)されていない。
**現実**: **既存テスト`test_alloc_read_write_var`で発火する**(fiber完了後もセルが生存)。`read_scoped_var_from`(vm/var_store.rs:144)の無条件フォールバックが吸収するため本番では不可視。
**意味**: SPEC-VM-020:191「VarId.owner_segmentは存在しないべき(セルはポインタで指す)」とSPEC-VM-019 Rev 2「segments are the scope」の矛盾(constraint-graph.md §4-④)が**実行時に観測可能な形で実在する**ことの証明。
**決着(2026-06-12、D15)**: Var一式は死設備と判明し全削除が確定(Python API削除済み・ブリッジ公開なし・使用者はRustテスト1本)。tensionは主体の消滅で解消する。I8チェッカーは削除issue(constraint-graph.md §4-④)の一部として撤去予定 — それまでは現状の「報告のみ」を維持。

## チェッカーの観測範囲の限界

1. **Python側PyKのみが保持する継続は不可視**(VM構造体から到達不能)。単一所在lawの完全検証にはブリッジ層の協力が要る
2. **DoCtrl内に埋まった継続**(例: EvalReturnのApply系フレームに積まれた`DoCtrl::Resume{k}`)は走査しない(再帰深度の割に発見余地が小さいため。必要なら拡張可能)
3. `step()`の**遷移後**のみ検査するため、遷移中の一時的不整合は観測しない(意図的: 遷移中は不変条件が成立しなくてよい)

## 実装の変更点(参照)

- 新規: `src/vm/invariants.rs`(チェッカー本体+ユニットテスト8本)
- `Cargo.toml`: feature `invariant-checks` 追加
- `src/vm.rs`: モジュール登録(cfgゲート)
- `src/vm/step.rs`: `step()`末尾にフック(cfgゲート、ロジック変更なし)
- `src/continuation.rs`: `cell_addr`/`inspect_chain`ヘルパ(cfgゲート、crate内専用)
- `src/arena.rs`: `SlotStatus`/`slot_status`/`free_list_violations`(cfgゲート、crate内専用)

検証結果(2026-06-11): feature有効で36 passed / 1 ignored(既存28+新規8)、feature無効で28 passed(退行なし)。
