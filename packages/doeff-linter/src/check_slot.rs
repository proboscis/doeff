//! 機体の検の枠(card ki-ce35bf38508d・親 ki-aabe38bf85ef)— 重い mode の doeff-linter は、起動の時に機体の検の枠の正本
//! (`~/dotfiles/agent/tests/machine_check_slots.py`)の命令の包み `run` へ自分を渡し、枠を 1 つ取ってから走る。
//!
//! なぜ: 同じ機体(Pod)で同時に走る検の process 木を、正本の宣言の K 本までに絞る。doeff-linter は会話が直に撃つ道
//! (`uv run doeff-linter`・poe の lint・land_lint_gate・日次の段・code-quality の中)が多く、道ごとに包むと
//! 包み忘れた道が枠の外で走る(10-12 08:39 JST の zeus・load 105 で poe の lint 1 本 1.9 core と会話の直 2 本 0.64 core が枠の外)。
//! binary の起動の 1 点で取れば、どの道から撃っても同じ枠に入る。commit の hook(`--commit-hook`)は検でなく止める検査なので
//! 取らない(weight の註・card ki-b96d1fd0433f)。
//!
//! 形: 自分の process を python へ exec し(pid は同じ — 呼び手の信号と時間切れは今までどおりこの pid に届く)、
//! `python <正本> run --label <名> -- <自分> <同じ引数>` で走り直す。正本が枠を取り(埋まっていれば保持者を名乗って待ち、待ちの
//! 上限で終了 75)、自分を子で走らせ、終われば解く。包みが SIGKILL で死ねば子も死ぬ(正本の die_with_wrapper)。
//! 枠の値(本数・置き場・待ちの上限)と取り方は正本と隣の machine_check_slots.json だけが持ち、ここは持たない —
//! ここが持つのは正本の在処と、どの mode が重いかと、再入の印だけ。
//! 枠を持つ process の子孫(pytest の中の linter・包んだ段の中の linter)は、正本が祖先の列で読んで 2 つ目を取らない。
//! 包みから起こされた linter には再入の印(GATED_ENV)が付き、その子孫は python を通らない。枠を取らない `--commit-hook` が
//! 起こす子の linter(editor-json — 重い mode の形)にも同じ印が付き、hook の一部として枠の外で走る。
//! 正本の無い機体(dotfiles を置かない機体)と、python を見つけられない時は、枠を取らずに走る(正本が宣言を読めない時と同じ)。
//! 費え: 重い mode の 1 回に `uv python find` と python の起動で 0.2〜0.6 秒(zeus の load 70〜90 で測った)。

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::process::Command;

/// 再入の印 — 包みが起こした linter(とその子孫)と、`--commit-hook` が起こす子の linter(commit_hook::run_linter)に付く。
/// 付いていれば包みへ渡さない。
pub const GATED_ENV: &str = "DOEFF_LINTER_CHECK_SLOT";

/// 正本の置き場(HOME からの相対)— agora の各 repo の root の conftest が受付の正本を読む置き場と同じ根。
pub const CANON_UNDER_HOME: &str = "dotfiles/agent/tests/machine_check_slots.py";

/// 実行の重さ — 枠を取るか。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Weight {
    /// 枠を取らない(表を出すだけ・呼び手が短い上限で切る問い)。
    Light,
    /// 枠を取る(repo か path の束を lint する)。名は保持者の名乗りに入る mode の名。
    Heavy(&'static str),
}

/// mode の判定に要る引数の欄(main の Args から写す)。
#[derive(Debug, Clone, Copy, Default)]
pub struct Mode {
    pub list_rules: bool,
    pub split_rules: bool,
    pub affected_tests: bool,
    pub stdin: bool,
    pub hook: bool,
    pub commit_hook: bool,
    pub editor_json: bool,
}

