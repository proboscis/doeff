# 盲検 A — 入力と返答

## 入力(prompt の要旨)

対象 repo /home/ubuntu/.worktrees/doeff-wt-design-check-fw の os_files.py と test_file_watch.py、設計本文 design.md。
変更の要求: (1) 見張りの数を既定の executor の thread の数より多くしても、既定の executor を使う他の仕事が遅れない。
(2) 見張りを待つ task を取り消したら、待ちの thread が待ち直さずに終わる。
問い: 要求は満たされているか(担う関数・行) / 変える module と変えなくてよい module / 既定の executor を使う残りの経路 /
実行して確かめた事(推測と実測を分ける)。file の変更・commit は禁止。

## 返答(subagent の最終の報告)

結論: 2 つの要求は今の実装で満たされています。変わったのは待ちの thread の持ち方(M-wait)だけです。ただ、取り消しの後に残る待ちが、
届いた変化を捨ててしまう事を実測で見つけました。これは直す前から在り、今回の変更による退行ではありません。

1. 要求 1: 満たされている。見張りの待ちごとに専用の daemon thread を 1 本起こす(os_files.py:188-200)。時間切れの後の待ち直しは同じ
   thread の中の loop(_wait_in_thread・143-147)。答えは loop.call_soon_threadsafe で loop へ(_settle・170-174)。今の os_files.py に
   既定の executor を使う呼び出しは無い。要求 2: 満たされている。ただし終わるまでに最大約 5 秒。Cancel → ep.on_cancel(fut.cancel)
   (handlers.py:494)→ bridge の task の取り消し → answer の取り消し → done callback で abandoned.set()(193)→ while の条件が偽(143)。
2. 変える module は _NextChanges / _wait_in_thread / _settle だけ。_Notifier・os_file_watch_handler・既定の executor は変えなくてよい
   (待ちの thread の場所と無関係・効果と答えの形が同じ・外の共有物)。design.md の「M-wait だけ」と同じ見立て。
3. 残る経路: (実測)取り消された待ちが変化を捨てる(143-147・163-164)— 直す前の版でも同じ。取り消しから thread が止まるまで最大 5 秒で、
   その間の新しい待ちと同じ notifier に 2 本が入る(推測)。(推測)GIL の在る Python では見張りの数に比例して GIL 待ちが増える(3.14t では効かない)。
   既定の executor を使う他の物(見張りの数に比例しない): storage/memory.py・storage/sqlite.py の to_thread、aiohttp_http_server.hy:371 の probe-answer。
   検の穴: C1 は 2 本・4 本だけ(上限 4 本以上の専用 pool に差し替えた退行は通る)。取り消しの検は 0.02 秒の代役で 5 秒の上限を見ていない。
4. 実測: test_file_watch.py → exit 0・11 passed。Python 3.14.3t・CPU 36 → 既定の executor 32 本。本物の watchfiles の見張り 64 本で
   thread 64 本・run_in_executor(None) 0.065 秒・getaddrinfo 0.0067 秒・1 本を取り消すと thread は 4.21 秒後に止まった。
   取り消し → 0.1 秒後に書く → 新しく待つ: (1, 0, FilesChanged(paths=())) で変化は届かない。直す前の版でも FilesChanged(paths=())。

使った model: Opus 5.5(claude-opus-5-5)、およそ 4 分。
