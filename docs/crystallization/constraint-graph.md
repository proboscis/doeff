# doeff 設計空間の制約グラフ

作成: 2026-06-11。postmortem.md の検死結果と現実装の検証(file:line付き)に基づく。
**用途**: モデルルーティングの基準 — 結合核(K1/K2)に触れる変更はフロンティア+人間+law改訂が前提。独立領域は検証条件付きissueとして下位モデルへ。

**拡張**: doeff-conductor サブシステムの制約グラフは [constraint-graph-conductor.md](constraint-graph-conductor.md)(2026-06-12、核 K3/K4/K5)。核番号はこのファイルから連番 — グラフは1つの生きた成果物。

意図性の判定区分: `spec`(スペック明文)/ `test`(アーキテクチャ違反テストで機械強制)/ `code`(コードコメント等のみ)/ `owner`(2026-06-11オーナー確認)/ `成り行き`(明示判断の証拠なし)。

---

## 1. 分岐表 — 何を選ぶと何が排除されるか

| # | 分岐 | 現実装の選択 | 根拠 | 排除されるもの | 意図性 |
|---|---|---|---|---|---|
| B1 | ハンドラ探索 deep / shallow | **deep**(parent鎖を全探索、再開後もハンドラ残置) | dispatch.rs find_handler_for_effect(:247-250付近) | shallowの再設置パターン、軽量一回ハンドラ | spec(SPEC-VM-020) |
| B2 | 継続使用回数 one-shot / multi-shot | **one-shot** | continuation.rs `take()`(:284付近)、エラー "one-shot violation" | 分岐再開(amb/プロンプト再入)、リプレイ、時間旅行 | spec(SPEC-VM-021)+test |
| B3 | one-shot強制 観測(ID+flag) / 構成(move) | **構成(move)** — `Option::take`、Clone禁止 | continuation.rs:252 `chain: Option<DetachedFiberChain>`, take():277, SPEC-VM-021:84,200, ガードテスト test_move_semantics_architecture.py(6件), D19 | 継続の恒等子・レジストリ・共有フラグ(=v1の補償機構)、Arc/Mutex/share_handle(=PR #404の機構、D19で除去) | spec+test |
| B4 | エフェクト選別 ランタイム型フィルタ / ハンドラ全受け | **全受けパターンマッチ** | SPEC-VM-020:206 | ランタイム型フィルタ最適化(キャッシュ含む) | spec ※残骸あり(§4-①) |
| B5 | ハンドラ解決 evidence passing / 動的探索 | **動的探索**(毎perform、キャッシュなし) | dispatch.rs(キャッシュ構造なし)、SPEC-VM-020:206がキャッシュ禁止 | 設置時解決、O(1)ディスパッチ | **成り行き**(evidence passingは未検討 [owner])。高速Rust化の論点として保留 |
| B6 | 状態×継続 共有 / スナップショット | **共有**(cells捕獲時コピーなし) | src/var_store.rs、vm/var_store.rs(コピー箇所なし) | 分岐意味論、トランザクショナルロールバック、multi-shot拡張 | **owner: 意図的・確定**(one-shot前提で共有が正しい)。law未明文 → Task 4で昇格。SPEC-EFF-002のknown issue(gh#157 snapshot isolation)は本回答と矛盾、spec更新要。※追記: `global_state`/`writer_log`は呼び出し元ゼロの死設備と判明(§4-④)。**※2026-06-12訂正: cells(Var)もスコープ束縛も死設備**(D15 — Python到達経路なし)。VM特権状態はゼロで、B6の共有意味論はPythonクロージャハンドラの状態(scheduler/state/writer)についてのlawとして生きる。正統のState/Writerハンドラはクロージャ実装(algebra-draft.md §0) |
| B7 | ハンドラ実行サイト 親fiber / 新fiber | **親fiber**(boundary_parentへ切替後にcall_handler) | step.rs(:723-735付近)、`d6923708` | ハンドラ専用fiber、ハンドラ内エフェクトの自己捕獲 | spec(OCaml 5整合) |
| B8 | 再開 Resume(non-tail) / Transfer(tail) | **両方提供**(Transferはhandler frameをpop) | do_ctrl.rs:37-48、step.rs:212-227 | — (OCaml 5のcontinueの2形を明示化) | code |
| B9 | 転送 Pass / Delegate | **Passのみ**(handler fiberをkへ付加して外側で再perform)。Delegateは削除済みでエラー | step.rs:258-264、SPEC-VM-021:200-215 | ハンドラ自身を鎖から外す転送 | spec(Pass)+code(Delegate削除) |
| B10 | スケジューラ所在 VM内蔵 / エフェクトハンドラ | **ハンドラ**(doeff-core-effects/scheduler.py、再帰WithHandler) | scheduler.py、VM coreにスケジューラなし | VM特権スケジューラ(=v1の6,798行) | code+検死で確証(R3) |
| B11 | Python境界 自動検出 / 明示タグ | **明示**(DoExprタグ必須、python_to_value自動変換なし) | ブリッジ層、handler.rs:97コメント | ダックタイピング検出、暗黙ストリーム実行 | code+test |
| B12 | トレース 蓄積 / 導出 | **導出**(fiber chainの純関数として都度組立) | SPEC-VM-020:204、違反テスト(TraceState禁止) | 蓄積トレース(staleの源)、DispatchId | spec+test |
| B13 | fiber解放 arena保持(G0) / GC(G1) | **G0**(解放なし) | SPEC-VM-023:38-92(G1は設計のみ) | 長寿命プロセスのメモリ回収(現状) | spec(保留と明記) |
| B14 | 変数の所在 セグメント所有(owner_segment) / ヒープセル | ~~owner_segment現役~~ → **削除決定**(2026-06-12 D15) | vm/var_store.rs:140,156,179 | — | **決着: 主体の消滅で解消**。Var一式は死設備と判明(Python API削除済み・ブリッジ公開なし・使用者はRustテスト1本)。削除によりSPEC-VM-020:191が自動成立、I8 tension消滅。削除issue: §4-④ |
| B15 | 観測 Intercept(実装済) / Mask(未実装) | Interceptのみ実働 | step.rs:568-598、MaskSpecはdispatch未参照 | — | Intercept: code / Mask: **成り行き残骸**(§4-②) |
| B16 | コア vs ライブラリのエフェクト境界 | Result/Maybe値化、scheduler/cache/listenはライブラリ側 | `fedfce85`, `d6923708` | — | 反復的に再交渉されてきた軸。Task 4(algebra-draft)の主題 |

## 2. 結合核(コア)のマーキング

### 核K1: 所有権コア — B2 ⇔ B3 ⇔ B6 ⇔ B10 ⇔ B12

相互拘束の構造(R3の検死で実証済み):

```
one-shot(B2) ──強制──> move所有権(B3) ──不要化──> 恒等子・レジストリ・フラグ
     │                      │
     └──整合──> 状態共有(B6)  └──前提──> スケジューラが継続を保持しない(B10)
                                              │
トレース導出(B12) <──可能化── 「fiber chainが唯一の真実」 <┘
```

- one-shotだから再開は1回 → 状態の分岐が存在せず共有(B6)が正しい意味論になる
- moveで強制(B3)するから恒等子(ContId)・レジストリ・共有フラグが不要 → 蓄積トレース(B12の逆)の存在理由も消える
- スケジューラ(B10)が継続を保持・複製しないからmoveが保てる(保持する設計はclone要求をC2へ波及させる — v1の失敗)
- **どれか1つだけ動かすと破綻する**: multi-shot化→B6スナップショット再設計+B3クローン許可→恒等子復活+B12再考。部分修正の失敗例=PR #371(SPEC-VM-021:6-11)

### 核K2: 捕獲コア — B1 ⇔ B5 ⇔ B9(+B7)

- deep探索(B1)+「捕獲は境界を含む」(`d6923708`: boundary included)→ resume時のハンドラ再設置が構造から自動で出る(deep handler等式の機械化)
- evidence passing(B5の対案)に変えると「ハンドラ集合は捕獲時固定か解決時参照か」が変わり、B1の意味論とB9(Passのfiber付加)の再定義が必要
- B7(親fiberで実行)はK2の前提: ハンドラ自身は捕獲範囲の外に立つ

### 独立領域(下位モデルへ委譲可能)

B8(Resume/Transferの表面)、B11の詳細、B15の清掃、B16の個別エフェクト整理、API命名、ドキュメント整合。検死の対照群と一致: これらのchurnは常に局所diffで済んでいる。

## 3. ルーティング規則(このグラフの使い方)

1. **K1/K2の頂点に触れる変更**(multi-shot、evidence passing、状態スナップショット、scheduler VM内蔵化、shallow handler、捕獲範囲変更)= フロンティア+人間。着手前にlaw改訂を等式で書き、SPEC更新とアーキテクチャ違反テスト追加までを1セットにする
2. **独立領域** = 検証条件付きissue(対象・違反条件・手順・検証方法)としてcodex級へ
3. **判定に迷う変更** = まずこのグラフに頂点として追加し、K1/K2への辺があるか問う。辺が2本以上あれば結合核扱い
4. 検死の教訓: 結合核は「誤った選択」より「**選択の不在**」で壊れる。K1/K2に辺を持つ実装PRは、diffの局所正しさでなく不変条件(Task 3 check_invariants)で守る

## 4. 疑義・残骸リスト(issue候補)

| # | 項目 | 状態 | 対応案 |
|---|---|---|---|
| ① | `PromptBoundary.types`(segment.rs:47)がランタイム探索で未参照 [agent読解・要追検証] | owner不明 | ブリッジ層での使用有無を検証 → 未使用なら削除issue |
| ② | `MaskSpec`(segment.rs:51-63)定義のみ、dispatch未参照 | owner不明 | 削除 or 実装意図のspec化 |
| ③ | `append_writer_log(_seg_id無視)`(src/var_store.rs:100) | **決着(2026-06-12)**: writer_logごと死設備 → ④に統合。Tellのlawはalgebra-draft W1〜W3で確定(正統はクロージャハンドラ) | ④の削除issueで消滅 |
| ④ | **VarStore一式の削除issue**(D15で決定済み。旧④spec矛盾+旧⑥死設備+③を統合) | **実行待ちissue(codex委譲可、下記ゲート必須)** | 削除対象(2026-06-12反例攻撃セッションで全数確認済み): `DoCtrl::{AllocVar,ReadVar,WriteVar}`(do_ctrl.rs:105-111)+step.rs:273-309のハンドラ/ `VarId`(ids.rs:41-91,165)/ `Value::Var`(value.rs:64,81)+python_generator_stream.rs:938/ `VarStore`全体(src/var_store.rs)+vm.rsフィールド/ `src/vm/var_store.rs`全体(visible_lexical_segments含む)/ `Frame::LexicalScope`(frame.rs:197-199)+step.rs:92-95/ `EvalReturnContinuation::EvalInScopeReturn`(frame.rs:142,171)+step.rs:786/ vm_tests.rs:174-226/ invariants.rs I8チェッカー+関連テスト/ pyvm.rs:50カウンタ/ lib.rs再エクスポート(vm-core:64, vm:23)。**残すもの**: doeff/__init__.pyの_Removedスタブ(墓標)。**ADR同梱**: 将来Rust製ハンドラの状態置き場は「ownerなし純ヒープセル」で再導入(SPEC-VM-020:191準拠)。**検証ゲート**: rg全文でゼロ参照+cargo test両feature(28+8相当)+Python統合テスト+アーキテクチャ違反テスト。純削除だがstep.rs/frame.rsに触れるため、tier-1レビュー1名にトリップワイヤレンズ(新規補償子の混入禁止) |
| ⑤ | SPEC-EFF-002のknown issue「snapshot isolation」(gh#157)vs owner回答「共有が確定」 | spec陳腐化 | spec更新issue(共有semanticsをlawとして明記 — algebra-draft S6/CC3参照) |
| ⑥ | ~~`VarStore.global_state`/`writer_log`~~ | **④に統合** | — |
