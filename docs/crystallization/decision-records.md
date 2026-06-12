# 決定記録 — doeff事後検死+結晶化セッション

形式: 各記録は「何を選び / 何を捨て / なぜか」。日付はすべて2026-06-11(本セッション)。
オーナー回答によるものは [オーナー]、セッション側の判定は [判定] と標示。

---

## D1. リビルド境界の認定 [オーナー]

- **選択**: R1=godインタプリタ→Python CESK(2025-12-30〜2026-01-17)/ R2=Python CESK→Rust VM v1(2026-02-05〜02-11)/ R3=Rust VM v1→OCaml 5整合(2026-03-17〜03-25)
- **棄却**: (案B) cesk_v3破棄やCESK内部書き直しを独立リビルドと数える(粒度が細かすぎ、mainに統合されていない探索を「リビルド」と呼ぶと語が薄まる)/(案C)事後統合(SPEC-VM-021 Steps、4〜6月)を3つ目と数える(アーキテクチャの置換ではなく残務)
- **理由**: オーナーの「VM化後に3回」の記憶と高密度期(12月〜3月)に最も整合。各境界に決定的コミットが存在する。

## D2. check_invariants(Task 3)の実装層 [オーナー]

- **選択**: Rust側(doeff-vm-core内、デバッグassertベース)に実装し、VMの主要遷移後に呼ぶ
- **棄却**: Rust→snapshot→Python検査(API追加分のRustがかえって増える)/ Python側のみ(arena・parent鎖・one-shot等の核心不変条件を観測できない)
- **理由**: ハンドオフの「Rust禁止」は意味論確定後の高速Rust化を指すと解釈。現VM=遅くて正しい参照実装への不変条件追加は判断の固定(償却)であり、戦略文書の趣旨に合致。

## D3. cesk_v3の扱い [判定]

- **選択**: R2の一部(Python内でC1問題を解く最後の試行)として記載
- **棄却**: 独立リビルド扱い
- **理由**: 実装2日・mainに未統合・`ec74d15f`が "never integrated into the main runtime" と明言。リビルド=「稼働中アーキテクチャの置換」という定義を保つ。

## D4. R3トリガーの認定 [判定 — オーナー記憶は不明瞭]

- **選択**: 「補償機構群のバグ圧(stale trace・scheduler hang・継続チェーンバグの2週間の消火)が閾値を超え、構造認識(SPEC-VM-020)に至った」と認定
- **棄却**: 単一バグ起因説/純粋な設計レビュー起因説
- **理由**: 3/2〜3/16に trace系(`7973cd4e`, `73900415`, `b6e3049a`)、hang系(`430f8503`, `dd2a05a8`, `34e558fb`)、継続チェーン系(`38150405`)の修正が密集し、側表除去の先行試行(`9ad9ba50`)まである。ブランチ名 drop-stale-trace とも整合。

## D5. 結合核クラスタにC5を追加 [判定]

- **選択**: C5「スケジューラの所在 ⇔ 継続所有権」を§3の事前予想4クラスタに追加
- **理由**: R1(pluggable scheduler導入)→v1(VM内蔵6,798行)→R3(エフェクトハンドラへ解体)と3回振動し、スケジューラが継続を保持する設計がC2へ複コピー要求を波及させる相互拘束が確認されたため。

## D6. 仮説Xの判定 [判定]

