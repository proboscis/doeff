# 設計(盲検の前に固定) — worker の process 自身の行を job の log の置き場へ送る

依頼 lt-8PQM3S5DDHCSJ92Q7X259K9HAW・card ki-35923a5895b2。完了の範囲 = **実装完了まで**(code は main に入った。機体の worker の版上げと起こし直しは範囲の外 — 起こし直しの決まりに従う別の段)。

対象の版: doeff 6c385dbb7e194c3277cb1581d71fd7921e4a776b(L2173)・agora-controllers 48dfe18a(L6371)。

## 要件(受入の条 ccd8d5cc8)

zeus-native と mac-native の worker の process の行(「worker: heartbeat の間の遅れ」を含む)を、Pod の会話から agora の命令 1 つで、時刻の範囲を指定して読める。

## 利用の状況

- 機体の worker(zeus = systemd・Mac = launchd)は stdout と stderr を同じ file へ書く(同じ path を 2 度開く)。会話の Pod はその file を読めない。
- job の子の行は、worker の刻つきの書き手(line_stamp.py)が取り決め job-log/1(OTLP/HTTP の logs)で collector へ送り、ClickHouse の表 job_logs に入り、`agora job-log <job>` で読める。
- 機体の worker の環境には送り先 DOEFF_JOB_LOG_OTLP と配備の属性 DOEFF_JOB_LOG_ATTRIBUTES(agora.tenant=personal)が既に在る。Pod の worker には無い。

## 責務(module)

| id | 置き場 | 持つ判断・知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| M-writer | doeff line_stamp.py の stamped_lines・StampedLines・pumped | fd 1・2 を 1 本の pipe へ差し替え、行の頭に刻を足して元の file へ書き、終わりに読み切る | pipe と fd の扱い・刻の綴り | `stamped_lines(plan) -> StampedLines`(begin・end) |
| M-shipper | line_stamp.py の JobLogShipper・logs_body・record_json・ShipLimits と foundation/otlp_post.py | OTLP の JSON・溜めの上限・束ね・POST・捨てた行の数え | 送りの thread・上限の値・HTTP | `JobLogShipper(target)`(take・finish・begin・end) |
| M-plan | line_stamp.py の job_log_plan・worker_log_plan・extra_attributes | 送るか・どこへ・何と名乗るか(job の名 worker/<名>・試み 0・世代 boot) | 環境変数の名・名乗りの決まり | `worker_log_plan(environ, worker, instance) -> JobLogPlan` |
| M-process | line_stamp.py の process_lines | worker の process で差し替えてよいかの判断(送り先が使えて stdout と stderr が同じ file)と、終わりを process の終わり(atexit)に置くこと | atexit・same_file | `process_lines(plan) -> OutputLines` |
| M-entry | doeff worker/entry/main.hy | 組み立て: 環境を ReadEnvironment で読み、boot を決め、process-lines を 1 度呼ぶ | — | (入口) |
| M-contract | agora-controllers deploy/claude-telemetry(yaml の頭・job_logs.sql・collector の振り分け)と検 test_job_log_route.hy | 取り決め job-log/1: service.name の振り分け・属性から作る列・1 行の上限・印の名 | 店の種類・口座の DB | OTLP の resource / record の属性 |
| M-reader | agora-controllers daily_verify の job_log_store・API・CLI agora job-log | Job・Worker・刻で引いて返す | SQL・店の接続 | `agora job-log <job> [--since] [--worker]` |

## 公開の契約と状態

- `worker_log_plan` の答えは JobLogTarget | JobLogOff | JobLogRefused の 3 つ(判別できる和)。名乗りは doeff の 5 つ(service.name・doeff.worker・doeff.job・doeff.attempt・doeff.instance)が配備の属性に勝つ。
- `process_lines` は JobLogTarget かつ same_file(1,2) の時だけ StampedLines を返して begin し、end を atexit に置く。それ以外は RawLines(何も替えない)で、Refused と別の file の時は理由を 1 行 stderr に書く。
- 送りは worker の write を止めない(書き手は溜めへ入れるだけ・溢れは捨てて数える・POST は別の thread)。file への書きが先で、送りの遅れで file を欠かない。
- 終わり: atexit が end を呼ぶ(捕まえない例外の traceback は excepthook が atexit より前に書く)。SIGKILL の時は pipe に残った分を失う(job の shim と同じ残る穴)。