/// 実行の重さを決める(純粋)。
///
/// 取らない: `--list-rules`・`--split-rules`(規則の表を出すだけ — CPU 0 秒)/ `--affected-tests`(検を選ぶ問い・CPU 1.8 秒
/// 0.15 core — 呼び手 land_focus_gate は 30 秒で切り、測れなければ逆依存の検を足さないので、待つと検が落ちる)/ `--stdin`
/// (保存前の 1 file — 書き込み直後の hook は全体 3 秒で切って黙って通すので、待つと hook が効かなくなる)/ `--commit-hook`
/// (test でなく、stage した file に file 1 つで決まる規則を当てる止める検査 — 子の linter 1 回ごとに自分の CPU 秒の上限
/// [tool.doeff-linter.commit_hook] timeout_s を持つ。枠に並ぶと pytest と型の検の後ろで最長 20 分待ち、会話の道具の上限で commit が
/// 切れた — card ki-b96d1fd0433f・利用者の決定 2026-10-12: commit の前の検査は test ではないので、ほかの検の後ろで待たせない。
/// 子の linter も取らない — commit_hook::run_linter が GATED_ENV を付けて起こす)。
/// 取る: `--hook`(Cursor の stop hook — workspace の全体)・editor-json と text / json の repo 全体(CPU 100 秒超)。
pub fn weight(mode: Mode) -> Weight {
    if mode.list_rules || mode.split_rules || mode.affected_tests || mode.stdin || mode.commit_hook {
        Weight::Light
    } else if mode.hook {
        Weight::Heavy("--hook")
    } else if mode.editor_json {
        Weight::Heavy("editor-json")
    } else {
        Weight::Heavy("lint")
    }
}

/// 保持者の名乗り(純粋)— mode の名と、走らせた dir の名(作業木の名)。会話の id は正本が足す。
pub fn label(mode_name: &str, cwd: Option<&Path>) -> String {
    let place = cwd
        .and_then(|dir| dir.file_name())
        .map(|name| format!(" {}", name.to_string_lossy()))
        .unwrap_or_default();
    format!("doeff-linter {}{}", mode_name, place)
}

/// 正本の `run` へ自分を渡す命令の列(純粋)— `[python, 正本, run, --label, 名, --, 自分, 引数…]`。
pub fn gate_command(python: &Path, canon: &Path, label: &str, exe: &Path, args: &[OsString]) -> Vec<OsString> {
    let head: [&OsStr; 6] = [
        python.as_os_str(),
        canon.as_os_str(),
        OsStr::new("run"),
        OsStr::new("--label"),
        OsStr::new(label),
        OsStr::new("--"),
    ];
    head.iter()
        .map(|part| part.to_os_string())
        .chain(std::iter::once(exe.as_os_str().to_os_string()))
        .chain(args.iter().cloned())
        .collect()
}

/// 正本の在処(HOME の下)。HOME が無いか正本が無ければ None。
fn canon_path() -> Option<PathBuf> {
    let home = std::env::var_os("HOME").filter(|value| !value.is_empty())?;
    let canon = Path::new(&home).join(CANON_UNDER_HOME);
    canon.is_file().then_some(canon)
}

/// 正本を走らせる python — `uv python find` の答え(作業木の venv が在ればそれ)。機体の python3 や venv の bin を直に探さない
/// (operator の決定 2026-09-27・2026-09-25 — python は uv で選び、機体の python や venv を直に使わない)。
/// 正本は標準の library だけで走る。`uv run` で包まないのは、uv が子を起こして待つ形だと、呼び手の SIGKILL が uv だけを殺し、
/// 包みと linter が孤児で残るため(exec した python は linter と同じ pid)。
fn python_for_canon() -> Result<PathBuf, String> {
    let found = Command::new("uv")
        .args(["python", "find"])
        .output()
        .map_err(|error| format!("python を探す uv を起こせない({error})"))?;
    let text = String::from_utf8_lossy(&found.stdout).trim().to_string();
    if found.status.success() && !text.is_empty() {
        Ok(PathBuf::from(text))
    } else {
        Err(format!(
            "`uv python find` が python を答えない(終了 {:?}・{})",
            found.status.code(),
            String::from_utf8_lossy(&found.stderr).trim()
        ))
    }
}

