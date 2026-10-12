# 盲検 B — 入力と返答

## 入力(prompt の要旨)

対象 repo /home/ubuntu/.worktrees/doeff-wt-design-check-fw の os_files.py と test_file_watch.py、設計本文 design.md。
主張: 待ちは専用の daemon thread で回り既定の executor を持たない / 取り消しで今の待ちの後に止まる / 既存の法(変化を落とさない・待ちが
中に居る間に close しない)は保たれる / 追加の検 2 本がこれを縛る。役は失敗例探し(承認ではない)。再現 script を書いて実行し、推測と実測、
直す前から在った物と新しく生じた物を分ける。repo の file の変更は禁止(script は notes-design-check-fw/blind-b/ の下だけ)。

## 返答(subagent の最終の報告)

失敗例 1 件(この変更で新しく生じた)、直す前から在った失敗例 1 件、検の穴 2 件、観察 1 件。環境 Python 3.14.3t・watchfiles 1.3.0。

- 失敗例 1(実測・新しい): notifier の待ちが BaseException を出すと、待つ task が永遠に止まる。_wait_in_thread は except Exception だけ
  (148 行)。BaseException の子で thread が死に answer が決まらない。PyO3 の PanicException は BaseException から派生する(PyO3 の文書の知識。
  本物の panic はこの環境で起こせなかった)。旧い版は asyncio.to_thread の worker が BaseException も future に渡していた。
  再現 blind-b/repro_baseexception.py new|old: new は exit 0・stderr に "Exception in thread doeff-file-watch: … PanicException"・
  "hung after 3 s: True | result: {}"。old は "hung after 3 s: False | result: {'error': "PanicException(...)"}"。
- 失敗例 2(実測・直す前から在った): 取り消された待ちの thread が変化を食べて捨てる(A が待つ → A を取り消す → 書く → B が待つ)。
  repro_cancel_loss.py: new between 8/8・new during 3/8・old between 8/8・old during 3/8 が落ちた。退行ではない。
- 検の穴: M5(try/except を丸ごと消した変異)と M3(loop.is_closed() の確かめを消した変異)が、どちらも 11 本の検を通った
  (MUTANT=<M> PYTHONPATH=…/blind-b uv run --no-sync pytest -q -p mutate_plugin … → 11 passed・exit 0)。
  失敗を task へ渡す振る舞いを縛る検が無い。M3 の害は process の終わりだけで小さい(推測)。
- 観察(実測): 待ちごとの thread が 50 ms ごとに起きるので CPU が見張りの数に比例する。new: 50 本で 1.9%・400 本で 11.9%。
  old: 1.3%・1.2%(32 本を越えた待ちは列に並んで動いていなかった — それ自体が直した不具合)。pids.max 143173・inotify の
  max_user_instances 8192(推測で当面の心配なし)。
- 見つからなかった物: 既定の executor の空き(C1 と C3 の組で捕まる — 推論)・close と待ちの重なり(_Notifier は不変)・loop が閉じる時
  (is_closed で止まる・RuntimeError も握る — 推論)・同じ見張りへの同時の待ち(新旧同じ)・取り消しの届き方(確かめた・C3 緑)。

Model: Opus 5.5(claude-opus-5-5)、およそ 20 分。
