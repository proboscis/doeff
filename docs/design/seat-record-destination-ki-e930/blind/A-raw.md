## 結論

設計が自分で挙げた変更シナリオ S2(記録サービスの所在が変わる)の範囲内に、反例が 1 つあります。前提 P1〜P4 はどれも崩していません。この反例は予定の検査 K1〜K5 を全部通り、固定要件 Q1 と S2 の主張の両方を破ります。直そうとすると、「変わらない」とされた `spawn-policy` か、表に載っていない起動経路の判断(`next-arm-for-job`)のどちらかを変えることになります。

## 1. 変更要求とその理由

変更要求は「記録サービスの宛先を移す」です。会社 Mac の `acp-single-mac.toml` の `[agentd-join.record].url` は今 `http://agora-record.taildd050.ts.net:8874` です。これを別の host や port に替え、宣言を描き直して agentd を再起動します。

- 宣言 file の注記は、宣言を変えた時の戻し方を「描き直し・適用(agentd を 1 回 restart)」と書いています。
- 同じ file の単一 Mac の注記には、ACP head を `127.0.0.1` に移す手順(`--set acp_url=...`)がすでにあります。所在の移設は現実にありうる運用です。

## 2. 関係する主張・前提と、予想に反して変わるもの

**破れる主張**

- S2「触るのは各機体の `[record].url` 1 行だけ。agentd と席が同時に新しい値を使う」。
- Q1「agentd が起こすすべての手番の process の env に、その agentd 自身が追記に使う宛先と byte 同一の値が在る」。
- C1 の適用範囲は「`incarnation-charter-of` が組むすべて」です。ところが実コードでは、温かい session への送り(send arm)も process を起こします。
  - `policy.hy:313-330` は、席の process を起こす経路を 4 つと明記しています。4 つ目は `headless.continue-headless-process` です。
  - claude の headless は手番の終わりで必ず降ります。そのため 2 手番目以降は、ふだんこの 4 つ目の経路を通ります。

**実コードの経路**

1. 起動時の `session_env` は、host の sqlite の行に `launch_overlay` として保存されます(`launch.hy:1094`、`headless.hy:319-321`)。
   - 保存から外されるのは `TURN-AUTH-ENV-KEYS` = `{CLAUDE_CODE_OAUTH_TOKEN}` だけです(`policy.hy:576-588`)。
   - したがって `RECORD_SERVICE_URL` は行に残ります。
2. send arm の process の env は `launch-spawn-env(identity, overlay.session_env | turn-env)` です(`headless.hy:465`)。
3. agentd が送る `turn-env` は `turn-session-env-of lease` = 札だけです(`judgment.hy:3677`、`agentd.hy:2306` / `2345`)。
4. どの経路を使うかの判断は `next-arm-for-job` です(`judgment.hy:1613`)。idle で「同じ家」で effort も同じなら send を選びます。
   - 「家」の鍵 `session-affinity-key-of` は account・binding・model だけで(`judgment.hy:1177-1194`)、記録の宛先を含みません。
5. Mac では host(`com.masui.acp-sessionhost`)と agentd が別の launchd unit です。
   - 宣言 file の 375-378 行が「agentd の unit を落としても走っている手番は切れない」と書いています。
   - `host.hy:1930` は「daemon 死 ≠ session 死」と書いています。
6. codex の headless は、生きた温かい process がそのまま次の手番を受けます(`headless.hy:517`)。起動時の env は process が生きている限り変わりません。

**予想に反して変わる責務・モジュール・契約**

- **send arm の env の組み立て**:`judgment.turn-session-env-of`(契約は「手番ごとの資格の env」)と、その呼び手の `agentd.after-start`。
- **host の保存方針**(`spawn-policy`、設計では「変わらない」):`policy.TURN-AUTH-ENV-KEYS` / `overlay-without-turn-auth`、またはそれに並ぶ新しい集合。
- **起動経路の判断**(表に無い):`next-arm-for-job` / `session-attribution-of`。あるいは `session-affinity-key-of` と、それに連動する `AGENT_SETTINGS_RESTART_ON`(node が公開する restartOn)。

## 3. どの知識がどこへ漏れるか(契約拡張との区別)

**漏れる知識は「値の寿命の区分」です。** C2 が隠すのは名前の集合だけです。ところが設計は `RECORD_SERVICE_URL` を「会話の身元と同じ走行者が持つ名」として扱っています。

- `AGORA_CONVERSATION_ID` / `AGORA_SEAT_OPENER` は会話ごとに一定です。行に保存されたまま再生されても古くなりません。
- 記録の宛先は機体の設定なので、session の途中で変わりえます。
- 行に保存するか、手番ごとに言い直すか、変わったら process を起こし直すか。この区分を決めて持つのは host 側と起動経路の判断です。
  - 保存するかどうかは `policy.hy` の `TURN-*` の集合が決めています。
  - 起こし直すかどうかは restartOn・affinity の鍵・effort の比較が決めています。

直し方ごとに、漏れ方は次のとおりです。