- **選択**: 真(精緻化付き: 失敗様式は「誤った選択」より「選択の不在=局所修正の複利」が支配的/覆るのは意味論選択ではなく意味論と表現の束縛機構)
- **棄却**: 偽(原因分散説)— 独立判断の誤りも多数あったが、いずれも局所修正で済みリビルドを誘発していないため
- **根拠**: postmortem.md §5。決定的証拠は SPEC-VM-020:193-196(結合の明言)と SPEC-VM-021:6-11(部分修正PR #371の失敗)、およびオーナー証言「なし崩しに増殖」。

## D7. R2の決め手の記録 [オーナー]

- 証言(2026-06-11): 「performance, compiler checks, it was about time to introduce proper effect language semantics like handler」
- 解釈: 性能+コンパイラによる強制(C2)+handler意味論の本格導入(語彙の結晶化)の複合。単一の決定打ではない。

## D8. R1脱出タイミングの評価 [オーナー]

- 証言: 「早すぎなかった。さもなければ『ハンドラ』抽象を導入できなかった」
- 帰結: R1の相判定は「正当な発見相からの適時脱出」。ただしC4(状態マージ)のchurnは機械と無関係に等式で先決できた、を併記(postmortem §1(d))。

## D9. 状態×継続の共有意味論を「意図的・確定」と認定 [オーナー]

- **選択**: global_state・cellsとも捕獲時コピーなし・全fiber共有を確定意味論とする(one-shot前提では再開が1回なので共有が正しい)
- **棄却**: snapshot isolation(SPEC-EFF-002のknown issue gh#157が示唆する方向)/ ハンドラ層への責務移譲
- **帰結**: Task 4でlawとして明文化する。SPEC-EFF-002のknown issue記載は本決定と矛盾するためspec更新issue候補(constraint-graph.md §4-⑤)。

## D10. evidence passingは「未検討」と記録 [オーナー]

- 現実装の動的探索は意図的選択ではなく成り行き(検討の上の棄却ではない)。constraint-graph.md B5に成り行きと明記し、意味論確定後の高速Rust化の論点として保留。K2(捕獲コア)に辺を持つためフロンティア判断対象。

## D11. ランタイム未使用残骸の扱い [判定 — オーナーは認識なし]

- PromptBoundary.types / MaskSpec / append_writer_logの_seg_id無視の3点は、オーナーも把握していない=明示判断を経ていない成り行き残骸と認定。constraint-graph.md §4に記録し、検証(ブリッジ層使用有無)を前提条件としたissue候補とする。本セッションでは削除しない(読解・抽出専念の規律)。

## D12. 不変条件の重大度分離(violation / tension) [判定]

- **選択**: I8(Varセル所有者の生存)は致命的違反でなく「既知の緊張(tension)」として報告のみとする。他のI1〜I7は致命的違反としてpanic
- **棄却**: (a) I8もpanicにする(既存テスト`test_alloc_read_write_var`が落ちる — 現コードは`read_scoped_var_from`のフォールバックで意図的に吸収しており、チェッカーが現意味論を否定するのは越権)/(b) I8を削除する(B14緊張の実行時証拠を捨てることになる)
- **理由**: チェッカーの仕事は「コードが仮定する条件の違反検出」と「スペック間矛盾の可視化」の2つで、後者の解消は結晶化の判断。発見はinvariants.md I8に記録済み
- **発見の記録**: B14緊張は実行時に観測可能(owner解放後のVarIdはグローバルヒープキーに退化)。Task 4のVar/Get/Put law議論で決着すべき入力

## D13. 代数の生成元の地位は「VM特権」でなく「独立law系」で与える [判定]

- **発見**: 棚卸し+grep検証により、(a) Ask/Get/Put/Tellの正統ハンドラはクロージャ実装でVM特権不使用、(b) `VarStore.global_state`/`writer_log`は呼び出し元ゼロの死設備、(c) VM特権が要るのはVar(DoCtrl)・Await(ドライバ協調)・GetHandlers(イントロスペクション)のみ
- **選択**: 「コアエフェクト=VM特権が必要なもの」という区分は空集合に近く判定基準として機能しないため、生成元の地位を「独立なlaw系を持つ意味論単位」で与える(algebra-draft.md §0,§2: 制御コア5+生成元5〜6)
- **棄却**: VM特権基準(空振り)/全エフェクト並列(45個は規格の5〜10に反する)
- **理由**: 歴史的削除パターン(§1.2)も「合成で書ける/値で表せる/外界はコア外」であり、特権の有無ではなかった

## D14. law-poor項目の扱い [判定]

- **選択**: law が書けない項目のうち、外界橋(Await/HTTP/Time)は「lawの境界」として正当と宣言し、それ以外(Var、Spawn×共有状態、Skip、SetTime real no-op、Cancel)は設計曖昧シグナルとしてフラグ(algebra-draft.md §4)
- **理由**: 「law-poor=曖昧」を機械的に適用すると外界橋が偽陽性になる。境界宣言と曖昧シグナルの区別が要る

---

以下は2026-06-12の反例攻撃セッション(algebra-draft第二稿)。

## D15. G6 Var(VarStore一式)は死設備 → 全削除 [オーナー]

- **発見**: 反例攻撃の第一歩(使用者確認)で前提崩壊。Python API削除済み(`doeff/__init__.py:163,168` _Removedスタブ)、PyO3ブリッジ公開なし(Pythonから到達不能)、生きた使用者はvm_tests.rs 1本のみ。周辺機構(write_scoped_var_nonlocal / read_scope_binding_from / root_scope_bindings / Frame::LexicalScope / EvalInScopeReturn)も全て呼び出し元ゼロまたは到達不能
- **選択**: 全削除(削除issue仕様: constraint-graph.md §4-④)。将来Rust製ハンドラの状態置き場は「ownerなし純ヒープセル」として再導入(SPEC-VM-020:191準拠)とADR明記
- **棄却**: ヒープセル化して温存(呼び出し元ゼロの設備を維持する理由がない)/ 現状維持(I8 tensionの恒久放置)
- **帰結**: B14 spec矛盾・I8 tensionは主体の消滅で解消。生成元はG1〜G5で閉じ、「コアエフェクトは空集合」が完全成立。D14のVarフラグは「設計曖昧」ではなく「死設備の誤認」だったと訂正

## D16. タスク間共有の規範: Promise/Semaphore経由のみ [オーナー]

- **選択**: タスク間の通信・同期はPromise/Semaphore/Waitの結果値経由のみを規範とする。タスクを跨ぐGet/Put共有はlaw保証外と宣言
- **棄却**: 干渉自由条件の明記のみ(検査不能でレビュー基準にならない)/ Spawn時のstate fork(実装変更+既存の共有依存コードを壊す)
- **理由**: stateハンドラの「再設置」(CC3)は同一クロージャの共有であり、跨ぎGet/Putは共有メモリ。規範準拠コードでは干渉自由が構成的に保証され、CC1往復律・AW2が適用可能になる
- **帰結**: algebra-draft §3 G5に規範として明記。レビュー判定基準: spawnされるプログラムがGet/Putで親や兄弟と通信していたら規範違反

## D17. AW2(Await逐次律)は干渉自由条件付きで保持 [オーナー]

- **発見**: 無条件AW2は偽。LHS(Await 2回)はyield点2つ、RHS(合成して1回)は1つで、asyncドライバ×複数タスク下では他タスクの割り込み可能点が観測可能に異なる(CC5決定論の下で確定的に異なる結果を生む入力が構成可能)
- **選択**: 「干渉自由の文脈でのみ成立」と条件付きで保持。await融合最適化の正当性条件として機能。D16規範に従うプログラムでは適用可能
- **棄却**: 削除(融合可否が毎回個別判断に戻る)/ 値等価への再定義(共有外界下では最終値自体が変わるため不健全)

## D18. lawの地位はハンドラ契約 [判定]

- **選択**: lawは「全ハンドラについての定理」ではなく「ハンドラ契約」(理論T、ハンドラ=T-代数の標準観)。契約を満たすハンドラだけが生成元名(Reader/State/Writer等)を名乗れる
- **理由**: 「Askを数えてTellするハンドラ」のような効果的ハンドラはA1/A4の見かけ反例になるが、これは代数の欠陥ではなく記述の欠陥。再解釈でA系・S系・W系への同型攻撃が全てハンドラ側の義務に転化する
- **帰結**: 等式変形(人・codexのリファクタリング)の正当性は「設置ハンドラの契約準拠」を前提とする、と規格に明記(algebra-draft §3冒頭)

## D19. K1復元: PR #404のArc/share_handle機構を除去し、move-only法を復元 [オーナー — 2026-06-12]

- **選択**: Option (b) — 構成的move-only法を復元。PR #404のArc<Mutex<Option<>>>・share_handle機構を完全に除去し、Continuation.chainを`Option<DetachedFiberChain>`(plain move)に戻す。例外伝播の#404セマンティクスは`Py<PyK>`ハンドル(Python参照、継続コピーではない)で保持
- **棄却**: Option (a) — 仕様を弱めてArc機構を許容(K1結合核の法を放棄することに等しい)
- **理由**: PR #404(47d2a518, 2026-04-25)は実際の意味論バグを修正したが、選ばれた機構がSPEC-VM-021不変条件1, 2, 4に違反。share_handle()はArcの第二参照を生成、VMとFrameに`Option<Continuation>`バックアップを格納。さらにライブバグ: バックアップがone-step窓を超えて残存し、次の無関係なProgramフレームに付着(stale-backup-leak)
- **機構**: VMは`Py<PyK>`ハンドル(continuation.rsのPyKへのPython参照)を保持。ハンドラが例外を発生させた場合、VMはハンドルを借用してPyK.take()でチェーンを取得し再接続(discontinue k exn相当)。Callable traitに`is_generator_handler()`を追加: Pythonジェネレータハンドラのみ`Py<PyK>`バックアップを使用、同期Rustハンドラは`Value::Continuation(k)`を直接受け取る
- **検証**: #404回帰テスト6件全件green、stale-backup-leak回帰テスト追加、ガードレイヤー(test_move_semantics_architecture.py)をSPEC-VM-021不変条件に準拠するよう書き直し(6テスト全pass)、cargo test --features "python_bridge,invariant-checks" 37pass/0fail

## D20. GetHandlersのコア公認 — リフレクション様相としてlawで縛る [オーナー裁可 2026-06-12]

- **選択**: コアDoCtrl(tag 26)として公認し、GH1(境界: kの捕獲地点からprompt境界までのハンドラ列・内側優先順)/ GH2(非消費: kはresume可能なまま — move-only規律の明示的例外)/ GH3(観測範囲: callable列のみ、フレーム・状態・継続構造は不可視)で縛る
- **棄却**: 削除してschedulerにVM特権付与(「コアエフェクトはほぼ空集合」定理を自壊)/ 生成元昇格(ユーザーが値のためにperformする効果ではなく、ハンドラ作者向けリフレクション=様相)
- **理由**: 既に荷重を負う — schedulerのSpawn時ハンドラ再設置(CC3の実装、scheduler.py:453)+doeff-traverse 4箇所(handlers.py:35,145,170,256)。実装(step.rsのfiber鎖歩行)はGH1と一致、違反なし
- **帰結**: algebra-draft §3観測子の隣に記載。機械化は委譲issue `laws-gh-can-mechanization`

## D21. Skipはselective層へ再定式化 [オーナー裁可 2026-06-12]

- **選択**: When/SkipをSelective functor(Mokhov)として再定式化。S1(真分岐)/ S2(偽分岐 — skipは値であり失敗ではない)/ S3(traverse吸収 — センチネル同一性チェックを等式で置換)/ S4(非失敗 — Try/Fail回復は発火しない)
- **棄却**: 現状維持(`is _SKIPPED`はlaw化不能のまま)/ Failへの統合(skip=除外とエラーは意味論が異なる。現実装がfailed=Trueで運んでいる混同自体が再定式化の根拠)
- **理由**: センチネルはhandlers.py内3箇所に閉じ、外部依存ゼロ — 移行コスト局所的。§5のselective層の空白がちょうど埋まる
- **帰結**: S1〜S4をalgebra-draft §5に制定。実装は委譲issue `skip-selective-reformulation`

## D22. Traverse並列はlawのみ制定、実装は需要発生まで保留 [オーナー裁可 2026-06-12]

- **選択**: TR1(書き換え: `traverse_par(f,xs) ≡ Gather(map(Spawn∘f,xs))`)/ TR2(干渉自由下で逐次と観測等価 — 条件はD16/D17と同一)を制定。実装はしない
- **棄却**: handlers.py直書きの専用並列実装(schedulerの並行性を複製する並走機械=compensator)/ law未制定のまま放置(将来の実装者が無法地帯で設計判断することになる)
- **理由**: traverse呼び出し元34件は実質テスト+examples、実需要なし。Gather(96件)が並列の主力。lawだけ固定すれば需要発生時にcodexへ一発委譲できる(判断の償却)
- **帰結**: algebra-draft §5にTR系として記載。委譲issueは作らない(需要発生時に起票)

## D23. Cancelは協調観測規範 — CAN1〜4 [オーナー裁可 2026-06-12]

- **選択**: 現実装の意味論を規範認定。CAN1(他者観測: cancelled済みtへのWait/Gather/Race ≡ TaskCancelledError throw — 観測点はscheduler.pyのちょうど4箇所)/ CAN2(自己観測: 次のscheduler効果でのみ。効果間は不可分)/ CAN3(非先取: 実行中の外部Awaitは完走、Promise/Semaphore遷移は遡及しない)/ CAN4(冪等)
- **棄却**: プリエンプティブ即時キャンセル(VM先取機構が必要 — K1所有権コアへ波及、協調スケジューラ設計と矛盾)/ structured concurrency(nurseryスコープ — 再設計規模。**将来再訪に値する**と明記)
- **理由**: cancellation/preemptionは結合核ホットスポット(K4隣接)であり、eef38caaのcancel lifecycle修正直後の今が固定の好機。CAN1〜3は現実装の記述で改修ゼロ
- **帰結**: algebra-draft §3 G5に記載。機械化は委譲issue `laws-gh-can-mechanization`

## D24. SetTimeはreal系ハンドラで即エラー [オーナー裁可 2026-06-12]

- **選択**: sync/asyncハンドラはSetTimeに即`NotImplementedError`(どのハンドラが何故拒否したかをメッセージに明記)。ST1として記録
- **棄却**: 現状のPass転送維持(外側の寛容なハンドラに黙って飲まれる・診断喪失)/ realで実時間変更(論外)
- **理由**: SetTimeはシミュレーション専用効果であり、realでの使用はバグ — 正しい層でfail-fast。なお旧記述「real実装はno-op」は不正確で、実態はPass転送だった(2026-06-12調査で訂正)— いずれにせよハンドラ間でlawが割れる問題は同じ
- **帰結**: 委譲issue `settime-real-handlers-fail-fast`(数行+テスト)
