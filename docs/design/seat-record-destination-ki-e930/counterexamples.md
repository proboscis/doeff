# 反例の再現・判定・修正

盲検の返答は `blind/A-raw.md`・`blind/B-raw.md`(無加工)。盲検前の主張は `claims-before-blind.md`(書き換えていない)。

## 1. A と B が独立に同じ穴を示した: 温かい session の続きの手番は、生まれた時の env を host の行から再生する

- A(S2 の範囲内・前提 P1〜P4 を崩さない): `[record].url` を変えて agentd だけを起こし直すと、既存の温かい session の次の手番は
  send の経路(`next-arm-for-job` が idle ∧ 同じ家 ∧ 同じ effort で send を選ぶ)を通り、host の `continue-headless-process` が
  行の `launch_overlay.session_env`(生まれた時の値)に手番の env(札だけ)を重ねて process を起こす。席は古い宛先を使う。
- B: 設計どおりの最小の実装(`counterexamples/B_counterexample.diff`)は K1〜K5 と既存の 2 検査を通るのに、同じ理由で Q1・Q4・S2 を破る。
  K1 が「1 つの settings の中で launch → continue」しか回さないので捕まらない。

### 設計者の再現(実行した)

- `counterexamples/B_witness_continue.py` を基準 commit の無改変の器で走らせた(`counterexamples/B_witness_continue.log`・exit 0):
  agentd B(宛先 URL2)の送りの手番の env は `{}`、起こした process の `RECORD_SERVICE_URL` は URL1(行の値)、URL2 と byte 同一でない。
- コードで確かめた経路: `headless.hy` 319〜321 行(launch が `overlay-without-turn-auth` を通した session_env を行へ保存 —
  落とすのは `CLAUDE_CODE_OAUTH_TOKEN` だけ)・448〜469 行(続きの process の env = 行の session_env | 手番の env)・
  `judgment.next-arm-for-job`(1559 行〜 — send の条件に機体の env は入っていない)。

### 判定: 成立(主張 S2 と要件 Q1 を破る)

- 盲検前の主張 S2「agentd と席が同時に新しい値を使う」は偽だった。実際の波及範囲は `host-declaration` 1 行では済まず、
  運用(host も入れ替える)か `charter-assembly` の外の判断(起こし方の判断)に及ぶ。
- A の分析のとおり、漏れていたのは「値の寿命の区分」: 会話の身元は会話ごとに一定なので行の再生で古くならないが、
  機体の宛先は session の途中で変わりうる。設計は記録の宛先を会話の身元と同じ扱いにして、この区分を見落としていた。
- この古びは宣言の `seat_env`(`ACP_BASE` 等)にも今日すでに在る(A の指摘・この設計が作った根ではない)。

### 修正(design.md §8)

**C5**: 機体が席へ渡す env(宣言の seat_env + 走行者の宛先 — 会話の身元と手番の札は含まない)を 1 つの関数で組み、
charter はその組を書き、同じ組の指紋を起こした session の帰属(`launch_attribution` の agentd の欄 `nodeEnv`)に刻む。
`next-arm-for-job` は、温かい session の帰属の指紋が今の agentd の指紋と違う(または無い)時に send を選ばず、
effort が違う時と同じ resume(温かい session を片付けて同じ session id で `--resume`・cache は保つ・charter を組み直す)を選ぶ。

- 寿命の知識は起こし方の判断(`judgment`・acp の層)の 1 点に置く。host(`headless.hy` / `policy.hy`)は変えない —
  A が挙げた「host の保存方針に名を教える」「手番ごとに言い直す」直し方は採らない
  (host に語彙が漏れ、codex の生きた process には手番ごとの env を届けられないため)。
- codex の生きた process も同じ resume の腕で片付けられ、新しい process で起きる。
- 配備の副作用: 配備前に生まれた session は帰属に指紋が無いので、各会話の次の手番が 1 度だけ resume になる(cache は保つ)。
  これは配備に必要な振る舞いでもある(それらの session の env には `RECORD_SERVICE_URL` が無い)。Mac の host を起こし直す必要が無くなる。

### 再検証(実行した)

- 試作の差分 `evidence/proto_arm_on_node_env_change.diff`(`judgment.hy` に 20 行 — 帰属の指紋・`next-arm-for-job` の比較・
  `node-seat-env-of`)を worktree に当て、本物の `next-arm-for-job` と `charter-with-seat-env` を呼ぶ
  `evidence/proto_arm_on_node_env_change.py` を走らせた(`evidence/proto_arm_on_node_env_change.log`・exit 0・5 件とも OK):
  同じ宛先 → send / 宛先が変わった → resume(候補を片付ける)/ 指紋の無い古い session → resume /
  組み直した charter の値 = URL2 / 記録が無効な settings では置かない。
- 試作は検証の後に `git apply -R` で戻した(`git diff --stat` が空)。本実装は実装の依頼が持つ。
- 未実行: 「settings A で launch → settings B の agentd が次の手番を起こす → 器が起こした process の env が URL2」の
  端から端までの試験(K1')。試作では起こし方の判断と charter の組み直しを別々に確かめただけで、resume の腕が
  host で新しい env の process を起こすことは既存の resume の経路(`incarnation-charter-of` が resume でも charter を組む)を
  読んで確かめた。K1' は実装の依頼の必須の検査にした。

### 検査の修正(implementation-request.md に反映)

- K1 → **K1'**: settings を手番の間で差し替える(settings A で launch → 行 → settings B の agentd の job-step)。器が起こした process の env が
  B の値であること。同じ settings のままなら send(cache を捨てない)であること。
- **K6**(新): `next-arm-for-job` の純粋な検 — 指紋が同じ → send / 違う → resume + retire / 指紋が無い → resume。
- K3 の拡張: 指紋の材料は charter が書く組と同じ関数の出力であること(組と指紋が別々の名簿を持たない)。

## 2. B の残りの指摘

- 「K1 が空の overlay で組まれていたら、実装者は送りの側にも名を運ばせ、K3 から見えない第 2 の書き手ができる」:
  C5 は送りの側に名を運ばせない(send を選ばないことで解く)。実装の依頼に「送りの手番の env(`turn-session-env-of`)へ
  走行者の名を足さない」を禁止として書き、K3 を「送りの env に走行者の名が 1 つも無い」まで広げた。
- 「席の settings file の `env` ブロックで上書きできるか」: 今日の `~/dotfiles/claude-hooks/seat-settings.json` の鍵は `hooks` だけで
  `env` は無い(実測 2026-09-24・`python3 -c "json.load(…).keys()"` → `['hooks']`)。ただし将来 `env` に走行者の名を書けば第 2 の定義点になる。
  実装の依頼に **K7**: join が席の settings file を検める同じ点で、`env` ブロックが走行者の名を含めば断る、を足した。
- 「`record_enabled=` を組む試験が 8 か所」: 実測 9 行(6 file)。欄の持ち方は実装者の自由(依頼書に記載)。

## 3. A の残りの指摘

- 「host の行に写しが残るので、agentd を通らない送り(操作者の救援など)は古い値で起きる」: 成立する。C5 は agentd が決める
  起こし方にだけ効く。限界として `design.md` §4 と依頼書に書いた(host の `session.send` を手で撃つ経路は生まれた時の env)。
- 「pool の pod の置き場の寿命」: pool は pod の入れ替えで host も落ちるので今日は古びが出にくいが、C5 の後は機体に依らない。
