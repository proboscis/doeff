# #2854 の調べの証拠(card ki-a4042e38352a・依頼 lt-WGRPDC6D074R32EX4Y4THKP5G7)

着地しない記録。調べの結果は依頼書(2 本)と報告に書いた。ここは数字の出どころ。

- `data/hist-ac.json.txt` — agora-controllers の main に残っていた過去の log 37 本から取り出した、検ごとの (壁の時計, CPU 秒, 歩数) の 355 観測(243 本の検)。`scripts/extract_hist.py.txt` が作る。
- `data/small-1.json.txt`・`runs/small-1.log.txt` — 2026-10-12 に zeus で、controllers/kanban・card_scheduler・patrol・artifact_store の検と idle_cli の 1 本(164 本)を -v で走らせた、検ごとの歩数と CPU 秒(load 35〜80)。
- `runs/spike-idle-cli.txt`・`runs/small-calls-toggle.log.txt` — sys.monitoring で Python の呼び出しを数えた試作(`scripts/python_calls_spike.py.txt`)の実測。
- `scripts/mon_bench.py.txt`(上乗せの最悪の負荷)・`scripts/thread_count.py.txt`(free-threaded で複数 thread が数を落とすか)・`scripts/analyze_*.py.txt`・`scripts/an_*.py.txt`(集計)・`scripts/proto_ratio.py.txt`(CPU 秒 ÷ 歩数の 1 行の規則の試作)。
- 使う時は `.txt` を外し、`uv run --no-project python <file>` か `uv run --script <file>`(`# /// script` の宣言つき)。