/// 重い mode の起動で、正本の `run` へ自分を渡す(exec — 戻らない)。印が付いている・正本が無い時は何もせず戻る。
/// 渡せなかった時(python が無い・exec の失敗)は 1 行名乗って戻り、枠を取らずに走る。
#[cfg(unix)]
pub fn enter(weight: Weight) {
    use std::os::unix::process::CommandExt;

    let Weight::Heavy(mode_name) = weight else {
        return;
    };
    if std::env::var_os(GATED_ENV).is_some() {
        return;
    }
    let Some(canon) = canon_path() else {
        return;
    };
    let exe = match std::env::current_exe() {
        Ok(exe) => exe,
        Err(error) => {
            eprintln!("doeff-linter: 機体の検の枠を取らずに走る — 自分の path を読めない({error})");
            return;
        }
    };
    let python = match python_for_canon() {
        Ok(python) => python,
        Err(reason) => {
            eprintln!("doeff-linter: 機体の検の枠を取らずに走る — {reason}");
            return;
        }
    };
    let cwd = std::env::current_dir().ok();
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let command = gate_command(&python, &canon, &label(mode_name, cwd.as_deref()), &exe, &args);
    let error = Command::new(&command[0]).args(&command[1..]).env(GATED_ENV, "1").exec();
    eprintln!(
        "doeff-linter: 機体の検の枠を取らずに走る — 正本 {} を {} で起こせない({error})",
        canon.display(),
        python.display()
    );
}

/// Unix でない機体には正本の命令の包みが無い(flock と /proc の祖先の列で数える)— 枠を取らずに走る。
#[cfg(not(unix))]
pub fn enter(_weight: Weight) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cheap_and_caller_bounded_modes_take_no_slot() {
        for mode in [
            Mode { list_rules: true, ..Mode::default() },
            Mode { split_rules: true, commit_hook: true, ..Mode::default() },
            Mode { affected_tests: true, ..Mode::default() },
            Mode { stdin: true, editor_json: true, ..Mode::default() },
            Mode { commit_hook: true, ..Mode::default() },
        ] {
            assert_eq!(weight(mode), Weight::Light, "{mode:?}");
        }
    }

    #[test]
    fn repo_scans_take_a_slot_named_by_their_mode() {
        assert_eq!(weight(Mode { hook: true, ..Mode::default() }), Weight::Heavy("--hook"));
        assert_eq!(weight(Mode { editor_json: true, ..Mode::default() }), Weight::Heavy("editor-json"));
        assert_eq!(weight(Mode::default()), Weight::Heavy("lint"));
    }

    #[test]
    fn the_gate_command_hands_the_same_arguments_to_the_canon() {
        let args = vec![OsString::from("--no-log"), OsString::from("src")];
        let command = gate_command(
            Path::new("/venv/bin/python"),
            Path::new("/home/u/dotfiles/agent/tests/machine_check_slots.py"),
            "doeff-linter lint repo",
            Path::new("/venv/bin/doeff-linter"),
            &args,
        );
        let expected: Vec<OsString> = [
            "/venv/bin/python",
            "/home/u/dotfiles/agent/tests/machine_check_slots.py",
            "run",
            "--label",
            "doeff-linter lint repo",
            "--",
            "/venv/bin/doeff-linter",
            "--no-log",
            "src",
        ]
        .iter()
        .map(OsString::from)
        .collect();
        assert_eq!(command, expected);
    }

    #[test]
    fn the_label_names_the_mode_and_the_working_tree() {
        assert_eq!(label("lint", Some(Path::new("/w/agora-controllers-wt-x"))), "doeff-linter lint agora-controllers-wt-x");
        assert_eq!(label("--commit-hook", None), "doeff-linter --commit-hook");
    }
}
