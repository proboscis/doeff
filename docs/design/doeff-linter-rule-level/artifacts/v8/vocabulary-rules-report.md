<!-- 写しの出自
元: 議論用の報告 artifact discuss-doeff-linter-vocabulary-rules-2026-09-30.html(sha256 356b0fd95c20dce68241308db5b75a59c965267b37870fad99e065191ed40338・artifact store に在る HTML の本文の md)。
数字の時点: agora-controllers の main 04f259914・2026-09-30。
図: [図 N: ...] の元の mermaid は fig/ に置いた(fig1-mechanism・fig2-clock-conflict・fig3-remaining・fig4-order)。
本文は元の md をそのまま写した。読み替えは無い。
-->

# doeff-linter の語彙の規則(DOEFF150 / 151)— 残り 161 件と、規則の側の欠陥を直すか(議論用の報告)

file 名: discuss-doeff-linter-vocabulary-rules-2026-09-30.html
title タグ: doeff-linter の語彙の規則 — 残り 161 件と規則の側の欠陥(2026-09-30)

---

## 0. これは何の話か

agora-controllers(main 04f259914)の doeff-linter の critical は、あと 161 件です。全部が「使わないと決めた綴り」の規則 DOEFF150 と「使わないと決めた呼び出し」の規則 DOEFF151 の検出です。今夜 operator は「`mail` / `letter` を内部の名でも禁じる決めは正しい」「語彙の規則は critical のまま」と決めました。残る問い c は「規則の側に欠陥があるように見える。直すか、どう直すか」です。この文書は 161 件を 1 件ずつ直し方で分け、doeff-linter の Rust の実装を読んで、欠陥の見立て 4 つを確かめた結果です。4 つのうち 1 つ(欠陥 3)は欠陥ではありませんでした。

### 決めてほしい事(先に結論)

| 欠陥 | 何が起きているか | 選択肢 | 推奨 | 戻せるか |
|---|---|---|---|---|
| 1. 契約で決まった綴りを許す手段が「行の目印」しかない | 契約の欄と値の 7 件が検出される。逆に、目印のある行の内部の名 6 行は隠れる | 案 1 目印を足す / 案 2 linter が契約の file を読み、文字列と wire の型の欄の定義を数えない / 案 3 wire の型に内部の名と wire の名を分けて書く | **案 2**(§4.1) | 戻せる |
| 2. 時計の呼び出しを 2 つの設定が逆に決め、規則ごとに片方しか読まない | `time.time` 6 件 | 案 A 使わない呼び出しの一覧から `time.time` を外し、生の時計は DOEFF106 だけが見る / 案 B DOEFF151 が境目の部品の設定を読む / 案 C 部品が時計の effect を使う | **案 A**(案 C は後でしてもよい・§4.2) | 戻せる |
| 3. (取り下げ)置き換え先を shared/intent から import できない | できる。同じ置き場の `card_marks.hy` が既に import している。本当の違反 2 件 | — | code で直す(§4.3) | — |
| 4. テストの file も対象に入れるか | テストの 79 件(helper 41・変数 27・欄の読み 6・契約の値 5) | 案 1 直す / 案 2 テストを `:except` に / 案 3 テストだけ重大さを下げる | **案 1**(決定 a・b の記録どおり・§4.4) | 戻せる |
| 参考: hint の文 | `直せない既存の当たりは登録簿に載せる` と、廃止した既知の一覧を勧める(決定 A と逆) | 文を直す / そのまま | 文を直す(0.5 時間・§4.5) | 戻せる |
| 参考: `.py` / `.pyi` が対象外 | 型の欄 `mail` 5 つは `.py` に在り、linter は読まない | 欄の改名と同じ変更で対象に足す / 足さない | 足す(§4.5) | 戻せる |
| 参考: 複合語は数えない | `letters`・`mail-row` など約 1,000 か所は 0 件の後も残る | 今は広げない / 広げる | 今は広げず、0 件の後に別に決める(§4.5) | 戻せる |

規則の決めを待つのは **13 件だけ**(欠陥 1 の 7 件・欠陥 2 の 6 件)です。残り 148 件は今の規則のままでも本当の違反なので、規則の議論と並行して直してよい(§6)。

## 1. 決まっている事(operator の言葉のまま)

| 記号 | 原文 | 意味(議論の担当の記録) | 記録 |
|---|---|---|---|
| A | "critical must not be left behind. such 'known' list must not exist to make anything passed" → "ah criticals are rachet okay?" → "not that they must be 0 NOW. we are to make them never increase and start to reduce" | critical を既知の一覧で通さない。今すぐ 0 ではなく、増やさない・減らし始める | agora-redesign #1762・一覧の廃止は #1806 |
| Q2-3(18:2x) | "I agree with the report" | 前の報告の推奨どおり、DOEFF150 から註・docstring・`.md`・契約と wire の欄名・`ACP_CHECKOUT` を外す | #1762・実施は #1794 と #1795 |
| 23:3x の b | "b: before talking about I, we confirm current state and focus on linter correctness and clearing linter issues" | 今は linter の正しさと違反の片づけに集中する | #1877 |
| a(23:5x) | "a: correct." | `mail` / `letter` を内部の名(欄・変数・テストの helper)でも禁じる語彙の決め(メッセージ = Message)は正しい。契約と wire の欄名の許し(Q2-3)は変えない | #1762 の追記・#1876 |
| b(23:5x) | "b: keep critical" | DOEFF150 / 151 は critical のまま。既知の一覧・例外・重大さの引き下げで通さない | 同上 |
| c(23:5x) | "c: idk. i want html report around this matter to discuss. ask opus5.5 subagent to make one" | 規則の側の欠陥を直すかは未決。この文書で議論する | 同上 |

