# 盲検 A の返答(未加工)

起動: Agent tool・subagent_type=general-purpose・model=opus(要求 claude-opus-5-5・effort xhigh は起動口に effort の欄が無く指定できず — 観測できたモデルは未確認)
agentId: a8e7acbf56fa679eb・起動 2026-09-25T23:0x Z 頃(JST 09-26 08:0x)・所要 748243 ms・tool_uses 42・機体 proboscis-mbp(personal)
fallback の理由: 第 1 候補 gpt-6-astra(effort low)を `CODEX_HOME=~/.codex/profiles/personal codex exec -m gpt-6-astra -c model_reasoning_effort=low -s read-only`
で起動した(session id 01a0dac0-33c4-7221-8c22-d932cef4eb45・2026-09-25T22:47:04Z)が、`401 Unauthorized: Incorrect API key provided` で実行に失敗した(log = evidence/blind-codex-run-a.log)。
入力: /tmp/blind-herdr-argv0/prompt-a.md(sha256 5e5b813aa2e6eb0b5ae0725c12e31612799529f30771b3ad49f1bbc1d1fac7e8 = 手順書の A の依頼文 + blind-input.md の全文)。読み取り対象 = /tmp/blind-herdr-argv0/src(doeff 72da0ff1 の写し)と /tmp/blind-herdr-argv0/herdr-0.9.1。
文脈: 親の会話を fork・resume せず新しい文脈で起動。B の返答は見せていない(A・B は同じ 1 つの返事の中で同時に起動)。

---

**結論**: 反例が 1 つ見つかりました。主張 S2(a)「herdr の新版が保証欄を足しても M2 だけで吸収できる」は、稼働している herdr の版が 1 つでない時に崩れます。答えの dict には「どの版の契約で読むべきか」が載っていないので、M2 は同じ入力から 2 通りの判定を出せません。M2 に加えて M3(と、場合によって M1)の変更が必要になります。

## 1. 変更要求と、それが必要になる理由

**変更要求**: herdr の新版(仮に protocol 23)で `PaneProcessInfoProcess` の必須欄に `argv0` が入る場合を考えます。S1 の「argv0 を埋め始める Linux 版」と S2(a) の「保証欄を足す」を合わせた形です。doeff は要件 1・2 に沿って次の 2 つを同時に満たしたい。
- protocol 23 の答えは保証欄 `argv0` から読み、`argv0` が無い答えは `HerdrContractError` で名指す。
- まだ 0.9.1 / protocol 22 の herdr に対しては、今までどおり argv → name の順で読む。

**必要になる理由**:
- 要件 1 は「保証された欄から読む」ことを求めています。今の読み順 argv0 → argv の先頭 → name は、name が process title で信用できないための推測の読み順です。保証欄が来ればこれが要らなくなります。
- herdr は doeff と独立に上がります。
  - 材料の記録では protocol が 14 → 17 → 20 → 22 と約 2 か月半で 3 回動きました。doeff の実装が 1 か月止まっている間に p17 → p20 へ上がった記録もあります(`conformance/herdr-physics.md` 560–605 行)。
  - herdr は Mac と Linux の 2 台で別々に入っています。
- herdr の公開 API には `server.live_handoff {import_exe, expected_version, expected_protocol}` があります(`herdr-api.schema.json`)。pane を残したまま、別の実行ファイル(別の版)へ server を引き継げます。つまり、同じ socket path・同じ doeff host プロセスのままでも、答える herdr の版が途中で変わりえます。

## 2. 関係する主張・前提と、予想に反して変わるもの

**関係する元の主張と公開の形**
- S2(a): 「(a) は M2 だけ。M1・M5 は変わらない」。
  - 明示された前提「封筒の形(result / error)は変わらない」は、本例でも守られています(`{"id","result"}` / `{"id","error"}` のまま)。
- M2 の公開の形: `(herdr-foreground-command result)` → `str | None`。契約の外は `HerdrContractError`。純関数で状態を持たない。
- M1 の公開の形: `(herdr-call socket-path method params)` → result の dict。call ごとに接続を張って閉じる。
- S4 は、この「M1 は状態を持たない」ことを根拠にしています。

**予想に反して変わるもの**

| 対象 | 変わる内容 |
|---|---|
| M2 | 入力に「答えた server の protocol」が要る。引数が増えるので公開の形の拡張になる。 |
| M3 または M1 | protocol を得る経路が今は無い。どちらかを変える必要がある(下記)。 |
| M4 / `host.hy` | handler の設置時に 1 回だけ版を読む案は採れない。 |
| M1 に版を cache する案 | M1 の寿命の約束と S4 の前提を壊す。 |
| 変わらないもの | M5・M6 の code と、effect `TmuxPaneCurrentCommand` の答えの型。 |

