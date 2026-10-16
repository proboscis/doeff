# 修正と再検証の記録(設計検証 lt-QBV8JVMD4WECA7E466E79HMWEQ)

- 事前の主張: design.md(sha256 ba492ac44b862104eb95f696ced775c3f3658dde17685f106167adc36a7e013d — 盲検の前に固定し、以後書き換えていない)
- 実装の版: e8d9481453ebea5511faa94ff2c7e109080fc45c(L2333 で着地)
- 修正の版: 8e9a0d3e22602e58e3bd796673054090db9595cf(L2336 で着地)。実験は修正を 3 つの commit に分けた版 75732ed2b1d4ed332e719c0dfa15680a63ad299d の上で走らせ、着地の前に 1 つの commit にまとめた(まとめた commit の木は 75732ed2b を origin/main の fc2c893f8 の上へ載せ替えた木と同じ — `git diff wt/stall-design-check 8e9a0d3e2` が空)。
- 完了の範囲: 実装完了まで。本番の coordinator の版は、頼み手が別の card(ki-e29a3fdde7b9)で e8d948145 へ上げた(fc2c893f8)。修正の 8e9a0d3e2 を本番へ載せるには、もう 1 度の版上げが要る(この検証の範囲の外)。

## 盲検 A — ずらす欄の一覧の漏れ(成立)

- 失敗例: 期限つきで頼み直す状態(cordon)を足すと、その持ち主の module に加えて M1 の clocks-shifted のずらしの一覧も変える必要があり、忘れると止まりの直後にその期限だけが切れる。同じ形の漏れが既存の drain の until-ms・盤の TTL の行の expires-ms に在る(盲検 A が実測)。
- 再現(修正前): evidence/E8-blind-A-repro-before.log — e8d948145 で、止まりの直後と起き直しの後に drains = []・盤の presence-x が消え、GET /workers/w の drain が None になる。
- 判定: 隠すはずの知識の漏洩(期限が頼み直しで延びるかという持ち主の判断を M1 が持つ)。境界は動かさず(task・worker・入れ替えのずらしも既に M1 に在り、持ち主へ移すと起き直しの道ごと作り直しになる)、M1 に ClusterState の欄ごとの分類(STALL-SHIFTED-FIELDS / STALL-UNSHIFTED-FIELDS)を必ず書かせ、test で強制した。漏れていた drain・温める表・盤の TTL の行・退いた世代の最後の報告(SupersededRun.seen-ms — 欄を数え上げて見つけた)はずらす側へ足した(起き直しの道も同じ)。
- 再検証(修正後): evidence/E8-blind-A-repro-after.log(drain は untilMs 241000 で残り、盤の行も expires 241000 で残る)・E4(分けていない欄 cordons を test が断る)・E5(drain のずらしを外すと test が断る)・E0(修正後の 14 本が通る)。

## 盲検 B — 調停ループの側の 2 つ目の止まりの定義(成立)

- 失敗例: program.hy の coordinator-step が stall-of を通さずに、寝過ごしが HEARTBEAT-LAG-MS(5 秒)を越えた歩で自分の Stall(gap = 寝過ごし)を作ってずらす。提示した 4 つの検査(判断の test・模擬の test・static_stub --check・defk の契約)は全部通る(盲検 B が実測)。
- 判定: 責務の侵入(止まりの定義・閾・gap の意味が M1 の外に 2 つ目として現れる)。提示した検査が M2 の水準を縛っていなかった。
- 修正: test_coordinator_stall_loop.hy を足した — 本物の run-coordinator を台本の受付と仮想の時計で回し、lease 以下の寝過ごし(7 秒)・遅い歩(7 秒)では止まりの行を出さず、lease を越えた寝過ごし(30 秒)・遅い歩(15 秒)では stall-of の内訳どおりの行がちょうど 1 つ出る事を縛る。
- 再検証: evidence/E2-blind-B-loop-redefines-stall.log — 盲検 B の差分をそのまま当てると、test_a_short_oversleep_is_no_stall_in_the_loop が [StallLine(gap_ms=7000, busy_ms=0, overslept_ms=7000)] で落ちる(pytest の終了 1)。
- 残る穴: Stall を M1 の外で作る事そのものを静的に断る規則は足していない(ループの test は振る舞いで断る — 閾より下の寝過ごしと遅い歩、閾より上の内訳)。

## 盲検 A の別件 — 見つけない止まりの幅(文言の修正)

- 「lease-ms 以下の止まりは見つけない」は、予定の起きる刻までの眠りを数えないので、起きる予定が前の歩から lease 近く先なら最大でおよそ lease の 2 倍まで見つけない(盲検 A が side_band で実測)。law の式とは合っていたので、api_policy の註と ADR の R8 の文を直した(振る舞いは変えない)。

## 自分で足した確かめ(盲検の外)

- 保存(S2): 模擬の test に、止まりが明けた歩の保存に生存の印(counter の aliveMs = その歩の刻)とずらした worker の印(worker/w1・worker/w2)が載る事を足した。SaveState に歩の前の状態でなくずらした状態を渡す変異を、この確かめが断る(evidence/E3-unpersisted-shift.log)。
- 閾の 1 か所(S1): 閾を M1 の stall-of だけで変えると、閾の境を名指す判断の test 1 本だけが落ち、ループと模擬の test は M1 の変更だけで新しい閾に従う(evidence/E1-threshold-only-in-M1.log)。
- 起き直しの道の回帰: drain・盤の TTL・温める表・退いた世代を起き直しでもずらすようになったので、それらに触れる既存の test 36 file(318 本)を回した — 317 passed・1 failed(型の宣言の test の pytest-timeout 103 秒 — 機体の負荷)・3 errors(teardown の .pyc の見張り — 新しい作業木の初回の import の書き)。この 4 件を単独で走らせ直して 6 passed(evidence/regression.log・evidence/regression-rerun.log)。
