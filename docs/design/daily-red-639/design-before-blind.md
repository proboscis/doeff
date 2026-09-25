# doeff の日次の全体検証の赤(2026-09-26)の当日修理 — 設計と事前の主張(盲検の前に固定)

- 依頼: lt-FPBECBQ3W22ESC92VAMCENR5JR(class investigate・agora-redesign #639)
- 基準の版: doeff 本線 `7ef0fa772afca54684d5b75b51f286e6e1bb367d`
- 完了の範囲: **設計まで**。実装は class dev の別の依頼で、この依頼を親に名乗る。
- 設計者: 会話 c-8JYPFZ9T1AFVA8C10W51M547JW(claude-opus-5-5)

## 1. 固定した受入条件(#639 と依頼書から)

1. 日次の記録(land-partition doeff の verify)で、赤(ADR 3 本・release 区分・wiring とその内側の ERROR)が失敗名の一覧から消える。
2. ADR-DOE-AGENTS-012 の検査が、説明の註や正当な引数の追加で赤にならない。同じ族の検査が同じ理由で赤にならないことを反例で示す。
3. codex worker の検は、環境が無い機体では赤ではなく**未実行として名乗る**(agent-control-plane の依頼 A と同じ形 = dotfiles の検査層 `remote_check` の未実行の申告行を、その書式の持ち主から読んで書く)。
4. 日次の判定が green か、unexecuted の理由が環境の欠けだけになる。
5. (計画の担当が足した)日次で測られていない範囲が「測った・赤なし」の顔をしない。doeff-agents の package の検が途中で process ごと止まり、残り約 94% が 2 日間測られていないのに失敗名 0 本・「未実行のレイヤー: なし」と記帳された。

触らないもの: doeff-cluster・doeff-records・doeff-claude-code の機能、台帳(DEFF-ROSTER・MACRO-OWNER-ROSTER)を増やす向き、dotfiles の検査層の語彙(`UNEXECUTED_KINDS`)と申告行の書式。

## 2. 現状の事実(実測・版 7ef0fa77)

| 赤 | 実測した機序 |
|---|---|
| F1: `test_hy_file_under_a_workspace_package_dir_imports_from_its_package_base` | 検が pytester の内側の run を**同じ process** で走らせる。根の `tests/` の検(`from tests._run_helpers import …`)が `tests` を名前空間 package として `sys.modules` に入れた後だと、内側の run の `(import tests.helper [VALUE])` が `ModuleNotFoundError`。単独では緑・`tests/test_finally_doctrl.py` と同じ process で赤(0.24 秒)。失敗名の `packages/doeff-x/tests/test_y.hy` は内側の run の `ERROR` 行 |
| F2: `test_adr_doe_agents_012_turn_records_are_not_left_to_one_write` | 検査が註の行を除いた行の中で、呼びの字面 `(turn-record-ended-status record-status None #())` を部分一致で探す。f3858b94 が 4 つ目の引数 `pair-conditions` を足したので字面が一致しない。性質(巡回が usage を書かない)は保たれている |
| F3: `test_real_codex_worker_returns_schema_valid_json_through_agent` | e2e の印が無く日次の母集団に入っている。`shutil.which("codex")` が router の入口(`~/.agent-router/bin/codex`)を見つけ、実体が無いので exit 127 で赤。成功すれば本物の codex(モデル)を呼ぶ |
| F4: doeff-agents の package の検の中断 | `test_python_module_importing_hy_imports_in_a_fresh_interpreter[doeff_agents.sessionhost.acp.runtime]` が 90 秒(負荷補正 1.5 倍)を超えた。この検は 19 個の module をそれぞれ新しい interpreter で import し、日次は `PYTHONDONTWRITEBYTECODE=1` なので Hy の compile を毎回やり直す(手元の cache 無しで runtime 39 秒・cache ありで 1 秒)。子の `subprocess.run(timeout=120)` は 1 本の上限より長い |
| F5: 1 本の時間切れが package の残りを全部消す | `pyproject.toml` の `timeout_method = "thread"`。pytest-timeout の thread 方式は時間切れで stack を出して `os._exit(1)` する(pytest_timeout.py `timeout_timer`)。signal 方式なら `pytest.fail` で 1 本だけ赤。根の conftest の註(ADR-DOE-ENFORCE-001 R6)は「pytest-timeout は 1 本を赤にし、process を殺すのは watchdog だけ」を前提にしていて、ini の設定と矛盾している。thread を選んだ 204cfea7(2026-03-22)は「thread 方式は C 拡張の中でも割り込める」と書いたが、実際は割り込まずに process を終了する |
| F6: 日次の失敗名の抽出(dotfiles `land.py` の `_failed_name_pytest`) | 出力のどこにある `FAILED ` / `ERROR ` 行でも拾う(失敗した検の報告に含まれる内側の run の行も拾う)。集計行の無い pytest の session(中断)は名前 0 本。処理ステージに他の名前が 1 本でもあれば「0 件の注意」(D5)も出ない |

## 3. 責務(module)と公開の契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の契約 | 外への作用 | 寿命 | 守る不変条件 |
|---|---|---|---|---|---|---|---|
| M1 adr012-sweep-check | ADR-012 R49 の巡回の構造の検査(docs/adr の冊の中) | 「usage・entries・responses の役を持つ引数はどれか」を**呼び先の定義(judgment.hy の defk の引数の並び)から導く**読み方 | 呼びの行の字面・折れ方・註・局所変数の名 | 既存の `call-args-of`・`bare-code-lines`・`defk-body` を読む。失敗文は「何の役に何が渡っているか」を言う | なし(file を読むだけ) | 検査 1 回 | 巡回の中の turn-record-ended-status の全ての呼びで usage は `None`・entries は `#()`・responses は渡さないか `None`。呼びが 1 つ以上ある |
| M2 wiring-hermetic-test | 「新しい pytest の process が package の下の Hy の検をどう import するか」を示す検 | pytester の内側の run は**別の process** で走らせる | 外側の process の `sys.modules` | `pytester.runpytest_subprocess`。自分の独立性を、外側に `tests` を先に置いた状態でも緑になることで自分で示す | 一時 dir の子 process | 検 1 回 | 内側の結果が外側の import の履歴に依らない |
| M3 premise-declaration(新しい 1 点・根の conftest) | 「この検の前提(実行できる道具)がこの機体に無い」を判じ、skip にしつつ、日次に未実行として名乗る | 前提の観測(道具の実体を実行して終了コード 0 か)・未実行の申告を**誰の書式で書くか**(dotfiles の `remote_check` を file-path で読む — `_ADMISSION_CANON` と同じ読み方) | 申告行の綴り・語彙の値(dotfiles が持つ) | fixture(例 `machine_premise`)→ 道具の path を返すか skip。skip の理由は構造化した接頭辞を持ち、**controller 側**の `pytest_terminal_summary` が skip の報告から申告行を書く(xdist でも 1 回)。検査層が読めない機体(素の clone)では申告せず skip の理由だけ(`_broad_run_admission` と同じ扱い) | stdout へ申告行 | session | 前提の欠けは赤にならない・黙って skip にもならない。前提が在る時の検の失敗は赤のまま(未実行へ化けない) |
| M4 codex-worker-test(doeff-conductor の検) | 本物の codex worker の schema つきの答えを確かめる | e2e の母集団であること | — | `@pytest.mark.e2e`。前提は M3 に聞く(`shutil.which` をやめる) | 本物のモデルの呼び出し(e2e のときだけ) | 検 1 回 | 日次(`-m 'not e2e'`)は本物のモデルを呼ばない |
| M5 fresh-interpreter-import-test(doeff-agents の検) | Hy を import する Python の module が、新しい interpreter でも自分で hy を読み込んでから import できることを確かめる | 子の process の bytecode の置き場(session ごとの**私的な置き場**・木は汚さない)・子の期限は検 1 本の上限より短い | 走行全体の bytecode の設定(conftest の固定)は変えない | 静的な検(各 importer が Hy の module より先に `import hy` する)を足し、動的な検は子の cache を共有する | 一時 dir・子 process | session | 子の超過は**その検の赤**になり、process を殺さない。木に `__pycache__` を作らない |
| M6 per-test-deadline(pyproject と根の conftest) | 1 本の時間切れを 1 本の赤にし、走行を続ける。止まった C 拡張は watchdog が最後に殺す | pytest-timeout の方式 = signal(POSIX の main thread) | — | `timeout_method = "signal"`。自前の SIGALRM を使う根の検 4 file は `@pytest.mark.timeout` に移す | signal handler | session | 時間切れの 1 本の後の検も走る。watchdog は 1 本の上限より後 |
| M7 daily-failure-names(dotfiles `land.py` の pytest の行) | 日次の出力から落ちた検の名前を読む | pytest の session の区切り(開始の見出しと集計行)・「short test summary info」の区画 | — | (a) 集計行の無い session は「中断」の名前を 1 つ出す(最後に見えた検の file か、時間切れの stack の検の名) (b) FAILED / ERROR は各 session の**最後の** short test summary の区画からだけ読む | なし(純関数) | 1 回の読み | 測れなかった範囲が名前 0 本にならない。内側の run の行を外側の失敗名に数えない |
| M8 check-layer(dotfiles `remote_check`・変えない) | 未実行の語彙と申告行の書式の単一定義点 | `UNEXECUTED_MARK`・`UNEXECUTED_KINDS`・`unexecuted_line`・`parse_unexecuted_all` | — | 既存のまま | — | — | 読み手・書き手が綴りの写しを持たない |

組み立ての点から無くなる判断: codex の検が自分で道具の有無を判じる所(M4 → M3)・子 process の期限を検が固定値で持つ所(M5 の 120 秒 → 検 1 本の上限から導く)。

## 4. 変更シナリオと事前の主張(反例の前に固定)

| id | 軸 | 変わる要求 | 主張 | 変わると予想する module | 変えない module と契約 | 範囲に収まる理由 |
|---|---|---|---|---|---|---|
| S1 | effects | 巡回が turn-record に書く材料が増える(引数の追加・既定値つきの引数の追加・引数の並べ替え)、または註が呼びの中に入る | M1 は変えずに緑のまま。usage・entries・responses のどれかに材料を渡す変更だけが赤 | なし | M1・judgment の公開の引数名 | 役の位置を呼び先の定義から読み、呼びの引数を `call-args-of` で分け、註と文字列を落とした行を読むから |
| S2 | concurrency | 検の実行順・同じ process で走る他の検の集合が変わる(根の母集団に package の検が足される・xdist) | M2 の答えは順に依らない | なし | M2・doeff-adr の plugin の import の規則 | 内側の run が別の process で、外側の `sys.modules` を共有しないから |
| S3 | hardware | 日次の機体が変わる(codex の実体が無い・router の入口だけ在る・codex が在る) | 道具が無い機体では codex の検は赤ではなく未実行の申告 1 行(kind tool-absent)になる。在る機体では本物の経路を走り、失敗は赤 | なし(機体の差は M3 の観測の答えだけが吸収する) | M3・M4・M8 の書式 | 前提の観測が M3 の 1 点で、書式は M8 から読むから |
| S4 | effects | 本物の外部サービスを呼ぶ検を増やす | 日次は本物を呼ばない。新しい検は e2e の印と M3 の前提を付けるだけ | 新しい検の file だけ | M3・M7・M8・日次の宣言 | 母集団の規則(e2e の印)と前提の観測(M3)が 1 点ずつだから |
| S5 | storage | 日次の bytecode の設定が変わる(`PYTHONDONTWRITEBYTECODE`・`PYTHONPYCACHEPREFIX` の有無) | M5 の所要は設定に依らず「cache 無しの compile 1 回分 + 小さな読み込み × 19」。木に `__pycache__` を作らない | なし | M5・根の conftest の固定 | 子の env を M5 が自分で組み、私的な置き場を指すから |
| S6 | hardware | 日次の機体が遅い(負荷・free-threaded の Python)・速い | 遅い機体で上限を超えた検は**その 1 本の赤**になり、package の残りは走る | なし | M5・M6 | M6 が 1 本の時間切れを `pytest.fail` にし、M5 の子の期限が 1 本の上限より短いから |
| S7 | distribution | 日次がどの機体・どの処理ステージで走っても、出力の読み取りは同じ(pod ↔ Mac・遠隔 ↔ 手元) | 中断した session は必ず名前が出る。内側の run の行は数えない | M7(dotfiles)だけ | doeff 側・M8 | pytest の出力の形(session の見出し・集計行・short test summary)は機体に依らないから |
| S8 | simulation | 本物の codex の代わりに替え玉の実行子で決定的に確かめる | M3・M4 の振る舞いは替え玉(壊れた入口・正しい答え・誤った答え)で全部確かめられる | なし | M3・M4 | 前提の観測が「実体を実行して終了コードを見る」だけで、替え玉を PATH に置けば同じ経路を通るから |

主張の前提: pytest-timeout の signal 方式は POSIX の main thread でだけ働く(日次は Linux・開発機は macOS で、どちらも POSIX)。C 拡張の中で止まった検は signal でも割り込めず、watchdog(SIGKILL)が最後に殺す — これは今と同じ。未知の将来をすべて予測できるとは主張しない。

## 5. 強制の方法

| 守る責務 | 強制の方法 | 実装の箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1 の読み方(役で読む) | ADR-012 の冊の検査そのもの。正常例(引数の追加・註)と違反例(usage に値・keyword で responses)を同じ冊の反例の検に足す | `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` | 日次の root 処理ステージ・変更時の focused な実行 | 引数名そのものを改名したら検査は「役が見つからない」で赤になる(意図した契約の変更 — 失敗文でそう言う) |
| M2 の独立性 | 検の中で外側に `tests` を先に置いてから内側を走らせる(順に依らず毎回確かめる) | `packages/doeff-adr/tests/test_wiring.py` | root 処理ステージ | 他の in-process の pytester の検が同じ穴を持つかは検が 1 本ずつ守る(全体の規則は置かない — 置く根拠の実例が 1 件だけ) |
| M3 の「前提の欠けは申告・前提ありの失敗は赤」 | M3 自身の検(替え玉の実行子で 3 通り)と、申告行を dotfiles の `parse_unexecuted_all` で読み戻す検 | 根の `conftest.py` と `tests/test_machine_premise.py`(仮) | root 処理ステージ | 検査層が読めない機体(素の clone)では申告しない(skip の理由だけ) |
| M4 の母集団 | `@pytest.mark.e2e`。日次の宣言(`-m 'not e2e'`)はそのまま | `packages/doeff-conductor/tests/test_agent_effect_c1.py` | packages 処理ステージ(選ばれない)・e2e の実行 | e2e を自動で走らせる所は今は無い(母集団から外れたことを #639 と commit に書く) |
| M5 の予算 | 子の期限を検 1 本の上限から導く(固定値を持たない)・静的な検を足す | `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py` | packages 処理ステージ | cache 無しの compile 1 回分がそれでも上限を超える機体では赤(名前つき)になる |
| M6 の方式 | ini の値と、「遅い 1 本の後の検も走る」を subprocess の pytest で確かめる検 | `pyproject.toml`・根の `conftest.py`・`tests/test_deadline_fails_one_test.py`(仮)・SIGALRM を自前で使う 4 file | root 処理ステージ | C 拡張の中の停止は watchdog(今と同じ) |
| M7 の読み取り | 実際の 09-26 の log を材料にした検(中断の名前・内側の行を数えない) | dotfiles `agentcli/src/agentcli/land.py` と tests | dotfiles の変更時の検査 | pytest 以外の書式は対象外 |

## 6. 実装の依頼の分け方(予定)

- 依頼 W(M2): eae6a774 の担当の分 — 受付経由(担当の会話の pane が見つからない)。
- 依頼 N(M1): f3858b94 の担当 c-AE7B1DTZ56D1HF1SDWN35WJRYW。
- 依頼 C(M3・M4): 計画の担当の lane(受付経由)。
- 依頼 T(M5・M6): 計画の担当の lane(受付経由)。M6 を先に入れる(M5 の赤が残っても package の残りが走る)。
- 依頼 X(M7): dotfiles の lane(受付経由・repo dotfiles)。