**protocol を得る経路が無い根拠**
- M1 は `(get response "result")` だけを返し、残りを捨てています(`substrate_herdr.hy` 192 行)。
- `substrate_herdr.hy` の中に `ping` の呼び出しは 1 つもありません。
- 答えの側にも版は載りません。`pane_process_info` の result は schema 上 `type` と `process_info` だけで、成功の封筒も `{id, result}` だけです。
- 版を運ぶのは `ping` の答え `pong {version, protocol}` と `session.snapshot` だけです。

**取りうる経路**
- M3 が観測のたびに `ping` を追加で実行する。1 回の観測が 2 回の RPC になり、M3 の RPC の並びが変わる。
- M1 が result と一緒に接続先の protocol を返す。M1 の公開の形の拡張になる。

**採れない経路**
- handler 設置時(`host.hy` 622–623 行)に 1 回だけ版を読む案: 上記の live handoff で版が途中で変わるので正しくない。運用設定で版を宣言する案も同じ問題を持ち、さらにマシンごとの設定が必要になる。
- M1 に版を cache する案: 「call ごとに接続を張って閉じる」という M1 の寿命を破り、S4 の前提も壊す。

## 3. どの知識・判断がどこへ漏れるか(区別)

**区別**
- **意図した契約の拡張(S2(a) の想定内)**: 新しい保証欄を読む表を M2 の中に足すこと。
- **想定外の公開契約の拡張**: M2 の入力に protocol を足すこと。M1 が接続先の版を返すようにする場合は、M1 の公開の形も広がります。
- **配線の変更**: M3 が `ping` を足して、得た protocol を M2 へ渡すこと。
- **共同の不変条件**: 「M2 の保証欄の表の版 = 答えた herdr の版」。code の中にこれを持つ場所がありません。今は次のものだけで保たれています。
  - M2 の docstring に書かれた定数「0.9.1 / protocol 22」(443 行)
  - テストの fixture に付いた注記
  - `herdr-physics.md` への人手の再測定と、ADR-004 R12 の「protocol 追随」の手順
- **漏洩かどうか**: M2 が隠すはずの platform 差は漏れていません。問題は逆向きです。M2 の「答え → 判定」という純関数の契約が、答えに含まれない情報(接続先の版)に暗黙に依存しています。「契約の外か」の判断に必要な知識の持ち主が決まっておらず、M2 が定数として抱えています。M1 が隠している「socket の扱い」の中の「接続先 server の版」を外へ出さない限り、M2 は正しく判定できません。

**解釈の分岐**: 要件 2 の「契約」を「対応する全版の和集合」と読めば、M2 だけで済みます(argv0 があれば読み、無ければ従来どおり)。ただしその場合、protocol 23 の server が argv0 を落とした答えは argv で黙って読まれ、名指されません。要件 2 のきっかけになった事故(zeus の `KeyError: 'argv0'`)と同じ種類の「想定した契約と実物のずれ」が、検出できなくなります。

## 4. 最小の例・再現手順・観測すべき結果

**入力 X**: zeus の実物です(テストの `ZEUS-PROCESS-INFO-ANSWER` の result、214 行)。
```json
{"type":"pane_process_info","process_info":{"pane_id":"w7:p1","shell_pid":1643746,"foreground_process_group_id":1643746,"foreground_processes":[{"pid":1643746,"name":"zsh","argv":["/usr/bin/zsh"],"cmdline":"/usr/bin/zsh","cwd":"/home/kento"}]}}
```

**仮の protocol 23 の差分**: `PaneProcessInfoProcess.required`: `["pid","name"]` → `["pid","name","argv0"]`

**期待される判定**
- protocol 22 の server が X を返した場合 → `"zsh"`(既存のテスト行。要件 3 のテストそのもの)
- protocol 23 の server が X を返した場合 → `HerdrContractError`(要件 2)
- X は同じ dict なので、`(herdr-foreground-command X)` が両方を満たすことはできません。

