# 盲検の前に固定した主張(変更シナリオと予想した波及範囲)

- 保存: 2026-09-24T15:5xZ(盲検 A・B の起動より前)
- 基準 commit: doeff `4da6ca4a3dd83bdf8bc57e5ecf799365721167be`
- 対象の設計: `design.md`(同じ dir)

この file は盲検の返答を受けても書き換えない。反例を受けた結論は `counterexamples.md` に別に書く。

## 前提(すべての主張が依る)

- P1. 席の process は agentd と同じネットワークの見え方を持つ(同じ pod・同じ Mac)。agentd が追記に使う URL は席からも届く
  (pool は 2026-09-24 に実測・Mac は 2026-09-21 の card 本文の実測)。
- P2. 記録サービスは席の札で追記を受ける(同上の実測)。認証は doeff の外。
- P3. 席の env は `charter.session_env` だけが運び、継承の名簿は開けない(R30 (4))。
- P4. 宛先は agentd の process の起動時に読む不変の値で、agentd の中に並行の書き手は居ない。

## module id

`host-declaration` / `join-gate` / `runner-env-names` / `agentd-settings` / `charter-assembly` /
`spawn-policy` / `seat-tool` / `record-service`

## 変更シナリオ(6 軸)

### S1 `hardware` — 機体を 1 台足す(3 台目の Mac・別の k8s pool)(applicable)

- change: 新しい機体で agentd を参加させる。
- claim: 席に宛先を届けるために触るのは**その機体の宣言 file の `[record].url` 1 行だけ**(参加に必須なので、
  書き忘れは参加の門が断る)。席向けの宣言(`seat_env`)に記録の宛先を書く必要は無く、書けば門が断る。
- expected_scope: `host-declaration`(新しい宣言 file)。
- unchanged: `join-gate` / `runner-env-names` / `agentd-settings` / `charter-assembly` / `spawn-policy` / `seat-tool`。

### S2 `storage` — 記録サービスの所在が変わる(Service 名・tailnet の名・port の変更)(applicable)

- change: 記録サービスを別の名で公開し直す。
- claim: 各機体の `[record].url` を 1 行ずつ変えて agentd を起こし直せば、agentd 自身の追記と席の `ai tell` が**同時に**新しい宛先を使う。
  片方だけが古い値を持つ状態は作れない(値の置き場が 1 つ)。
- expected_scope: `host-declaration`(各機体 1 行)。
- unchanged: 他のすべての module。

### S3 `effects` — 走行者が席へ運ぶ宛先が 1 つ増える(例: 預かり所の宛先 `AGORA_CUSTODY_URL` を同じ形へ移す)(applicable)

- change: agentd が自分の宣言から知っている別の宛先を、同じ形で席へ届ける。
- claim: 変わるのは `runner-env-names`(集合に 1 行)・`agentd-settings`(値を判断の層へ運ぶ欄)・`charter-assembly`(書く 1 行)。
  門 (c) は同じ集合を読むので `join-gate` のコードは変わらない。`spawn-policy` と `seat-tool` は変わらない。
  ⚠ 移行の時だけ、既にその名を `seat_env` に写している宣言(pool の `AGORA_CUSTODY_URL`)が門で断られるので、
  宣言側(`host-declaration`)を同じ便で直す — これは意図した共同の不変条件(値の置き場は 1 つ)の帰結で、知識の漏洩ではない。
- expected_scope: `runner-env-names` / `agentd-settings` / `charter-assembly`(+ 移行時の `host-declaration`)。
- unchanged: `join-gate` のコード / `spawn-policy` / `seat-tool` / `record-service`。

### S4 `distribution` — 席を agentd と別のネットワークの見え方で走らせる(別 pod の sidecar・遠隔の器)(applicable・前提 P1 の外)

- change: 席の process から見た記録サービスの名が agentd から見た名と違う形の配置を足す。
- claim: P1 が崩れるので C1(byte 同一)の契約の**適用範囲外**。その時は宣言に「席向けの宛先」を足す契約の拡張になり、
  変わるのは `host-declaration`(鍵 1 つ)・`join-gate`(鍵の読み)・`agentd-settings`(運ぶ値)。
  `charter-assembly` は「settings の席向けの値を書く」だけで、宛先の由来を知らないので変わらない。`seat-tool` は変わらない。
- expected_scope: `host-declaration` / `join-gate` / `agentd-settings`。
- unchanged: `charter-assembly` / `spawn-policy` / `seat-tool`。

### S5 `concurrency` — (excluded)

- reason: 宛先は agentd の process の起動時に 1 度読む不変の値で、手番ごとに純粋な関数が charter へ写すだけ。
  並行の書き手も共有の可変状態も無い。走っている席の env が差し替わらないのは R51 (5) の既存の性質で、この変更で変わらない。

### S6 `simulation` — 決定的な試験で席の env を検める(applicable)

- change: 記録の宛先の有無・値を変えた settings で、launch / resume / continue の経路を fake の器で回して席の env を検める。
- claim: `charter-assembly` は純粋な関数で、settings(値)だけを材料にする。試験は env も network も触らずに
  `AgentdSettings` を組むだけで全経路を検められる。実 I/O の handler(`RecordHttp`)を差し替える必要は無い。
- expected_scope: 試験の file だけ。
- unchanged: 本体のすべての module。

## 維持すると主張する契約

C1〜C4(`design.md` §3.2)。