## 2. 161 件の内訳

### 2.1 規則 × 綴り × 置き場

| 規則 | 綴り | 画面の core | 画面のテスト | 模擬環境 | 共有 | 計 |
|---|---|---|---|---|---|---|
| DOEFF150 | `mail` | 50 | 30 | 2 | 0 | 82 |
| DOEFF150 | `letter` | 22 | 49 | 0 | 0 | 71 |
| DOEFF150 | `"agora-kanban-dialogue"` | 0 | 0 | 0 | 2 | 2 |
| DOEFF151 | `time.time` | 0 | 0 | 6 | 0 | 6 |
| 計 | | 72 | 79 | 8 | 2 | 161 |

file ごと: 画面の core 72 = `messaging.hy` 19・`board.hy` 12・`socket_codec.hy` 10・`record.hy` 9・`queue.hy` 6・`slices.hy` 5・`request_history.hy` 4・`views.hy` 4・`react.hy` 3。画面のテスト 79 = `test_glue.hy` 55・`test_server.hy` 15・`test_protocol.hy` 7・`test_slice_cases.hy` 2。模擬環境 8 = `input_marks.hy` 2・`local_screen_socket_client.hy` 6。共有 2 = `shared/intent/kind_tables.hy`。

### 2.2 直し方で分けた種類(1 件ずつ分けた)

| 種類 | 件数 | 実物の行(path:line) | 判定 |
|---|---|---|---|
| 画面の core — 型の欄 `mail` を読む・作る行 | 25 | `board.hy:280` `:messages (dict board.mail)` / `record.hy:330` `:origin (Unreachable :reason page.reason) :mail cache.mail :mail-misses cache.mail-misses` / `views.hy:454` `(!= messaging.mail ws.messages)` | 本当の違反。`.py` の型の欄と同じ変更で直す |
| 画面の core — 変数・引数の名 | 47 | `queue.hy:159` `(var letter (get owed-letters 0))` / `board.hy:393` `(defk chain-of [asks-by-card mail replies key]` / `socket_codec.hy:878` `(<- letter LetterWire (letter-wire row (tuple items)))` | 本当の違反。file の中で閉じる |
| テストの helper の名 | 41 | `test_glue.hy:321` `(defk letter [mid sender to kind at waiting-on in-reply-to state]` / `test_glue.hy:406` `(<- m-a MessageFact (letter "lt-a" A "operator" "question" 5000 None None "inbox"))` | 規則の上は違反(欠陥 4 の問い) |
| テストの変数の名 | 27 | `test_protocol.hy:937` `(setv mail (MessageFact :message-id "lt-big" …))` / `test_server.hy:4035` `(assert (= (get letter "items" 0 "key") "go"))` / `test_glue.hy:3209` `(<- mail RecordBody (body-of-mail message nothing))` | 規則の上は違反(欠陥 4 の問い) |
| テストの欄の読み | 6 | `test_glue.hy:1889` `(assert (= (list board.mail) [ask-id]) …)` / `test_protocol.hy:962` `:origin (Absent) :mail {"lt-far" carried}) NO-EXECUTION))` | core の欄の改名に付いて直る |
| 契約の欄(wire の型の欄の定義) | 2 | `input_marks.hy:38` `(setv #^ (\| str None) mail None))` / `input_marks.hy:58` `(#^ str mail))` | 誤検出(欠陥 1)。ACP の契約の欄 `status.carrier.mail` |
| 契約の値(文字列) | 5 | `test_server.hy:801` `(.append record C2 first-id [{…}] "mail")` / `test_server.hy:5988` `… [#("lt-2" "mail")]) bodies)` / `test_glue.hy:6133` `#((spec-of "c-01M27BBBBBBBBBBBBBBBBBBBBB" "report") "mail")` | 誤検出(欠陥 1)。record-service の stream の種類と screen-socket の `source` の値 |
| 身分の名の直書き | 2 | `kind_tables.hy:87` `:status-writers (FrozenMap {"state" #("agora-messaging" "acp-work" "agora-kanban-dialogue" …` / `kind_tables.hy:154` `:creators #("agora-kanban-dialogue")` | 本当の違反(欠陥 3 は取り下げ) |
| 時計の呼び出し | 6 | `local_screen_socket_client.hy:28` `(int (* 1000 (time.time))))` / `:45` `(val started (time.time))` / `:49` `(while (< (- (time.time) started) seconds)` | 規則どうしの食い違い(欠陥 2) |
| 計 | 161 | | |

### 2.3 依頼の時の見立てと違った点