**再現手順**(私は実行していません。共有ソースを変えない制約があり、pytest は cache を作るためです。作業用の複製で行う想定です)
1. `test-herdr-foreground-command-follows-the-process-info-contract`(275 行)に「protocol 23 の答えとしての X → `HerdrContractError`」の行を足します。既存の「X → `"zsh"`」の行は残します。
2. M2 だけを変える実装を何通り書いても、`uv run pytest packages/doeff-agents/tests/test_sessionhost_substrate_herdr.py -k follows_the_process_info_contract` はどちらかの行で赤になるはずです。解く手は M2 の引数に protocol を足すことしかありません。
3. protocol を渡すために M3 へ `ping` を足すと、`test-herdr-pane-current-command-reads-answers-without-argv0` が赤になるはずです。
   - このテストの偽 herdr は、1 接続に 1 通の答えを順番に返します(`answer-herdr-requests`)。
   - テストは要求の並びを `pane.process_info` × 4 に固定しています(271 行)。
   - つまり M3 の RPC の並びが変わったことが、既存のテストで観測できます。

**運用で観測されるはずの結果**(code を読んだうえでの推測)
- M2 を厳しくするだけの場合: protocol 22 のマシンでは、刈り取り免除でない running の行ごとに、`monitor-cycle` の結果が毎周回 `error:HerdrContractError` になります(`policy.hy` 1954–1956 行の session ごとの隔離)。zombie より後の処理(画面の取得・turn-end・結果の先取り・督促・stall watchdog)が毎回飛ばされます(1313 行の分岐順)。2026-09-26 の zeus の KeyError と同じ形で、例外に型が付くだけです。
- M2 を緩くするだけの場合: 観測できるものが何もありません。それ自体が欠陥です。

## 5. 未確認の前提・不足する情報(実測と推測の区別)

**読んで確かめたこと(実測)**
- M1 は result だけを返し、`ping` を呼んでいない。
- herdr の schema: `pane_process_info` と成功の封筒に版の欄が無い。`pong` は `protocol` を持つ。`server.live_handoff` は `import_exe` と `expected_protocol` を受ける。
- M2 の docstring に「0.9.1 / protocol 22」という前提が書かれている。
- テストは要求の並びを固定している。
- `monitor-cycle` は例外を session ごとに捕まえる。

**推測・未確認・不足**
- herdr が保証欄を実際に足すかは、S2(a) の仮定そのものです。
- Mac 側の herdr の版は材料にありません(zeus = 0.9.1 / protocol 22 はテストの注記だけ)。2 台が同時に違う版で動くことがあるかは未確認です。
- `live_handoff` が実際に版の更新に使われているかは不明です。材料で確かめられるのは API があることだけです。
- 1 つの接続で `ping` と `pane.process_info` を続けて送れるかは不明です。herdr 側の接続処理は抜粋にありません。
- `pong.protocol` が答えた server の schema の protocol と一致する、というのは欄名からの推測です。
- 要件 2 の「契約」を版ごとと読むか和集合と読むかは、材料で決まっていません。この反例が成り立つかは、この解釈に依存します。
- テストは実行していません。

**補足(この反例とは別の読み取り。複数モジュールにまたがる反例にはならないもの)**
- **`foreground_processes` の並び順が platform で違う**:
  - Linux は pid の昇順に並べ替えます(`linux.rs` 479 行)。macOS は `proc_listpids` の順のままです(`macos.rs` 421–450 行)。schema は順序を約束していません。
  - M2 は「先頭」を読むので、前面の job に複数の process がある時、Linux と macOS で違う process を選びえます。S1 の前提「platform 差は欄の有無と値の形だけ」は、今の 2 platform ですでに満たされていません。
  - ただし直しは M2 の中で済みます(例: pid が `foreground_process_group_id` と一致する要素を選ぶ)。
  - agent の子 process が同じ process group に入るか、macOS の kernel が返す並び順は材料に無く、未確認です。
- **M2 の「契約の外」の検査は、読む欄だけが対象**です。必須欄 `pid` と `pane_id` の欠落や、2 番目以降の要素の型は検査しておらず、テスト表にもありません。今の実装は、上の解釈の分岐でいう「和集合として読む」側に近いです。
- **他の主張の探索結果**:
  - S1 の Windows: pwsh・cmd を扱うには M5 の語彙の追加が要ります。これは値の形ではなく値の範囲の差なので、S1 の適用範囲外です。bash.exe やバックスラッシュ区切りの path だけなら、M2 の中で吸収できます。
  - S2(b): M3 だけで済みます。M3 が見ている error code は `pane_not_found` の 1 語だけです。
  - S3: 記録した dict を M2 に通す試験用の handler を組めば、本体を変えずに済みます。
  - S4: host は周回を thread で動かし、M1 は状態を持たないので、読んだ範囲では成り立ちます。
  - いずれも「安全」とまでは結論していません。