## 変更の筋書き(6 軸)と事前の主張

| id | 軸 | 適用 | 変更 | 主張(変える・変えない) |
|---|---|---|---|---|
| S1 | hardware | 適用 | 機体の worker を 1 つ足す(例: 別の Linux の機体の systemd の worker eos-native) | 変えるのは配備(dotfiles の unit — WORKER_NAME・DOEFF_JOB_LOG_*)だけ。M-writer・M-shipper・M-plan・M-process・M-entry・M-contract・M-reader は変えない。job の名は worker の名から作られ、読み手は job の名 worker/eos-native で引ける |
| S2 | storage | 適用 | 置き場の口座を替える(会社の境界の worker が company の口座の DB へ送る) | 変えるのは配備の属性(DOEFF_JOB_LOG_ATTRIBUTES の agora.tenant)と M-contract・M-reader の口座の側だけ。doeff(M-plan 以下)は口座の語彙を持たないので変えない。配備の属性は doeff の名乗り(doeff.job など)を書き換えられない |
| S3 | effects | 適用 | 送り先(collector)が落ちる・答えない | worker の write も終わりも送りに待たされず、file は全部残る。変えるのは無し(M-shipper の中の上限で閉じる) |
| S4 | distribution | 除外 | Pod の worker の process の行も送る | Pod の worker の行は kubectl logs で読める・要求に無い。Pod の stdout と stderr は別の pipe なので M-process は差し替えない(今の形)。送るなら別の設計(1 本ずつの pipe)が要り、この主張の範囲の外 |
| S5 | concurrency | 適用 | worker の中の複数の thread(offloaded-tree-handler の thread・HTTP)が同時に書く・行が多い | 行は 1 本の pipe に書いた順で入り、書き手は送りに待たされない(溜めが溢れたら捨てて数え、番号の飛びで分かる)。M-entry と業務の Program は thread を知らない |
| S6 | simulation | 除外 | 模擬の環境で worker の行の送りを決定的に回す | 差し替えは入口の層の OS の扱い(fd・atexit)で、模擬の worker(sim/local.hy の worker-on)は main を通らない。Program の効果ではないので模擬の時計の外 |

## 強制方法(事前)

| 守る責務 | 強制方法 | 実装の場所 | 実行の経路 | 限界 |
|---|---|---|---|---|
| line_stamp を使えるのは shim・warm_child・worker の入口・検だけ | code-quality の module 契約(allow の表) | .agents/code-quality.d/doeff-cluster-line-stamp.json | commit の hook・日次 | Python の import の向きだけ。呼び方(begin の前に fork しない等)は見ない |
| 入口は os.environ を直に読まない | doeff-linter DOEFF106 | main.hy は ReadEnvironment で読む | commit の hook・日次 | line_stamp.py の中の os 呼びは見ない(entry の Python) |
| 名乗りと送り方と file の完全さ | 本物の process の検 | packages/doeff-cluster/tests/test_job_log_ship.py(worker の 4 本と既存の 27 本) | 日次の packages の段 | Linux だけで走る(Mac は未実測) |
| 送り手の 1 行の上限・印の名が取り決めと同じ | 契約の検 | agora-controllers controllers/agora_sim/tests/test_job_log_route.hy | agora-controllers の日次 | job の名の綴り(worker/<名>)は検しない |
| 機体の worker の版 ≦ coordinator の版 | dotfiles の検 test_machine_workers_are_not_newer_than_the_coordinator | dotfiles agent/tests/test_doeff_host_worker.py | dotfiles の全体検証 | 起こし直しの時期は縛らない |