| 見立て | 実装と実物で確かめた結果 |
|---|---|
| wire の型の名(`LetterWire`・`letter-wire`)が数件〜10 件検出される | **0 件**。規則は大文字小文字を区別し、前後が英字・`_`・`-` の所を語と数えないので、`LetterWire` も `letter-wire` も数えない。`socket_codec.hy:878` の検出は同じ行の変数 `letter` で、直す側。#1881 の「残す: wire の型の名」は対象が無い |
| テストの helper の名が 79 件 | helper は 41 件(`(defk letter …)` 1 + 呼び出し 40)。残りはテストの変数 27・欄の読み 6・契約の値 5 |
| 契約の欄のコピーが 2 件 | 契約の欄 2 + 契約の値(文字列 `"mail"`)5 = 7 件 |
| 内部の名が約 60 件 | 画面の core で 72 件(欄 25・変数と引数 47) |
| 欠陥 3: shared/intent から kanban の語彙を import できない | できる(§4.3)。欠陥ではない |
| `screen-socket.json` に `letter` / `letters` / `mail` の欄が 42 か所 | 引用符つきの綴りで 26 か所(`"letter"` 5・`"letters"` 20・`"mail"` 1)、JSON のキーとしては 22 か所。42 は再現できなかった(説明文の中の語を含めた数かもしれない) |

## 3. 規則の今の仕組みと、欠陥の起きる所

[図 1: fig1-mechanism]

