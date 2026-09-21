# 盲検の**前**に固定した主張(変更シナリオと予想した波及範囲)

- 保存時刻: 2026-09-21T05:2xZ(盲検 A・B の起動より前)
- 基準 commit: doeff `57641077306214b7b7da86708b04e9ed0524499b`
- 対象の設計: `design.md`(同 dir)

⚠ この file は盲検の返答を受けても**書き換えない**。反例を受けた結論は
`counterexamples.md` に別に書く。

## 主張の前提(すべての claim が依る)

- P1. 席を起こすのは agentd(node の側)で、正本の file はその node の checkout に在る。
- P2. 家(`CLAUDE_CONFIG_DIR`)は同じ資格の複数 session が共有し、doeff だけが書く。
- P3. 本体(Claude Code)の読み口は §2 の逐語のとおり(版が動けば §6 の計器が赤くなる)。
- P4. 宿ごとの差は参加宣言の 1 点に在る(`claude_settings_file` が既に取っている形)。

## module id(`report.json` の `modules` と対応)

`dotfiles-canon` / `host-declaration` / `join-judgment` / `agentd-effects` /
`launch-readout` / `claude-home-installer`

## 変更シナリオ(6 軸)

### S1 `storage` — 正本の置き場が checkout から**別の置き場**へ移る(applicable)

- change: 宿の共通条文と skills を、dotfiles の checkout ではなく専用の volume / OCI layer /
  `/opt/agent-canon` のような機体の別の置き場から配る(pod で dotfiles を clone しない形)。
- claim: **変わるのは `host-declaration` の 2 鍵の値だけ**。doeff 側は 1 行も変わらない。
- expected_scope: `cron_management/*.toml` の 2 行(+ pod の yaml の 2 行)。
- unchanged: `join-judgment`(形しか見ない)・`agentd-effects`・`launch-readout`・
  `claude-home-installer`(運ばれてきた path を実体化するだけ)。

### S2 `effects` — 運ぶ物が **1 種類増える**(applicable)

- change: 共通の `agents/`(subagent 定義)や `commands/` も席へ配りたくなる。
- claim: 足すのは**鍵 1 本 + 据え付け 1 行**で、判断の層(`join-judgment`)は
  「path の形」の検めを共有し、家の綴りは `claude-home-installer` の 1 か所に閉じる。
  **`launch-readout` の 4 枚の名簿を手で触らせる形にはしない**
  (d8472e1a の教訓 = 欄を 1 つ足す操作が名簿を 4 枚触らせる形は 3 度壊れた)。
- expected_scope: `agentd-effects`(綴り 1 本)・`join-judgment`(鍵の集合 1 行)・
  `claude-home-installer`(据え付け 1 行)・宣言 1 行。
- risk(自分で挙げる): **`launch-readout` にも 1 行増える**。ここが増え続けるなら、
  2 鍵を「席へ据える正本の名簿」1 つの欄へ畳む改訂が要る(設計の弱点として登記)。

### S3 `concurrency` — 同じ家で席が同時に起きる / 走行中に正本が動く(applicable)

- change: 1 つの account の家で 10 席が同時に起き、途中で dotfiles が更新される。
- claim: 書きは冪等(同じ宣言 → 同じ中身)かつ atomic(temp+rename)なので、
  **どの席も自分の起動の拍の中身を持ち、torn read は起きない**。skills は
  「既に正しければ張り替えない」ので走っている席の watcher を叩かない。
- expected_scope: 変わる責務は無い(`claude-home-installer` の内部の判断だけ)。
- unchanged: 公開契約(「家の中身は席の起動の拍で決まる」)。

### S4 `distribution` — 宿が 3 台から N 台へ・pod が複数の image 版で並ぶ(applicable)

- change: 4 台目の宿が増える / pod の replica が古い image と新しい image で混ざる。
- claim: もれなさは**針**(母集団を宣言から導く)が受け、版の崖は
  **node の行の `spec.agentd.revision`** で受ける。doeff の code は台数を知らない。
- expected_scope: 宣言 1 枚(+ 針は自動で母集団に入る)。
- unchanged: doeff 側すべて。

### S5 `hardware` — 機体の OS / arch / 本体の版が変わる(applicable)

- change: 会社 Mac が 2.1.278 → 2.2.x へ上がり、user 層の記憶の門の逐語が変わる。
- claim: **黙っては壊れない**。§6 の計器(据わっている本体から逐語を読む)が赤くなり、
  設計の前提 P3 が偽になった日が名指しで分かる。
- expected_scope: 計器が赤 → `claude-home-installer` の据え付けの形(実体 file か symlink か)を
  測り直す便が 1 本。
- unchanged: 宣言・join・env・launch の連鎖。

### S6 `simulation` — 決定的に再現したい(applicable)

- change: 実 claude を起こさずに「宣言 → 家の中の file」を検で固定したい。
- claim: `Fs*` / `EnvGet` だけの substrate-clean な層なので、既存の deftest の器で
  **家の中の file を数える**ところまで決定的に回せる(`memory_files` の検と同じ形)。
  本体の読みは実行体の逐語の計器が別に受ける(2 段に割る)。
- expected_scope: 検 1 枚 + 計器 1 枚。
- unchanged: 実装。

## 盲検 B(静的検査を通る責務違反)に対して先に張っている網

- N1. `.semgrep.yaml` の第 2 の綴りの禁止(`claudeMdExcludes` / 家の file 名)。
- N2. 参加の門 (c)(宣言が doeff の鍵を持てない)。
- N3. **argv の出口**を見る検(導出点の戻りではなく `--settings` に現に出る JSON)。
- N4. 家の中の file を数える deftest(層の内部で何をしようと、出た物で判定する)。
- N5. 据わっている本体の逐語の計器(外部の契約に依る条は witness とセットでだけ書く)。

**自分で見えている穴(盲検の前に登記)**:
- H1. `claude-home-installer` は「中身」を判断しないと決めたが、**中身を読んで分岐する code を
  足しても N1〜N5 のどれも止めない**(例: 「dotfiles の条文に `Company` の語が在る席では
  skills を張らない」)。
- H2. 据え付けの**順序**(CLAUDE.md を書く前に skills を張る等)は契約に書いていない。
- H3. 「家への書き口は 1 か所」は semgrep で縛っていない(`Fs*` はこの層で正当に使えるため)。
