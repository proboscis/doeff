# 設計検証 lt-TH2ZXPJ9QPXBB8EV6SNEWN1N89 — 事前の主張(盲検の前に固定)

- 完了の範囲: **実装完了まで**。doeff main に着地済み(L2198・cc8f9e7dfe09dba03b6ecb488222cb84c68e449e)。基底 = 6f439141223231fd40ba86a7640f3548409c7a6d。
- card: agora-redesign ki-26ebd3f636e0(親 ki-aabe38bf85ef の子 G)。

## 要件(固定)

- 受入 c07865153: 会話が直に doeff-hy-check を起動しても、機体の検の枠を取ってから走る事を、枠を埋めた失敗ケースの検で確かめて doeff main で緑(直す前は枠を取らずに走る)。
- 親の前提: 1 枠 = 検の process 木 1 つ。枠を持つ process の子孫は 2 つ目を取らない。値(K・置き場・待ちの上限)は正本の宣言 `machine_check_slots.json` 1 つが持ち、env で替える口は作らない。
- 利用状況: 同じ Pod(zeus の worker)で会話が数十並ぶ。doeff-hy-check は 1 本で展開の子 4 本と pyright を起こし、RSS 約 10 GiB を使う。撃つ道は 4 つ: 会話の直(`uv run doeff-hy-check …`・`python -m doeff_hy.static_check …`)、agora-controllers の手の入口 `hy_type_check.hy gate` の子(gate が枠を持つ)、日次の `hy_type_trend.hy`、pytest の中の in-process の `main([...])`。

## 責務

| id | 持つ判断・知識 | 隠す物 | 口 |
|---|---|---|---|
| M-canon(dotfiles `agent/tests/machine_check_slots.py` と `.json`) | 枠の値・取り方(flock・名乗り・待ち)・入れ子の判定(/proc の祖先の列・無ければ env の印 HOLDER_ENV)・待ちの上限で SlotWaitExpired | lock の file の形・ポーリング・祖先の判定 | `read_policy()`・`take(policy, label, environ, out)`・`PolicyUnreadable`・`SlotWaitExpired`・`HOLDER_ENV` |
| M-slot(doeff `packages/doeff-hy/src/doeff_hy/check_slot.py`) | 正本の在処(home からの相対 path)・正本を読む module の鍵・口が揃うかの判定・素通しの判断(正本が無い/読めない/口が揃わない/呼び方が合わない/宣言を読めない)・保持者の名乗りの形 | 正本の module の読み込み・正本の口の名 | `take_slot(home, label, out) -> SlotHeld \| SlotExpired`・`slot_label(root, conversation)`・`SlotHeld.release()` |
| M-entry(`static_check.main`) | 枠を持つ区間(引数の検めの後・展開と pyright の間)・待ちの上限の時の終了コード 2 | 枠の取り方 | CLI の終了コード 0 / 1 / 2 |
| M-env(`doeff_hy/env_places.py`) | doeff_hy で環境変数を読む唯一の所 | os.environ の読み方(foundation の答え手 env_var_ask) | `slot_holder_setting(name)`・`conversation_setting()` |
| M-callers(agora-controllers `scripts/hy_type_check.hy`・`scripts/hy_type_trend.hy`) | 終了コードの読み(0・1 以外 = 測れなかった)・gate は自分で枠を持つ | — | doeff-hy-check を子 process で撃つ |

## 変更シナリオと主張(盲検の前)