- **claude(手番ごとに言い直す直し方)**
  - `turn-env` に走行者の名を足すのは配線の変更です。
  - ただしそれだけでは行に古い写しが残ります。`turn-env` を伴わない送り(操作者による救援など)は、その古い写しで起きます。
  - コード自身の規則(`policy.hy:320-324`「行へ写して解かない — 行の写しが古い拍で手番の値と割れる」)に従うなら、host の `policy.hy` がこの名前を知る必要があります。
  - acp と host の両側が読む語彙の置き場は `policy.hy` と決まっています(`policy.hy:389` 付近)。そのため C2「`effects` の 1 か所」と K5(`effects.py` だけを除外する semgrep)がそのままでは成り立ちません。
- **codex(生きた process)**
  - 手番ごとに言い直す手段はありません。値が変わったら process を起こし直す判断が必要です。
  - effort と同じ形なら attribution に値を記録して比較します。affinity の鍵に入れるなら、home の digest・`conversation.status.home`・restartOn の公開値まで波及します。
- **運用で吸収する直し方**
  - 「宛先を変えたら host の blue/green 入れ替えもする」(draining から resume arm に移り、新しい charter が組まれる)。
  - この場合は知識が運用手順へ移り、S2 の「1 行だけ」が成り立ちません。

**区別**

- 契約拡張ではありません。S2 は設計が自ら範囲内と宣言したシナリオで、Q1 そのものが破れています。
- `after-start` の引数を増やす部分は配線の変更です。
- 「すべての process の env = 今の agentd の値」は複数モジュールにまたがる共同の不変条件です。設計はこれを `charter-assembly` だけに割り当てています。
- 名の寿命の区分は、隠すはずの知識の漏洩です。
- なお、宣言された `seat_env`(`ACP_BASE` 等)にも今日すでに同じ古びがあります。この設計が新しく作った根ではありませんが、S2 の主張はこの点に触れていません。

## 4. 最小の入力・再現手順・観測すべき結果

**実測(基準 commit 4da6ca4a と同一の main checkout を `python -B` で読み込み、既存 test の偽の器 `ContinueWorld` を再利用。書き込みなし)**

1. `overlay-without-turn-auth({AGORA_CONVERSATION_ID, RECORD_SERVICE_URL, CLAUDE_CODE_OAUTH_TOKEN})` の結果、行に残る名は `['AGORA_CONVERSATION_ID', 'RECORD_SERVICE_URL']` でした。
2. `turn-session-env-of(None)` の結果は `{}` でした。
3. 行の `launch_overlay.session_env = {RECORD_SERVICE_URL: "http://old-record:8874"}` を入力にしました。`turn-env = {CLAUDE_CODE_OAUTH_TOKEN: "tok-new"}`(設計どおりの agentd が send arm で送る形)で `headless-send-program` を実行した結果です。
   - 起動した process の env:`RECORD_SERVICE_URL = http://old-record:8874`、札は `tok-new`。
   - 行の overlay は古い値のまま変わりませんでした。

**検査に落とす形(案。書いていません)**

- K1 の continue ケースは今、行を同じ settings から作っています。そのため必ず通ります。
- 「settings A(U1)で launch → 行を保存 → settings B(U2)の agentd が send arm を撃つ → 起動 env に U2 が在る」とすれば、設計どおりの実装では U1 が出て落ちるはずです。
- codex は「生きた process のまま U1 で次の手番を受ける」ことを検査します。

**実機での手順(推測・未実行)**

1. Mac で claude の会話 C の 1 手番目を終えます。
2. `[record].url` を U1 から U2 に替え、agentd の unit だけを再起動します。
3. 同じ profile・model・effort で、compactAt 未満のまま C に郵便を送ります。
4. 手番の中で `printenv RECORD_SERVICE_URL` を実行すると U1 が出るはずです(期待値は U2)。
5. 32,768 byte を超える `ai tell` を実行した時の結果は次のどちらかです。
   - U1 が止まっていれば、送り手に NotPosted(stage record)が出ます。
   - U1 が生きていれば、見出しは受理され本文は U1 に置かれます。受け手の agentd は U2 から読むので `body of message … could not be read` が log に出て、条件 `InputUnavailable` が付きます(`agentd.hy:2069-2110`)。
6. 観測する場所は host の sqlite の `agent_sessions.launch_overlay_json` です。

## 5. 未確認の前提と不足する情報

**実測したもの**
- 上の 3 点だけです。

**コードを読んで確認したが実行していないもの**
- send が選ばれる条件、「家」の鍵の中身、codex の生きた process、Mac で unit が分かれていること、受け手の agentd が本文を自分の宛先から読むこと。

**推測・未確認**
- pool pod の置き場の寿命。emptyDir なら、pod を再起動した時点で行が消え、pool では S2 が成り立つかもしれません。
- 移設の間、旧サービスを生かしておくか。これで、止まって気づく壊れ方か、黙って本文が消える壊れ方かが分かれます。
- 実際の運用で、宛先の変更に host の入れ替えを伴わせるか。
- codex の席と TUI backend が今どれだけ使われているか。
- warm session が resume や rehydrate を経ずに続く期間の実測。
- 実装者が表の外で `turn-session-env-of` まで直すかどうか。

**読んでいない範囲**
- ADR R30 / R51 の本文と `test_sessionhost_headless.py` は精読していません。
