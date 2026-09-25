# doeff の日次の全体検証の赤(2026-09-26)の当日修理 — 設計(盲検の反例を反映した版)

- 依頼: lt-FPBECBQ3W22ESC92VAMCENR5JR(class investigate・agora-redesign #639)
- 基準の版: doeff 本線 `7ef0fa772afca54684d5b75b51f286e6e1bb367d`
- 完了の範囲: **設計まで**。実装は class dev の別の依頼(この依頼を親に名乗る)で行い、依頼者の側(この会話)が検収する。本文の「予定」は未実施、「実測」は下の検証の記録に実行の証拠がある。
- 設計者: 会話 c-8JYPFZ9T1AFVA8C10W51M547JW(claude-opus-5-5)
- 盲検の前に固定した設計と事前の主張: `design-before-blind.md`(sha256 `b7768500c08aadd8ce3671830ee48392b0219e99f1e8d3cee1384010b22cc77f`)。この本文は反例を受けた改訂で、事前の主張は書き換えずに §4 で実測と並べる。

## 1. 固定した受入条件

`design-before-blind.md` §1 のとおり(変えていない)。要約:

1. 日次の記録で、ADR 3 本・release の区分・wiring とその内側の ERROR が失敗名の一覧から消える。
2. ADR-DOE-AGENTS-012 の検査が、註や正当な引数の追加で赤にならない。同じ族の検査が同じ理由で赤にならないことを反例で示す。
3. codex worker の検は、環境が無い機体では赤ではなく未実行として名乗る(agent-control-plane の #629 の依頼 A と同じ形 = dotfiles の検査層 `remote_check` の未実行の申告行を、書式の持ち主から読んで書く)。
4. 日次の判定が green か、unexecuted の理由が環境の欠けだけになる。
5. 日次で測られていない範囲が「測った・赤なし」の顔をしない。

このうち 1 の ADR 2 本(deff の台帳・macro の名簿)と release の区分は、既存の ADR の規則どおりに直せるので設計の検証の対象外とし、先に実装の依頼 1〜3 で配った(#639 の comment 5838935245)。この本文の対象は、構造の判断が要る 5 つ(ADR-012 の検査・wiring の検・codex の検・doeff-agents の検の中断・日次の失敗名の読み取り)。

## 2. 現状の事実(実測・版 7ef0fa77)

F1〜F6 は `design-before-blind.md` §2 のとおり。盲検と最小実験で次を足した。

| 事実 | 実測 |
|---|---|
| F7: watchdog の終わり方 | 根の conftest の watchdog は自分の process に SIGKILL を送る。取り込みの道具は、根の処理ステージの終了コードが負(信号)なら「外乱」と読み、赤とは数えない(dotfiles `land.py` の結末の分類)。つまり C 拡張の中で止まった検は、今の形では**赤として名乗られない**。watchdog の文言にも検の名が無い(盲検 A の再現 `evidence/A-repro.log`) |
| F8: 冷えた bytecode での所要 | 版 7ef0fa77・手元の Mac・bytecode の書き込み無し: `sessionhost.acp.host_slot_cli` の子 19.7 秒、`sessionhost.acp.runtime` の子は単独で 38.8 秒(`evidence/M5-C15-*.log`)。日次の pod は runtime で 90 秒の上限を超えた |
| F9: 取り込みの作業枠 | 取り込みの作業枠(`~/.worktrees/doeff-land`)は候補ごとに使い回され、ignore 対象の生成物を温存する。2026-09-26 06:00 JST の時点で、その木には `.pyc` が 1 つも無い(venv の中も 0)。日次の作業樹は走行ごとに作って捨てる(`doeff-verify-11` は走行の後に無い) |
| F10: pytest-timeout の方式 | signal 方式なら時間切れの 1 本だけが赤で、後の検も走る。thread 方式は process を終える(`evidence/M6-C12-*.log`)。自前の SIGALRM を使う根の検 5 file は signal 方式でも 15 本全部緑(`evidence/M6-C14-*.log`) |

## 3. 責務(module)と公開の契約(改訂)

改訂した所は「改訂」の列に書く。書いていない module は `design-before-blind.md` §3 と同じ。

| id | 責務 | 持つ知識 | 隠す知識 | 公開の契約 | 外への作用 | 寿命 | 守る不変条件 | 改訂 |
|---|---|---|---|---|---|---|---|---|
| M1 adr012-sweep-check | ADR-012 R49 の巡回の構造の検査 | usage・entries・responses の役を持つ引数を**呼び先の defk の引数の並びから導く**読み方 | 呼びの行の字面・折れ方・註・局所変数の名 | Hy の reader で呼びを読み、役ごとに値を確かめる。失敗文は「どの役に何が渡っているか」を言う | なし(file を読むだけ) | 検査 1 回 | 巡回の中の全ての呼びで usage は `None`・entries は `#()`・responses は渡さないか `None`。呼びが 1 つ以上ある | なし |
| M2 wiring-hermetic-test | 新しい pytest の process が package の下の Hy の検をどう import するかを示す検 | 内側の run は別の process | 外側の process の `sys.modules` | `pytester.runpytest_subprocess`。外側に `tests` を先に置いても緑になることを自分で示す | 一時 dir の子 process | 検 1 回 | 内側の結果が外側の import の履歴に依らない | なし |
| M3 machine-premise(根の conftest) | 検の前提(起動できる道具)がこの機体に無いことを判じ、skip にしつつ未実行として名乗る | 前提の観測 = 道具を 1 回起こして終了コード 0 か/申告行の書式の持ち主(dotfiles `remote_check` を file-path で読む) | 申告行の綴りと語彙の値(dotfiles が持つ) | fixture `machine_tool(name, *probe)` → 起動できた道具の path か skip(理由は接頭辞 `machine premise unmet:`)。`pytest_terminal_summary` が skip の報告を集め、検査層の `unexecuted_line(["tool-absent"], …)` で 1 行書く。検査層の無い機体では skip の理由の要約だけ | stdout へ申告行 1 行 | session | 前提の欠けは赤にならず、黙って skip にもならない。前提が在る時の検の失敗は赤のまま | 実測で確定(fixture の名を `machine_tool` にした) |
| M4 codex-worker-test(doeff-conductor) | 本物の codex worker の schema つきの答えを確かめる | e2e の母集団であること | — | `@pytest.mark.e2e`。前提は M3 の `machine_tool("codex")` に聞く(`shutil.which` をやめる) | 本物のモデルの呼び出し(e2e の時だけ) | 検 1 回 | 日次(`-m 'not e2e'`)は本物のモデルを呼ばない | なし |
| M5 fresh-interpreter-import-test(doeff-agents) | Hy を import する Python の module が、新しい interpreter でも自分で hy を読み込んでから import できることを確かめる | 子の bytecode の置き場 = **pytest の一時 dir の下の、この module の走行だけの dir**(`tmp_path_factory`)・子の期限 = 検 1 本の期限 − 15 秒 | 走行全体の bytecode の設定(M9 の固定)は変えない | module scope の fixture `private_bytecode_dir` と `child_deadline`。子の env だけ `PYTHONDONTWRITEBYTECODE` を外し `PYTHONPYCACHEPREFIX` をその dir へ向ける。子の期限切れは `pytest.fail("<module> did not finish within Ns …")` | 一時 dir・子 process | module の走行 | 子の超過はその検の赤で、process を殺さない。木に bytecode を書かない(M9 が走行の終わりに確かめる) | **改訂**: 静的な検を足す予定をやめた(共有の私的な置き場だけで 22 本 42 秒 — 実測 C16)。木を汚さないことの強制を M5 の約束から M9 の検査へ移した(盲検 B) |
| M6 per-test-deadline(pyproject と根の conftest) | 1 本の時間切れを 1 本の赤にして走行を続ける。signal の届かない停止は watchdog が最後に終える | pytest-timeout の方式 = signal/watchdog が今どの検を見張っているか(nodeid) | — | `timeout_method = "signal"`。watchdog は時間切れの時、捕獲を外して pytest の short test summary の形の 1 行 `FAILED <nodeid> - WATCHDOG: …` を書き、**終了コード 1** で終える(SIGKILL をやめる) | 信号の handler・終了 | session | 時間切れの 1 本の後の検も走る。watchdog で終わった走行は、止まった検の名前つきの赤になる | **改訂**(盲検 A): watchdog が nodeid を持ち、名前を書いてから通常の終了コードで終える。自前の SIGALRM を使う 5 file は変えない(C14 で緑) |
| M7 daily-failure-names(dotfiles `land.py`) | 日次の出力から落ちた検の名前を読む | pytest の session の区切り(開始の見出し・集計行)と short test summary の区画 | — | (a) 集計行の無い pytest の session は、最後の進捗の行より後の FAILED/ERROR 行(M6 の watchdog の行)を、それも無ければ最後に進捗を出した file の path を名前にする (b) 閉じた session の FAILED/ERROR は、その session の**最後の** short test summary の区画(集計行の直前・間に別の集計行が無いもの)からだけ読む。pytest の見出しも集計行も無い出力(build・cargo)には当てない | なし(純関数) | 1 回の読み | 測れなかった範囲が名前 0 本にならない。内側の run の行を外側の失敗名に数えない | 改訂: (a) の材料に M6 の watchdog の行を足した(盲検 A)。pytest 以外の出力に当てないことを契約に足した(試作で build・rust の処理ステージに当たった) |
| M8 check-layer(dotfiles `remote_check`・変えない) | 未実行の語彙と申告行の書式の単一定義点 | `UNEXECUTED_MARK`・`UNEXECUTED_KINDS`・`unexecuted_line`・`parse_unexecuted_all` | — | 既存のまま | — | — | 読み手・書き手が綴りの写しを持たない | なし |
| **M9 checkout-bytecode-guard(根の conftest・新規)** | 走行が checkout の中に bytecode を書かないことを、走行の終わりに実物で確かめる | 走行の開始時刻と checkout の根(`.git` を除く全て) | 子 process がどう設定されたか(見るのは結果の file だけ) | 既存の session fixture `_bytecode_settings_pinned` の終わりに、開始時刻より新しい `*.pyc` を checkout の中から探し、在れば path を挙げて失敗 | 木を読むだけ(Mac で約 0.2 秒) | session | 走行の後の checkout に、その走行が書いた bytecode が無い | **新規**(盲検 B) |

組み立ての点から無くなる判断: codex の検が自分で道具の有無を判じる所(M4 → M3)・子 process の期限を検が固定値で持つ所(M5 の 120 秒 → 検 1 本の期限から導く)・watchdog の終わり方を読み手が推し量る所(M6 が名前と終了コードで言う)。

結合核との突合: `docs/crystallization/constraint-graph.md` の K1(VM と scheduler の所有権)・K2(捕獲)には、どの module も触れない。触るのは検の file・根の conftest・pyproject の pytest の設定・dotfiles の読み取りだけ。

## 4. 変更シナリオ — 事前の主張と実測

事前の主張(`design-before-blind.md` §4)は書き換えずに要約し、実測の範囲と並べる。

| id | 軸 | 事前の主張(盲検前) | 予想した範囲 | 実測の範囲 | 差の理由 | 修正と再検証 |
|---|---|---|---|---|---|---|
| S1 | effects | 巡回が turn-record に書く材料が増えても(引数の追加・既定値つきの引数・註)M1 は変えずに緑。usage・entries・responses に材料を渡す変更だけが赤 | なし | なし(C01・C02 緑、C03〜C06 赤) | — | 不要(主張どおり) |
| S2 | concurrency | 検の実行順や同じ process の他の検が変わっても M2 の答えは変わらない | なし | なし(C07 緑、C08 で旧形の赤を再現) | — | 不要 |
| S3 | hardware | 道具が無い機体では codex の検は未実行の申告 1 行(tool-absent)。在る機体では本物の経路で、失敗は赤 | なし(M3 の観測だけが吸収) | なし(C19・C19 読み戻し・C20・C22) | — | 不要。検査層の無い機体では申告を見送る形を実測で確かめた(C22) |
| S4 | effects | 本物の外部サービスを呼ぶ検が増えても、日次は本物を呼ばない。新しい検は e2e の印と M3 の前提を付けるだけ | 新しい検の file だけ | 同じ(C21 で日次の母集団から外れる、C26 で旧形が母集団に入って赤になるのを再現) | — | 不要 |
| S5 | storage | 日次の bytecode の設定が変わっても M5 の所要は「cache 無しの compile 1 回分 + 小さな読み込み × 19」。木に `__pycache__` を作らない | なし | **M5 と、新しい M9**(盲検 B) | 盲検 B: 子の保存先を checkout の中の固定 dir に向ける変更が、M5 の既存の検も M6 の設定の検も通りながら、走行をまたいで残る共有の状態を木に作った(0 → 131 個)。「木を汚さない」は M5 の約束として書いただけで、どこでも検査していなかった。知識の漏れではなく、**強制の欠け**(不変条件の持ち主 = 走行の bytecode を固定している根の conftest が、結果を見ていなかった) | M9 を足した。B の候補は M9 で赤(C18・131 個を名指し)、M5 の改訂は緑(C16・C17)。M5 の所要は 22 本 42 秒(C16、旧形は 60 秒の手元の上限で打ち切り C15) |
| S6 | hardware | 遅い機体で上限を超えた検はその 1 本の赤になり、package の残りは走る | なし(M5・M6 の設計どおり) | M6 の watchdog の終わり方も変えた | 盲検 A と F7: signal の届かない停止は watchdog が SIGKILL で終え、根の処理ステージでは「外乱」と読まれて赤にならない | M6 の watchdog を「名前の行 + 終了コード 1」に変えた(C23)。signal 方式の 1 本の赤は C24・C12 |
| S7 | distribution | どの機体・処理ステージで走っても、中断した session は必ず名前が出る。内側の run の行は数えない。変えるのは M7 だけ | M7 だけ | **M7 と M6**(盲検 A) | 盲検 A: 根の処理ステージは `-q` で session の見出しも file の名も出ず、watchdog の文言にも検の名が無い。「いま走っている検」を知っているのは pytest の実行側で、読み手の側は推し量れない。実行側が名前を出す変更は**公開の契約の正当な拡張**(読み手が実行順から推し量るなら知識の漏れ) | M6 が pytest の失敗行の形で名前を出し、M7 は集計行の無い session でその行を読む。実際の 09-25・09-26 の log で、止まった doeff-agents の session を名指し、入れ子の `packages/doeff-x/tests/test_y.hy` を数えないことを確かめた(C25) |
| S8 | simulation | 本物の codex の代わりに替え玉の実行子で M3・M4 の振る舞いを全部確かめられる | なし | なし(C19・C20・C21・C22 は全部替え玉) | — | 不要 |

主張の前提(変えていない): pytest-timeout の signal 方式は POSIX の main thread でだけ働く(日次は Linux・開発機は macOS)。watchdog の thread が動けること(free-threaded の Python 3.14t では GIL が無い)。未知の将来をすべて予測できるとは主張しない。

## 5. 強制の方法(改訂)

| 守る責務 | 強制の方法 | 実装の箇所(予定) | 実行経路 | 限界 |
|---|---|---|---|---|
| M1 の読み方(役で読む) | ADR-012 の冊の検査そのもの。正常例(引数の追加・註・keyword の既定値)と違反例(usage に値・keyword で responses・entries に値・呼びを消す)を同じ冊の反例の検に足す | `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` | 日次の root 処理ステージ・変更時の名指しの実行 | 引数名そのものを改名したら「役が見つからない」で赤になる(意図した契約の変更 — 失敗文でそう言う) |
| M2 の独立性 | 検の中で外側に `tests` を先に置いてから内側を走らせる | `packages/doeff-adr/tests/test_wiring.py` | root 処理ステージ | 他の in-process の pytester の検が同じ穴を持つかは、検が 1 本ずつ守る(全体の規則は置かない — 実例が 1 件だけ) |
| M3 の「前提の欠けは申告・前提ありの失敗は赤」 | M3 自身の検: 替え玉の実行子を PATH に置いた subprocess の pytest で、終了コード 127 の入口 → skip と申告行(検査層の `parse_unexecuted_all` で読み戻す)/起動するが壊れた答え → 赤で申告なし/HOME に検査層が無い → 申告を見送る | 根の `conftest.py`・`tests/test_machine_premise.py` | root 処理ステージ | 検査層が読めない機体(素の clone)では申告しない。道具の資格(login)の欠けは前提の観測に入れない(`--version` が通れば本物の経路へ進み、失敗は赤) |
| M4 の母集団 | `@pytest.mark.e2e`。日次の宣言(`-m 'not e2e'`)はそのまま | `packages/doeff-conductor/tests/test_agent_effect_c1.py` | packages 処理ステージ(選ばれない)・e2e の実行 | e2e を自動で走らせる所は今は無い(母集団から外れたことを #639 と commit に書く) |
| M5 の予算と置き場 | 子の期限を検 1 本の期限から導く(固定値を持たない)・子の置き場は `tmp_path_factory` の下 | `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py` | packages 処理ステージ | cache 無しの compile 1 回分がそれでも期限を超える機体では、その検が名前つきの赤になる |
| M6 の方式と watchdog の名乗り | ini の値の検と、subprocess の pytest で「遅い 1 本の後の検も走る」「signal を遮って止まった検は watchdog が名前の行を書いて終了コード 1 で終える」を確かめる検 | `pyproject.toml`・根の `conftest.py`・`tests/test_deadline_fails_one_test.py` | root 処理ステージ | C 拡張が Python の thread まで止める停止(GIL を持つ build で GIL を離さない)は、今と同じく watchdog も動けない |
| M7 の読み取り | 実際の 09-25・09-26 の log の該当部分を材料にした検(中断の名前・watchdog の行・内側の行を数えない・pytest 以外の出力に当てない) | dotfiles `agentcli/src/agentcli/land.py` と tests | dotfiles の変更時の検査 | pytest 以外の書式は対象外(従来の処理ステージ名の合成が受ける) |
| M9 の「走行は木に bytecode を書かない」 | 根の conftest の session fixture の終わりの検査。subprocess の pytest で、木の中へ bytecode を書く検を走らせると走行が失敗し、path を名指すことを確かめる検 | 根の `conftest.py`・`tests/test_bytecode_settings_pinned.py` | 根から走る全ての pytest(root・packages 処理ステージ・手元の名指しの実行) | 古い時刻を付けて書かれた file、`.git` の中は見ない |

## 6. 盲検の記録

どちらも同じ機体(proboscis-mbp・個人の Mac)で、この会話とは文脈を共有しない新しい codex の process として起こした。

| | A(波及の反例) | B(検査を通る違反の反例) |
|---|---|---|
| model・effort | GPT-6 Astra(`gpt-6-astra`)・reasoning effort low | 同じ |
| profile・起動口 | `cx personal exec`(個人の profile。会社の profile は使っていない) | 同じ |
| session id | 01a0da40-b6e8-7690-a03c-6417aba2eeca | 01a0da40-b67c-7da1-883e-8ebc5276699d |
| 起動 | 2026-09-25T20:27:48Z(`blind/launch_blind.py` — 新しい process group・herdr の env を外す・入力は stdin) | 同じ |
| 返答の時刻・所要 | 2026-09-26 05:29:48 JST・2 分 0 秒 | 2026-09-26 05:31:08 JST・3 分 20 秒 |
| 実際に観測したモデル | codex の実行の log の見出し: `model: gpt-6-astra`・`reasoning effort: low`・`provider: openai` | 同じ |
| 選んだ理由・fallback | skill の優先順の 1 番(gpt-6-astra・low)。fallback なし・再試行なし | 同じ |
| 作業の木 | `/tmp/wt639-blind-A/tree`(基準の版の写し・.git 無し・専用の venv) | `/tmp/wt639-blind-B/tree`(同じ作り方) |
| 入力 | `blind-a-input.md` sha256 `db745988…52cd5` | `blind-b-input.md` sha256 `6619fb66…ca0cb09` |
| 未加工の返答 | `blind/blind-a-return.md` | `blind/blind-b-return.md` |
| 渡さなかったもの | 設計者の自己評価・既知の反例・望む結論 | 同じ |

入力と共通資料は skill の本文を逐語で含むので、公開の repo には置かず、この機体の `~/.local/state/design-check/lt-FPBECBQ3W22ESC92VAMCENR5JR/blind/` に置いた(codex の実行の log も同じ所)。hash は報告の JSON にある。

### 盲検 A の反例と扱い

主張: 遅い機体で watchdog が止めた検を名前つきで報告する要求は、M7 だけでは満たせない(根は `-q` で見出しも file 名も出ず、watchdog の文言に検の名が無い)。

再現: A の限定検証 2 本(`evidence/blindA-test_m7_evidence.py.txt`(盲検 A が書いた検。証拠なので中身を変えず、コードの検査の対象から外すため拡張子に .txt を足した — `evidence/blindB/reproduce.py.txt` も同じ)・`evidence/A-repro.log` 2 passed)で、`-q` で見出しが出ないこと、watchdog の文言に識別子が無いことを確かめた。F7(信号の終了は根の処理ステージで赤と数えられない)を足すと、名前が出ないだけでなく赤にもならない。**成立**。

修正: M6 の watchdog が nodeid を持ち、pytest の失敗行の形で書いて終了コード 1 で終える(C23 で確かめた — 捕獲を外さない最初の試作は名前が出なかったので、pytest-timeout の thread 方式と同じ手順で捕獲を外す形に直した)。M7 は集計行の無い session でこの行を読む(C25)。

### 盲検 B の反例と扱い

主張: 子の保存先を package の中の固定 dir(`.hy-import-cache`)にする「普通の高速化」は、M5 の既存の検と M6 の設定の検を通りながら、session をまたいで木に残る共有の状態を作り、M5 の「私的な置き場・木を汚さない」に反する。

再現: B の実測(`evidence/blindB/*`: 基準 0 → 0、候補 0 → 131 → 131・どれも終了コード 0)。自分の作業樹で同じ候補を当てると、M9 の無い形では通る。取り込みの作業枠は ignore 対象の生成物を温存して使い回す(F9)ので、名前が ignore の形(`__pycache__` 等)なら次の候補の走行へ持ち越される。**成立**。

修正: M9 を足した。B の候補は M9 で赤(C18・131 個を名指し)、M5 の改訂(一時 dir)は M9 の下で緑(C17)。

## 7. 検証の記録(最小実験)

実行はどれも手元の Mac の隔離した作業樹(基準の版 7ef0fa77 から作った `~/.worktrees/doeff-wt-639-verify` と `~/.worktrees/doeff-wt-639-m5`)で、名指しの実行だけ。コマンド・終了コード・出力は各 log の中にある。報告の JSON の `checks` が同じ一覧を持つ。

| id | シナリオ | 対照 | 何を | 結果 | 証拠 |
|---|---|---|---|---|---|
| C01 | S1 | 正 | 今の巡回の呼び(引数 4 つ)を試作の役の検査で読む | 緑 rc 0 | `evidence/M1-C01-base.log` |
| C02 | S1 | 正 | 呼びの中に註・keyword の既定値の引数を足す | 緑 rc 0 | `evidence/M1-C02-*.log` |
| C03 | S1 | 反 | usage に値を渡す | 赤「usage に 'usage-total を渡している」 | `evidence/M1-C03-*.log` |
| C04 | S1 | 反 | keyword で responses を渡す | 赤 | `evidence/M1-C04-*.log` |
| C05 | S1 | 反 | entries に値を渡す | 赤 | `evidence/M1-C05-*.log` |
| C06 | S1 | 反 | 呼びを消す | 赤「呼んでいない」 | `evidence/M1-C06-*.log` |
| C07 | S2 | 正 | 内側を別の process にし、外側に根の検を先に読ませる | 6 passed | `evidence/M2-C07-*.log` |
| C08 | S2 | 反 | 旧形(同じ process)で外側に `tests` を先に置く | 1 failed・ModuleNotFoundError | `evidence/M2-C08-*.log` |
| C12a | S6 | 正 | 遅い検を signal 方式で走らせる | 1 failed 1 passed | `evidence/M6-C12-method-signal.log` |
| C12b | S6 | 反 | 同じ検を今の thread 方式で走らせる(欠陥の再現) | 集計行なし・後の検が走らない | `evidence/M6-C12-method-thread.log` |
| C14 | S6 | 正 | 自前の SIGALRM を使う根の 5 file を signal 方式で | 15 passed | `evidence/M6-C14-*.log` |
| C15 | S5 | 反 | 旧形の M5 を冷えた bytecode で | 60 秒の手元の上限で打ち切り(15 本まで)・runtime 単独 38.8 秒 | `evidence/M5-C15-*.log` |
| C16 | S5 | 正 | 改訂の M5(共有の私的な置き場・子の期限) | 22 passed 42 秒・木に `.pyc` 0 | `evidence/M5-C16-*.log` |
| C17 | S5 | 正 | M9 の下で改訂の M5 | 22 passed | `evidence/M9-C17-*.log` |
| C18 | S5 | 反 | M9 の下で盲検 B の候補(木の中の固定 dir) | 1 error「131 bytecode file(s) were written under the checkout」 | `evidence/M9-C18-*.log` |
| C19 | S3・S8 | 正 | 終了コード 127 の替え玉の入口で `-m e2e` | 1 skipped・`check-unexecuted: kind=tool-absent …` 1 行・rc 0。dotfiles の `parse_unexecuted_all` と取り込みの道具の `gate_unexecuted_declarations` で読み戻せた | `evidence/M3-C19-*.log` |
| C20 | S3・S8 | 反 | 起動はするが壊れた JSON を返す替え玉 | 1 failed・申告行 0 | `evidence/M3-C20-*.log` |
| C21 | S4 | 正 | 日次の母集団(`-m 'not e2e'`)で改訂の M4 | 4 passed 1 deselected | `evidence/M4-C21-*.log` |
| C22 | S3 | 正 | 検査層の無い HOME で終了コード 127 の替え玉 | skip の理由の要約だけ・申告行なし | `evidence/M3-C22-*.log` |
| C23 | S6・S7 | 正 | signal を遮って止まる検で改訂の watchdog | rc 1・`FAILED …::test_blocks_signals_and_hangs - WATCHDOG: …` | `evidence/M6-C23-*.log` |
| C24 | S6 | 正 | signal 方式で遅い検の後の検 | 1 failed 1 passed | `evidence/M6-C24-*.log` |
| C25a | S7 | 正 | M7 の試作を 09-25・09-26 の日次の全文の log と watchdog の log に当てる | 止まった doeff-agents の session を 2 日とも名指し、watchdog の log も名指す | `~/.local/state/design-check/lt-FPBECBQ3W22ESC92VAMCENR5JR/evidence/M7-C25-real-logs.log` |
| C25b | S7 | 反 | 同じ試作が根の入れ子の ERROR を数えないこと | 今の読み取り 6 名・session の読み取り 5 名(`packages/doeff-x/tests/test_y.hy` を数えない) | 同上 |
| C26 | S4 | 反 | 基準の版の母集団に codex の検が入り、終了コード 127 の入口で赤 | 1 failed 4 passed | `evidence/M4-C26-*.log` |
| C27 | S7 | 反 | 盲検 A の再現: 旧形の watchdog の文言に検の識別子が無く、`-q` では見出しも出ない | 2 passed | `evidence/A-repro.log`・`evidence/blindA-test_m7_evidence.py.txt` |
| C28 | S5 | 反 | 盲検 B の再現: M9 の無い旧形では候補が検を通り木に 131 個を残す | 基準 0→0・候補 0→131→131・どれも rc 0 | `evidence/blindB/*` |

試作の差分は `proto/`(doeff 側)と `~/.local/state/design-check/lt-FPBECBQ3W22ESC92VAMCENR5JR/proto/`(dotfiles 側の M7)にある。**本実装の検証は各実装の依頼の受入条件で改めて行う**(ここの実験の成功を本実装の検証に流用しない)。

## 8. 実装の依頼の分け方

| 依頼 | module | 担当 | 順序と依存 |
|---|---|---|---|
| N | M1 | f3858b94 の担当 c-AE7B1DTZ56D1HF1SDWN35WJRYW | 独立 |
| W | M2 | 受付経由(eae6a774 の担当の会話の pane が見つからない) | 独立 |
| C | M3・M4 | 受付経由 | 独立 |
| T | M6 → M5 → M9 | 受付経由 | M6 を先に入れる(M5 の赤が残っても package の残りが走る)。M9 は M5 と同じ取り込みか後(M5 の旧形は木に書かないので順序の制約は無い) |
| X | M7 | 受付経由・repo dotfiles | 独立。doeff 側の M6 の watchdog の行の形(pytest の失敗行)に依るが、その形は pytest の既存の形なので同じ日に入らなくても壊れない |

期限はどれも 2026-09-27 02:00 JST までに本線へ取り込む(03:30 の日次の前)。

## 9. 残る限界(未解決ではなく、受け入れた限界)

- doeff-agents の package の検は 2 日間約 94% 測られていない。T が入ると翌朝の日次で未知の赤が出る可能性がある(53a5e5e2 の開発機の全体実行では 21 件の失敗)。広範囲の実行は日次に任せ、事前に全数を走らせない(全体の実行は承認が要る)。packages 処理ステージの期限は 3600 秒。
- M9 は古い時刻の file と `.git` の中を見ない。
- M3 は道具の資格(login)の欠けを前提に入れない。
- M7 は pytest の出力の形にだけ当てる。