| id | 軸 | 変える要求 | 主張(局所化の理由) | 予想の波及 | 変えない |
|---|---|---|---|---|---|
| S1 | hardware | 機体の core が変わり K を 8 → 16 にする | doeff は値を読まず read_policy の答えをそのまま take へ渡すので、宣言の json の 1 行で済む | M-canon の宣言だけ | M-slot・M-entry・M-env・M-callers |
| S2 | storage | 枠の置き場を /tmp/agora-check-slots から別の dir へ移す | 同上(置き場も宣言の値) | M-canon の宣言だけ | M-slot・M-entry・M-env・M-callers |
| S3 | effects | 正本の口が dotfiles の先端で変わる(take の引数が増える・名が変わる)が、doeff は使い手の pin で古いまま | M-slot は口の名を確かめ、呼び方の食い違い(TypeError)を捕まえて 1 行名乗って素通しする。doeff-hy-check は落ちず、終了コードの語彙(0/1/2)は変わらない | M-canon(と、口を使い直す時の M-slot の 1 file) | M-entry・M-env・M-callers |
| S4 | effects | 枠が長く埋まり、待ちが上限を越える | M-entry は展開も pyright も起こさずに exit 2 で終わる。呼び手は既存の「0・1 以外 = 測れなかった」の読みのまま | なし(既に入っている) | M-callers |
| S5 | concurrency | 枠を持つ process の子孫として doeff-hy-check が撃たれる(gate の子・pytest の中・正本の run の子) | 判定は M-canon の take に任せ、M-slot は自分で枠を数えない。K = 1 で祖先が唯一の枠を持っても待たない(止まらない) | なし | M-canon・M-slot・M-entry |
| S6 | distribution | 機体 1 台 → cluster 全体で枠を数える | 適用外: 親の決め(Pod で共有する枠)の外。cluster で数える時は M-canon の take の中身の差し替えで、M-slot は口が同じなら変えない(前提の変更なので主張の外) | — | — |
| S7 | simulation | 決定的な模擬の上で枠の待ちを再現する | 適用外: 枠は OS の flock と実 process の資源で、doeff-hy-check は模擬の世界の Program ではない。検は実 process で行う | — | — |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 値を持たない(M-slot) | 失敗ケースの検が正本の**写し**の宣言(K = 1・検体の dir)で走る — doeff が値を写せば写しに従わず赤 | `packages/doeff-hy/tests/test_static_check_slot.py` | `make test-changed`(変えた検・逆依存)・日次の全体の回 | 値の文字列を静的に探す検は無い(写しが振る舞いを変えない形は捕まえない) |
| 枠を取ってから走る(M-entry) | 同じ検の受入の 1 本(埋まった枠の間 pyright を撃たない) | 同上 `test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees` | 同上 | 本物の pyright と展開の子の CPU は測らない(偽の pyright の起動の有無で判じる) |
| 上限の語彙(M-entry) | 検 `test_doeff_hy_check_runs_nothing_when_the_slot_wait_expires`(exit 2・pyright を撃たない) | 同上 | 同上 | 呼び手の読みは agora-controllers の側の検 |
| 入れ子で 2 つ目を取らない(S5) | 検 `test_doeff_hy_check_inside_a_slot_holder_takes_no_second_slot`(K = 1・正本の run の子) | 同上 | 同上 | /proc の無い機体(env の印)の道は検で走らない |
| 正本の無い宿は素通し | 検 `test_a_host_without_the_canon_takes_no_slot_and_says_nothing` | 同上 | 同上 | — |
| env を読むのは M-env だけ | doeff-linter DOEFF004(os.environ・os.getenv の参照を当てる) | `packages/doeff-linter/src/rules/doeff004_no_os_environ.rs`・`pyproject.toml [tool.doeff-linter]` | pre-commit の hook「doeff-linter(Python・基点に無い error だけ止める)」・日次の lint の段 | `Path.home()` が HOME を読む事は当てない |
| module の依存の向き | code-quality の module 契約(static_check → check_slot → env_places だけ) | `.agents/code-quality.d/doeff-hy-check-slot.json` | 出荷前の `code-quality --scope changed`・日次 | path で読む正本(importlib)は依存として数えない |
| 正本の口の食い違いで落ちない(S3) | M-slot の名の確かめ(CANON_NAMES)と TypeError の捕まえ | `check_slot.py` の `take_slot` | 検は無い(実験で確かめる) | 正本の例外の class が class でない形・take の中の別の TypeError も素通しに倒れる |
