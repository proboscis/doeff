# 盲検 A・B に渡す材料(設計検証 lt-5KHSMYM9R1HW7AKJBRG32NNF4S)

## 対象

「doeff-hy-check(doeff の Hy の型検査の道具)が 1 つの機体の検の枠の中で並べる process の数を、機体の検の枠の宣言(dotfiles)の 1 つの数に従わせる」設計と、その最小実験(試作)。

- 設計(要件・現状・決め・責務・変更シナリオ S1〜S6 と事前の主張・強制方法・完了の範囲): 同じ dir の `design-pre-blind.md` の全文。
- 試作の版(読むだけ):
  - doeff: `/home/ubuntu/.worktrees/doeff-wt-ppslot-blind`(branch wt/ppslot-blind・7e092becc = 試作 0dfe61d56 の上に文の整理だけを足した版。基準は doeff main a4abf5218)
  - dotfiles: `/home/ubuntu/.worktrees/dotfiles-wt-ppslot-blind`(4b9b243b9 = master cd7a49008 の上の試作)
  - 基準との差: `git -C <木> diff a4abf5218 HEAD`(doeff)・`git -C <木> diff cd7a49008 HEAD`(dotfiles)

## 読んでよい実コード(読むだけ)

doeff の木の下:
- `packages/doeff-hy/src/doeff_hy/check_slot.py`(M-slot)
- `packages/doeff-hy/src/doeff_hy/static_check.py`(M-main — `expander_limit`・`pyright_threads`・`main` の枠を取る所と幅を使う所)
- `packages/doeff-hy/src/doeff_hy/env_places.py`(M-env-places)
- `packages/doeff-hy/tests/test_check_slot_opacity.py`(M-opacity-guard)
- `packages/doeff-hy/tests/test_static_check_slot.py`(M-slot-scene)
- `packages/doeff-hy/tests/test_static_check_parallel.py`・`test_static_check_pyright_threads.py`・`test_env_places_read.py`
- `.agents/code-quality.d/doeff-hy-check-slot.json`(M-slot の契約の断片)

dotfiles の木の下:
- `agent/tests/machine_check_slots.py`(M-canon — 枠の正本)・`agent/tests/machine_check_slots.json`(宣言)・`agent/tests/test_machine_check_slots.py`

他の入口の例(読むだけ): `/home/ubuntu/repos/agora` の origin/main にある `scripts/node-test.mjs`・`scripts/check-slot.test.mjs`(env の AI_CHECK_SLOT_PROCESSES だけを読む JS の入口と、その入口が宣言を直に読まない事を見る検)。

## 検査の実体・設定・検出範囲(B 用 — これらが通ることが「検査を通る」)

doeff の木で、dotfiles の木を HOME の下の `dotfiles` として見せて走らせる(機体の ~/dotfiles の正本は幅の口が無い版のため、口が要る 2 本は skip になる):
1. M-opacity-guard: `uv run pytest packages/doeff-hy/tests/test_check_slot_opacity.py`
2. M-slot-scene: `uv run pytest packages/doeff-hy/tests/test_static_check_slot.py`
3. 幅の使い手: `uv run pytest packages/doeff-hy/tests/test_static_check_parallel.py packages/doeff-hy/tests/test_static_check_pyright_threads.py packages/doeff-hy/tests/test_env_places_read.py`
4. 変更時の品質検査: `sh ~/dotfiles/agent/venv-run.sh ~/dotfiles/agentcli code-quality --repo <doeff の木> --scope changed --base a4abf5218`(速い規則だけ。型検査は日次で、この検査には含まれない)
5. 正本の検(dotfiles の木): `uv run --project agentcli pytest agent/tests/test_machine_check_slots.py`

HOME を替える場合の例: 空の dir H を作り `ln -s <dotfiles の木> H/dotfiles`、`HOME=H XDG_CACHE_HOME=/home/ubuntu/.cache uv run pytest …`。

## 守ること

共有の木(上の 2 つの木と ~/repos 以下)の file を変えない。git の commit・branch の作成をしない。全数の検査を走らせない。他の会話へ連絡しない。実験が要るなら、/tmp の下に自分の複製を作って行う(この機体は混んでいる — 上の検査は名指した focused の物だけ、必要な時に限って走らせる)。計測・実行できなかった物は『未実行』と書き、推測と実測を分ける。