| 手順 | 実装(doeff-linter の `src/project/`) | 欠陥 |
|---|---|---|
| file を選ぶ | `retired.rs` の `selected` — `:files` の glob に当たり、`:except` の glob に当たらない file | 欠陥 4(テストを入れるか) |
| 註・docstring・`.md` を除く | `retired.rs` の `counted_text`(Q2-3 を実施した #1794) | — |
| 行の目印 | `retired.rs` の `line_hits` — 行に `:rule-lines` の文字列が 1 つでも含まれれば、その行の全部の語を数えない | 欠陥 1 |
| 語の一致 | `retired.rs` の `stands_alone` — 前後が英字・`_`・`-` でない所だけ。大文字小文字は区別する。1 行 1 語につき 1 件 | 参考: 複合語(§4.5) |
| 呼び出し(DOEFF151) | `retired.rs` の `hy_calls` — Hy の form の頭が `:calls` の綴り。見るのは `:files` と `:except` だけ | 欠陥 2 |
| 重大さ | `mod.rs` の `retired_drafts` は全部 `Severity::Error`、`rule.rs` の `default_level` で critical | 決定 b で critical のまま |

欠陥 3 は図の中の仕組みの欠陥ではなく、置き換え先を import できるかという code の側の問いでした(§4.3)。

## 4. 欠陥ごとの検討

### 4.1 欠陥 1 — 契約で決まった綴りを許す手段が「行の目印」しかない

**何が起きているか**: file の中で検出を外す手段は、行に目印の文字列が含まれるかの 1 つだけです。「その綴りが契約で決まっているか」は見ていません。

```
// doeff-linter src/project/retired.rs の line_hits(抜粋)
let body = line.trim_end_matches(['\n', '\r']);
if !group.rule_lines.iter().any(|marker| body.contains(marker.as_str())) {
    for (start, end, spelling, detail) in match_group(masked.trim_end_matches(['\n', '\r']), group, patterns) {
        out.push(WordHit { … });
    }
}
```

その結果、狭すぎる所と広すぎる所が同時にあります。

| 行 | 中身 | 今の結果 | なぜ |
|---|---|---|---|
| `input_marks.hy:58` | `(#^ str mail))`(`defwire InputCarrier` の欄) | 検出 | wire の型の欄の定義なのに、行に目印が無い |
| `server_messages.hy:729` | `(#^ LetterFactsWire letter))`(同じ形の欄の定義) | 通る | 型の名 `LetterFactsWire` が目印の一覧に在り、偶然同じ行に在るから |
| `test_server.hy:801` | `(.append record C2 first-id [{…}] "mail")` | 検出 | `"mail"` は record-service の stream の種類。目印は `"kind" "mail"` などの決まった並びだけ |
| `socket_codec.hy:994` | `(val before (dfor letter previous.letters letter.id letter))` | 通る(隠れる) | loop の変数 `letter` は内部の名なのに、目印 `previous.letters` が行ごと許す |

目印のうち `current.letters`・`previous.letters`・`"letters" "upsert"` の 3 個は、`letters` がそもそも語として当たらない(後ろに `s` が続く)ので、契約の綴りを許す役を持っていません。同じ行の内部の名 `letter` を隠すだけです(`socket_codec.hy:994〜997`・`test_server.hy:4032`・`4386` の 6 行)。

**Before(今の書き方)**

```
(retired-words "message-vocabulary" :words ["letter" "mail"]
  :files ["README.md" "docs/contracts/README.md" ".agents/code-quality.json"
          "controllers/**/*.md" "controllers/**/*.hy" "controllers/**/*.json"]
  :except ["controllers/kanban/forbidden-terms.json"]
  :rule-lines ["使わない" "置かない" "FORBIDDEN" "forbidden"
               "carrier.mail" "\"carrier\"" "InputCarrier"
               "\"kind\" \"mail\"" "\"kind\") \"mail\"" "\"streamKind\" \"mail\"" "STREAM-KIND \"mail\""
               "DecisionDocWire" "LetterFactsWire" "letter-facts-wire" "\"letter\" {" "(get (get docs"
               "current.letters" "previous.letters" "\"letters\" \"upsert\""
               "viewIndex.letter" "#(\"letter\" " "(get examples \"letter\")"]
  :instead "mail・letter → Message(契約と wire の欄名は契約の綴りのまま)")
```

**After(案 2 — 契約の file を読む)**

```
(retired-words "message-vocabulary" :words ["letter" "mail"]
  :files [… 今と同じ …]
  :except ["controllers/kanban/forbidden-terms.json"]
  :contract-files ["docs/contracts/screen-socket.json" "docs/contracts/agora-kinds.json"
                   "docs/contracts/record-service.json"]
  :rule-lines ["使わない" "置かない" "FORBIDDEN" "forbidden"
               "carrier.mail" "InputCarrier" …]   ; 欄を読む行の目印だけ残す(約 20 行)
  :instead "mail・letter → Message(契約と wire の欄名は契約の綴りのまま)")
```

案 2 の linter の振る舞い: `:contract-files` の JSON からキーの名と enum の値を集め、その中に在る語について、Hy の文字列で中身がその語ちょうどの物と、`defwire` の中の欄の定義の名だけを数えません。変数・引数・型でない record の欄は今どおり数えます。

| 行 | 今 | 案 2 |
|---|---|---|
| `input_marks.hy:58` `(#^ str mail))` | 検出 | 数えない(契約の欄 `carrier.mail`) |
| `test_server.hy:801` `… "mail")` | 検出 | 数えない(契約の値) |
| `server_messages.hy:729` `(#^ LetterFactsWire letter))` | 目印で通る | 数えない(契約の `DecisionDocWire.letter`) |
| `socket_codec.hy:994` `(dfor letter previous.letters …)` | 目印で隠れる | 検出(無効の目印を消すので) |
| `board.hy:280` `(dict board.mail)` | 検出 | 検出(内部の欄) |

**選択肢**

| 案 | 効く件数 | 実装の場所と規模 | 副作用 | 戻し方 |
|---|---|---|---|---|
| 案 1: 行の目印を足す(今の仕組みの延長) | 7 件消える | agora-controllers の `architecture.hy` の `:rule-lines` に 4〜5 個・10 分 | 目印は行ごと全部の語を許すので、同じ行の内部の名が隠れる(今も 6 行)。目印は code の書き方(改行・並び)に依存し、書き方を変えると黙って効かなくなる | 足した目印を消す |
| 案 2: 契約の file から綴りを読み、文字列と wire の型の欄の定義を数えない(推奨) | 7 件消える。無効の目印 3 個を消すと、隠れていた 6 行が出る(+6) | doeff-linter の `retired.rs`・`architecture.rs`(グループに `:contract-files` を足す・JSON のキーと enum の値を集める・Hy の文字列と `defwire` の欄の位置を除く)。Rust 150〜250 行と失敗ケースのテスト・4〜6 時間 | 契約に在る綴りの文字列は、内部の用途でも数えない(今の検出では 0 件)。欄を読む行(`status.carrier.mail`)は目印のまま残る | グループの `:contract-files` を消す |
| 案 3: wire の型に「内部の名」と「wire の名」を分けて書けるようにする(doeff-hy の `defwire`) | 7 件消え、欄を読む行の目印も要らなくなる | doeff-hy の `defwire` の macro と変換、使う所の改名・1〜2 日 | 実行時の wire の変換を触るので危険が大きい。doeff 側の変更 | revert |

**推奨 = 案 2**。許す根拠が「契約に在るか」になり、Q2-3 で決めた「契約と wire の欄名は契約の綴りのまま」を機械で守れます。行ごとの許しで内部の名が隠れる穴も同時に塞がります。新しい規則ではなく、今の規則に例外の書き方を 1 つ足す形です(#1877 の「新しい linter の規則を足さない」に反しない)。失敗ケースのテストは 3 本: `defwire` の欄は数えない・同じ綴りの変数は数える・無効の目印を消した行の変数を数える。

### 4.2 欠陥 2 — 時計の呼び出しを 2 つの設定が逆に決めている

[図 2: fig2-clock-conflict]

**何が起きているか**: `local_screen_socket_client.hy` について、`architecture.hy` に 2 つの設定があります。

```
;; architecture.hy 181 行(#1797)— 外の世界との境目の部品
:boundary-parts [(boundary-part "controllers.agora_sim.local_screen_socket_client" :touches [network clock thread]
                   :reason "本物の画面の待ち受けへ実 socket で… 客と壁の時計の台 …")]

;; architecture.hy 334 行 — 使わないと決めた呼び出し
:retired-calls [(retired-calls "clock" :calls ["Now" "Elapsed" "EpochMillis" "ReadClock" "time.time"]
                  :files ["controllers/**/*.hy"] :except ["controllers/**/tests/**"]
                  :instead "経過の秒は (GetMonotonic)、壁時計の刻は (GetTime) …")]
```

DOEFF106(生の副作用)は前者を読み、DOEFF151 は後者だけを読みます。

```
// src/project/mod.rs 1716 行(DOEFF106)— 境目の部品の :touches を照らす
.filter(|found| !boundary_allows(raw, module, definitions[found.definition].raw.direct[found.evidence].category))

// src/project/retired.rs の judge_prepared(DOEFF151)— :files と :except だけ。境目の部品は読まない
let call_groups: Vec<&RetiredCalls> = prepared.calls.iter().filter(|g| rel.ends_with(".hy") && selected(rel, &g.files, &g.except)).collect();
```

さらに、生の時計は DOEFF106 の目録(doeff-indexer の `data/raw_side_effects.json` の time)が既に持っています: `time.time`・`time.monotonic`・`time.perf_counter`・`datetime.datetime.now` など。DOEFF106 はこれらを、境目の部品と外の世界に触れてよい handler の一覧の外で critical(強い証拠)として出します。DOEFF151 の `time.time` は、その目録の一部のコピーです。実際に同じ file の `time.perf-counter` 3 か所(103・113・117 行)はどちらの規則でも通り、`time.time` の 6 か所だけが DOEFF151 で出ています。

**Before**: 同じ module に「時計に触ってよい」と「`time.time` を呼ばない」の 2 つ。6 件が出る。境目の部品の外の core で `time.time` を 1 回呼ぶと、DOEFF106 と DOEFF151 が 1 件ずつ出す(同じ事実で critical 2 件)。

**After(案 A)**

```
:retired-calls [(retired-calls "clock" :calls ["Now" "Elapsed" "EpochMillis" "ReadClock"]
                  :files ["controllers/**/*.hy"] :except ["controllers/**/tests/**"]
                  :instead "経過の秒は (GetMonotonic)、壁時計の刻は (GetTime) …")]
```

生の時計に触ってよい所は、DOEFF106 と「外の世界に触れてよい handler の一覧」「境目の部品」の 1 か所で決まります。DOEFF151 は、DOEFF106 では捕まらない退役した effect(`Now` など)だけを見ます。

**選択肢**

| 案 | 効く件数 | 実装の場所と規模 | 副作用 | 戻し方 |
|---|---|---|---|---|
| 案 A: `:retired-calls` の clock から `"time.time"` を外し、生の時計は DOEFF106 だけが見る(推奨) | 6 件消える | agora-controllers の `architecture.hy` の 1 行と、失敗ケースのテスト 1 本(境目の部品の外で `(time.time)` を呼ぶと DOEFF106 が critical を出す)・30 分 | 境目の部品の外の `time.time` は今どおり critical(DOEFF106)。二重に数えていた分は 1 件になる。外の世界に触れてよい handler の module の中の `time.time` も数えなくなる(今は 0 件) | `"time.time"` を戻す |
| 案 B: DOEFF151 が境目の部品の設定を読む | 6 件消える | doeff-linter の `retired.rs`(呼び出しを目録で種類に分け、部品の `:touches` に入れば数えない)。Rust 60〜100 行とテスト・2〜3 時間 | 同じ生の時計を 2 つの規則が数え続ける(部品の外で 1 回呼ぶと critical 2 件)。時計の綴りが目録と `:calls` の 2 か所に残る | revert |
| 案 C: 部品が時計の effect を使う(`interactive-tool-world` の `:clock` = `sync-time-handler` を部品が自分で差し、`(GetMonotonic)` と `(GetTime)` で読む) | 6 件消える(規則は変えない) | `local_screen_socket_client.hy`・1〜2 時間 | 2 つの設定の食い違いは残る(次に時計に触る境目の部品で同じ事が起きる)。同じ file の `time.perf-counter` 3 か所は残る。呼び手が仮想の時計を差すと待ちが終わらないので、部品が本物の時計を自分で差す必要がある(#1880 の担当の指摘) | revert |
| 案 D: `:except` にこの file を足す | 6 件消える | `architecture.hy` の 1 行・5 分 | 同じ事実(この部品は時計に触ってよい)を 2 か所に書く。部品の名を変えた時に片方だけ古くなる | 消す |

**推奨 = 案 A**。食い違いの元は「生の時計に触ってよい所」を 2 つの規則が別々に持っている事で、案 A はその片方を消します(案 B は両方を残したまま照らし合わせるので、二重に数える形が残る)。案 C は、境目の部品の `:touches` から `clock` を外したくなった時の code の改善として後でしてよく、0 件には要りません。

### 4.3 欠陥 3 — 取り下げ(欠陥ではなかった)

見立ては「hint の置き換え先 `controllers/kanban/intent/vocabulary.hy` の PRINCIPAL-OF を、検出の置き場 `controllers/shared/intent/` から import できない」でした。実装と実物で確かめると、import できます。

| 確かめた事 | 結果 |
|---|---|
| service の依存の規則 DOEFF116 | 「shared と foundation は service ではないので見ない」(doeff-linter の仕様 378 行) |
| 層の import の決まり(`controllers/agora_sim/tests/module_tags.hy`) | 別の service の intent は公開の契約として読んでよい。shared の intent から kanban の intent は許される |
| 実例 | 同じ shared/intent の `card_marks.hy:15` が `controllers.kanban.intent.vocabulary` を import 済み。`shared/core/identity_principals.hy:13` は `KANBAN-DIALOGUE-PRINCIPAL` を import 済み |
| 試し(#1880 の担当・作業用の branch) | 87・154 行を `KANBAN-DIALOGUE-PRINCIPAL` に置き換え import を 1 行足した版に、Jev 以外の全規則を当てて違反 0 件 |

結論: 本当の違反 2 件です。code で直します(#1880 の保留を外す)。87 行は ACP の契約 `agora-kinds.json` の intent の書き手の一覧(1253 行)のコピーですが、定数に置き換えても値は同じで、契約との突き合わせのテスト(`controllers/kanban/tests/test_kinds.hy`)はそのまま効きます。

### 4.4 欠陥 4 — テストの file も対象に入れるか

**何が起きているか**: message-vocabulary グループの `:files` は `controllers/**/*.hy`(テストを含む)で、`:except` は `forbidden-terms.json` だけです。ほかのグループ(identity-principal・status-tag-family・retired-operators・呼び出しの clock)は `controllers/**/tests/**` を `:except` に持っています。

ほかのグループがテストを外す理由は、テストが綴りそのものを書いて確かめる必要があるからです(身分の名を契約と突き合わせる・効果を実際に実行する)。メッセージの語彙のテストは、helper の名に `letter` を使う必要がありません。

テストの 79 件 = helper 41(`test_glue.hy` の `(defk letter …)` 1 + 呼び出し 40)・変数 27・欄の読み 6・契約の値 5(欠陥 1 で消える)。

**Before**

```
(defk letter [mid sender to kind at waiting-on in-reply-to state] …)
(<- m-a MessageFact (letter "lt-a" A "operator" "question" 5000 None None "inbox"))
```

**After(案 1)**

```
(defk message-fact [mid sender to kind at waiting-on in-reply-to state] …)
(<- m-a MessageFact (message-fact "lt-a" A "operator" "question" 5000 None None "inbox"))
```

名だけを変えます。wire の形を固定するテストの文字列(`"letter"` のキーなど)は変えません。

**選択肢**

| 案 | 効く件数 | 実装の場所と規模 | 副作用 | 戻し方 |
|---|---|---|---|---|
| 案 1: テストも数えたまま直す(推奨) | 74 件を直す(契約の値 5 件は欠陥 1 で消える) | `test_glue.hy` ほか 4 file の改名・1〜1.5 時間(欄の読み 6 件は core の欄の改名と一緒) | 無し | revert |
| 案 2: テストを `:except` に足す | 79 件がすぐ消える | `architecture.hy` の 1 行・5 分 | 決定 b の記録「例外で通さない」に反する。決定 a の記録は「テストの helper でも禁じる」。テストに `mail` / `letter` が戻っても気づけない | 消す |
| 案 3: テストだけ重大さを下げる | 79 件が critical から外れる | linter はグループごとの重大さを持たないので Rust の変更も要る・2〜3 時間 | 決定 b「keep critical」に反する | revert |

**推奨 = 案 1**。決定 a の記録が「内部の名(欄・変数・テストの helper)でも禁じる」を含み、決定 b の記録が「既知の一覧・例外・重大さの引き下げで通さない」なので、案 2・3 は決まっている事と食い違います。この欠陥 4 は、実質は決定 a・b で答えが出ています。

### 4.5 別に見つけた事(4 つの外)

| 何 | 実装・実物 | 推奨 | 規模 |
|---|---|---|---|
| hint の文が古い | `explain.rs:966〜967`・`rule.rs:870〜871` の hint が `直せない既存の当たりは登録簿に載せる`(既知の一覧に載せる)と `規則そのものを述べる行なら :rule-lines の綴りを含めて書く` を勧める。critical の既知の一覧は廃止済み(決定 A)で、後半は目印を足して規則を避ける書き方を誘う | 文を直す(「`:instead` の語に書き換える。契約の綴りなら契約の file に在るかを確かめる」)。同じ文を持つ規則(DOEFF146・140)も | doeff-linter・0.5 時間 |
| `.py` / `.pyi` が対象外 | グループの `:files` は `.md`・`.hy`・`.json` だけ。型の欄 `mail`(`BoardView`・`MessagingView`・`RequestHistoryView`・`ViewsView`・`RecordCache`)は `.py` に在る。単独の語は画面の `.py` / `.pyi` に 14 行 | 欄の改名(§5 のまとまり A)と同じ変更で、`controllers/**/*.py` と `controllers/**/*.pyi` を `:files` に足す | 設定 1 行 |
| 複合語は数えない | 語の境目の決め(前後が英字・`_`・`-` でない)で、`letters`・`mail-row`・`mails`・`mail-of`・`LettersView`・`mail-misses`・`body-of-mail` などは数えない。概算で code 約 530 か所・テスト約 520 か所(契約の型の名 `LetterWire` や一般の語 dead letter も含む)。DOEFF150 が 0 件になっても、語彙の移行が済んだことにはならない | 今は広げない。0 件の後に「複合語をどこまで改名するか」を別の issue で決める(`architecture.hy` の註も「改名は後のフェーズ」) | — |

## 5. 規則の側を直した後に残る「直す物」の見積り

[図 3: fig3-remaining]

### 5.1 件数の流れ(推奨の案を採った場合)

| 手順 | 件数 | 中身 |
|---|---|---|
| 今 | 161 | |
| 欠陥 1(案 2)で消える | −7 | 契約の欄 2・契約の値 5 |
| 欠陥 2(案 A)で消える | −6 | `time.time` |
| 欠陥 1(案 2)で無効の目印 3 個を消し、隠れていた行が出る | +6 | `socket_codec.hy:994〜997`・`test_server.hy:4032`・`4386` の変数 `letter` |
| 直す物 | 154 | 下の 4 つのまとまり |
| `.py` / `.pyi` を対象に足すと | 約 +14 | まとまり A で一緒に直る |

### 5.2 まとまりごとの直し方

| まとまり | 件数 | 中身 | 直し方 | 時間(目安) | 危険 |
|---|---|---|---|---|---|
| A. 画面の core の欄 `mail` | 31(core 25・テストの欄の読み 6)+ `.py` / `.pyi` 約 14 | `BoardView`・`MessagingView`・`RequestHistoryView`・`ViewsView`・`RecordCache` の欄 `mail` | 型の `.py`・`.pyi` と `.hy` の読み書きを 1 commit で改名(例: `messages`) | 2〜3 時間 | `.py` の dataclass は欄を名でしか受けないので、片側だけ直すと実行時に TypeError。今の linter は `.py` を見ないので漏れに気づけない。wire へ出る欄(`defwire` の欄)と混同しない |
| B. 画面の core の変数・引数 | 51(47 + 隠れていた 4) | `(var letter …)`・`(defk chain-of [… mail …])`・`(<- letter LetterWire …)` | file の中で閉じた改名 | 1.5〜2 時間 | 小さい。同じ file に `message` が既に在る所は名の衝突に注意 |
| C. テストの helper と変数 | 70(41 + 27 + 隠れていた 2) | `test_glue.hy` の `(defk letter …)` と呼び出し 40・変数 | 改名 | 1〜1.5 時間 | 小さい。wire の形を固定するテストの文字列は変えない |
| D. 身分の名の直書き | 2 | `kind_tables.hy:87`・`154` | `KANBAN-DIALOGUE-PRINCIPAL` を import(#1880 の作業用の branch に版がある) | 0.5 時間 | 小さい(値は同じ) |
| 計 | 154 | | | 5〜7 時間 | |

### 5.3 規則の側の作業

| 作業 | 場所 | 時間(目安) |
|---|---|---|
| 欠陥 2 の案 A | agora-controllers の `architecture.hy` 1 行と失敗ケースのテスト | 0.5 時間 |
| hint の文(§4.5) | doeff-linter | 0.5 時間 |
| 欠陥 1 の案 2 | doeff-linter(`retired.rs`・`architecture.rs`)と agora-controllers の設定 | 4〜6 時間 |
| 計 | | 5〜7 時間 |

## 6. 進め方の順

[図 4: fig4-order]

| 順 | 何を | 持ち主 | 時間(目安) | 待つ物 |
|---|---|---|---|---|
| 1 | 規則の側の小さい 2 つ: 欠陥 2 の案 A・hint の文 | agora-controllers・doeff-linter | 1 時間 | この文書の決め |
| 1 と並行 | まとまり B・C・D(本当の違反) | #1881・#1880 | 3〜4 時間 | 無し(D は保留を外すだけ) |
| 2 | 欠陥 1 の案 2 | doeff-linter | 4〜6 時間 | この文書の決め |
| 2 と並行 | まとまり A(`.py` / `.pyi` を対象に足す) | #1881 の続き | 2〜3 時間 | 無し |
| 3 | main で数え直し、DOEFF150 / 151 の critical が 0 件 | — | — | 1・2 |
| 4 | 複合語をどこまで改名するかを決める | operator | — | 3 |

#1881 は今「残す: wire の型の名(`LetterWire`・`letter-wire`)」の指示で範囲を狭めていますが、その行の検出は変数 `letter` なので、`socket_codec.hy:878`・`879` は直す側へ戻してよい。`input_marks.hy:38`・`58` を残す指示は、欠陥 1 の推奨と一致します。

## 7. 付録

### 7.1 用語

| 語 | 意味 |
|---|---|
| doeff-linter | doeff の Hy / Python の code を規則で検査する道具(Rust)。設定は各 repo の `pyproject.toml` と `architecture.hy` |
| DOEFF150 | 使わないと決めた綴りの規則。`architecture.hy` の `:retired-words` のグループごとに、file の行の中の語を数える |
| DOEFF151 | 使わないと決めた呼び出しの規則。`:retired-calls` のグループごとに、Hy の form の頭の綴りを数える |
| DOEFF106 | 許されない所での生の副作用(実 socket・実時計・file など)の規則。生の副作用の目録で種類を決める |
| critical | 規則の重大さの最上位。既知の一覧では下げない(決定 A)。マージ前の検査は main と比べて増えたら止める |
| architecture.hy | agora-controllers の層・service・語彙の設定を書いた file。doeff-linter が実行せずに読む |
| 行の目印 | `:rule-lines`。行にその文字列が 1 つでも含まれれば、その行はグループの語を全部数えない |
| 契約 | `docs/contracts/*.json`。ACP・画面の socket・記録の service との間でやり取りする形の定義。欄の名と値は相手と共有しているので、こちらだけでは改名できない |
| wire | 外とやり取りする形(JSON)。`defwire` は、その形を Hy の型として書く doeff-hy の macro |
| 模擬環境 | `controllers/agora_sim`。本番の組み立てのまま handler を差し替えて業務を確かめる手元の環境 |
| 境目の部品 | `:boundary-parts`(#1797)。模擬環境から本物の外の世界へ届く部品で、指定した種類の生の副作用だけを DOEFF106 で数えない |
| ratchet | 「増えない・減るだけ」を機械で守る検査(決定 A の実装の形) |
| 失敗ケースのテスト | 規則が鳴るべき例で本当に鳴ることを確かめるテスト(規則 1 本ごとに持つ決め) |
| Jev | 自然言語の判断を確率つきで返す小さな LLM の判定器。doeff-linter は DOEFF201〜205 で使う |

### 7.2 関連する issue(agora-redesign)

| issue | 中身 | 状態 |
|---|---|---|
| #1762 | 決定の親(A・B・C・Q2-3・今夜の a・b・c の追記) | closed |
| #1877 | linter の正しさと片づけの親(今の状態の表・子と順) | open |
| #1876 | 綴りの規則の 161 件を減らす(親) | open |
| #1880 | 綴りの残り(小): `time.time` 6 件・身分の名 2 件。欠陥 2・3 の議論待ちで保留 | open(保留) |
| #1881 | 綴りの残り(大): 画面の `mail` / `letter` 153 件。範囲を狭めて作業中 | open |
| #1797 | DOEFF106 に境目の部品の設定を足す | closed |
| #1794 | DOEFF150 が註・docstring・`.md` を数えない | closed |
| #1795 | 契約と wire の欄名を行の目印で許し、`ACP_CHECKOUT` を外す | closed |
| #1806 | critical の既知の一覧の廃止 | closed |

### 7.3 数字の元と測り方

| 数字 | 元 | 測り方 |
|---|---|---|
| 161 件と 1 件ずつの行 | 依頼者の `hits-161.json`(main 04f259914)。#1877 の今の状態の表(同じ main・161 件・同じ内訳)と一致 | 各行について、`retired.rs` の語の境目の決め(前後が英字・`_`・`-` でない最初の位置)を Python で再現し、161 件全部で検出の位置を確かめた |
| 種類分け(§2.2) | 同じ 161 件 | 語の直前の文字で分けた: `.` か `:` なら欄、文字列の中なら契約の値、`(defk letter` とその呼び出しなら helper、それ以外は変数・引数。`input_marks.hy` の 2 件は `defwire` の欄として手で確かめた |
| 隠れている 6 行 | `controllers/**/*.hy` 全部 | 目印を含み、註を除いた code に単独の `mail` / `letter` を持つ行 26 行を読み、内部の名だけの 6 行を数えた |
| 契約の綴りの数 | `docs/contracts/screen-socket.json` ほか | JSON を読んでキーと値を数え、引用符つきの綴りは文字列の検索で数えた |
| 複合語・`.py` の数 | `controllers/` の `.hy`・`.py`・`.pyi` | 文字列と註を粗く除いた概算(規則の実装そのものではない) |
| 時間 | 見積り | 改名の件数と file の数からの目安で、測った値ではない |

### 7.4 実装の該当箇所(doeff の main)

| 箇所 | 中身 |
|---|---|
| `packages/doeff-linter/src/project/retired.rs` | DOEFF150 / 151 の判定。`selected`(file の選び)・`stands_alone`(語の境目)・`counted_text`(註と docstring を除く)・`line_hits`(行の目印)・`hy_calls`(呼び出し)・`judge_prepared` |
| `packages/doeff-linter/src/project/mod.rs` 1716 行・3444 行 | DOEFF106 が `boundary_allows` で境目の部品の `:touches` を照らす所(層の置き場の中と外の 2 か所) |
| `packages/doeff-linter/src/project/mod.rs` の `retired_drafts` | DOEFF150 / 151 の検出を違反にする所(重さは `Severity::Error`) |
| `packages/doeff-linter/src/project/rule.rs` の `default_level` | DOEFF106・150・151 の既定の重大さ critical |
| `packages/doeff-linter/src/project/explain.rs` 966〜967 行 | DOEFF150 / 151 の hint の文 |
| `packages/doeff-indexer/data/raw_side_effects.json` | 生の副作用の目録(time の種類に `time.time`・`time.perf_counter` など) |
| agora-controllers の `architecture.hy` 181 行・226〜340 行 | 境目の部品・使わない綴りのグループ・使わない呼び出しのグループ |

### 7.5 確かめられなかった事

| 事 | 理由 |
|---|---|
| linter を自分で実行して 161 件を数え直す | 手元の doeff-linter の実行 file は #1794・#1797 より前に組まれた物で、repo の中で実行すると log の file も書く。依頼者の結果と #1877 の数(2 つとも 161 件・同じ内訳)を使い、各行の位置を実装の決めで再現した |
| 欠陥 1 の案 2 の規模 | 試作していない。Rust 150〜250 行・4〜6 時間は読んだ実装からの目安 |
| #1880 の作業用の branch の「違反 0 件」 | 担当の報告(#1880 の comment)を引いた。自分では実行していない |
| 複合語と `.py` の数 | 簡単な script での概算 |
| `screen-socket.json` の「42 か所」の数え方 | 元の数え方が分からず、再現できなかった |
