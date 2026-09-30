//! commit の hook の入口(`doeff-linter --commit-hook`・agora-redesign #1989)。
//!
//! 各 repo(agora-controllers・merge-queue ほか)が hook の論理を写して持たず、この 1 か所を呼ぶ。repo が書くのは設定の
//! `[tool.doeff-linter.commit_hook]`(repo 全体に当てる規則の列と上限の秒)だけ。元の実装 = agora-controllers の
//! `scripts/commit_hook.hy`(lint-staged・whole-repo-fresh)。
//!
//! 止める物は 3 種:
//! - stage した source(.hy・.hyk・.hyp・.py)の、critical でない規則の登録簿の外の error(登録簿の既知は linter が warning に下げる)。
//! - stage した source の critical のうち HEAD の版に無い物(`--baseline-report` の `new_critical`)。
//! - repo 全体の比べ(設定の `whole_repo_rules` の列を repo 全体に当てる)の当たりのうち HEAD の木に無い物 — 当たりが変更の外の file
//!   (architecture.hy・登録簿の表)に付く規則は、stage した path に当てても出ない。source を 1 つも stage しない commit(表だけの変更)
//!   にも当てる。`whole_repo_rules` は stage した path に当てる規則から外す列。列に DOEFF166(当たらない登録簿の行)が在れば、linter が
//!   行の名指す規則を enable に無くても同じ実行で当てるので、列だけで当たらない行を見逃さない(agora-redesign #1999・#2033 — それまでは
//!   全部の規則で撃っていた・#1998)。
//!
//! 子の linter は 1 回ごとに上限(既定 20 秒)を持ち、越えたら「測れなかった」と 1 行出して止めない(速さが優先)。
//! 純粋な部分(規則の分け・鍵の差・止める当たりの選び)は関数に分けて、単体の検で確かめる。

use crate::config::{CommitHookSection, Config};
use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// 子の linter 1 回ごとの既定の上限(秒)。
pub const DEFAULT_TIMEOUT_S: u64 = 20;
/// Jev に問う意味の規則の番号の頭(DOEFF201〜205)— commit では問わない(cache を読むだけでも遅い)。
pub const SEMANTIC_PREFIX: &str = "DOEFF2";
/// stage した path のうち linter が読む物の末尾。
pub const LINTED_SUFFIXES: [&str; 4] = [".hy", ".hyk", ".hyp", ".py"];
/// 出す行の頭。
const PREFIX: &str = "doeff-linter commit-hook: ";

/// 有効な規則を、stage した path に当てる規則(quick)と、stage した path から外して repo 全体の比べに当てる規則(whole)に分けた物。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RuleSplit {
    pub quick: Vec<String>,
    pub whole: Vec<String>,
}

/// 純粋: 有効な規則から意味の規則(DOEFF2xx)を除き、設定の whole_repo_rules に在る物を whole、残りを quick に分ける(順は保つ)。
pub fn split_rules(enabled: &[String], whole_declared: &[String]) -> RuleSplit {
    let declared: BTreeSet<String> = whole_declared.iter().map(|r| r.to_uppercase()).collect();
    let mut split = RuleSplit::default();
    for rule in enabled.iter().filter(|r| !r.to_uppercase().starts_with(SEMANTIC_PREFIX)) {
        if declared.contains(&rule.to_uppercase()) {
            split.whole.push(rule.clone());
        } else {
            split.quick.push(rule.clone());
        }
    }
    split
}

/// 純粋: stage した path のうち、作業木に在り(exists)linter が読む物。
pub fn linted_paths(staged: &[String], exists: impl Fn(&str) -> bool) -> Vec<String> {
    staged.iter().filter(|p| LINTED_SUFFIXES.iter().any(|s| p.ends_with(s)) && exists(p)).cloned().collect()
}

/// 純粋: editor-json の出力のうち止める当たり — critical でない規則の登録簿の外の error(severity が error で level が critical
/// でない)。critical は登録簿の有無でなく HEAD との比べだけで判じるので入れない。
pub fn blocking_violations(report: &Value) -> Vec<&Value> {
    violations(report).filter(|v| v["severity"] == "error" && v["level"] != "critical").collect()
}

fn violations(report: &Value) -> impl Iterator<Item = &Value> {
    report["violations"].as_array().into_iter().flatten()
}

/// 違反の path を報告の根からの相対にする(根の外ならそのまま)。
fn relative_to_root<'a>(report: &Value, path: &'a str) -> &'a str {
    match report["root"].as_str() {
        Some(root) => path.strip_prefix(root).map(|rest| rest.trim_start_matches('/')).unwrap_or(path),
        None => path,
    }
}

/// 純粋: 違反の識別子 — 鍵(repo の根からの相対)か、鍵の無い違反(Python の文ごとの規則)は `<相対 path>::<規則>::<message>`。
/// 先端と HEAD の木は根が違うので、path は根からの相対で綴る。
pub fn violation_identity(report: &Value, violation: &Value) -> String {
    match violation["key"].as_str() {
        Some(key) => key.to_string(),
        None => format!(
            "{}::{}::{}",
            relative_to_root(report, violation["path"].as_str().unwrap_or("")),
            violation["rule"].as_str().unwrap_or(""),
            violation["message"].as_str().unwrap_or("")
        ),
    }
}

/// 純粋: 止める当たりの識別子の集合 — critical と、critical でない規則の登録簿の外の error。件数でなく鍵で比べるので、
/// 1 つ直して 1 つ足した変更も止まる。
pub fn report_idents(report: &Value) -> BTreeSet<String> {
    violations(report)
        .filter(|v| v["level"] == "critical")
        .chain(blocking_violations(report))
        .map(|v| violation_identity(report, v))
        .collect()
}

/// 純粋: repo 全体の規則の当たりのうち、先端(tip)に在って HEAD(head)に無い物の識別子(辞書順)。
pub fn fresh_whole_repo_hits(head: &Value, tip: &Value) -> Vec<String> {
    let before = report_idents(head);
    report_idents(tip).into_iter().filter(|id| !before.contains(id)).collect()
}

/// 純粋: 報告の違反の path と根を、from の根から to の根へ付け替える — HEAD の木で走らせた出力を、先端の根で走らせた出力と
/// 同じ path で比べる(鍵の無い違反の識別子は絶対 path を含む)。
pub fn rebase_paths(report: &mut Value, from: &str, to: &str) {
    let rebase = |text: &str| -> Option<String> {
        let rest = text.strip_prefix(from)?;
        (rest.is_empty() || rest.starts_with('/')).then(|| format!("{}{}", to, rest))
    };
    if let Some(root) = report["root"].as_str().and_then(rebase) {
        report["root"] = Value::String(root);
    }
    if let Some(list) = report["violations"].as_array_mut() {
        for violation in list {
            if let Some(path) = violation["path"].as_str().and_then(rebase) {
                violation["path"] = Value::String(path);
            }
        }
    }
}

/// 純粋: 止める当たりを 1 行に書く(`<相対 path>:<行>: <規則> <message の 1 行目>`)。
pub fn violation_line(report: &Value, violation: &Value) -> String {
    let path = relative_to_root(report, violation["path"].as_str().unwrap_or(""));
    let line = violation["range"]["start"]["line"].as_u64().unwrap_or(0) + 1;
    let message = violation["message"].as_str().unwrap_or("").lines().next().unwrap_or("");
    format!("{}:{}: {} {}", path, line, violation["rule"].as_str().unwrap_or(""), message)
}

/// 純粋: 設定と引数から子の linter 1 回ごとの上限を決める(引数が勝つ・無ければ設定・無ければ既定)。
pub fn resolve_timeout(section: Option<&CommitHookSection>, cli: Option<u64>) -> Duration {
    Duration::from_secs(cli.or_else(|| section.and_then(|s| s.timeout_s)).unwrap_or(DEFAULT_TIMEOUT_S))
}

/// 純粋: 設定の enable・disable と引数を当てた有効な規則の列(ふだんの実行と同じ merge_config・None は全部の規則)。
pub fn effective_rules(config: Option<&Config>, cli_enable: &[String], cli_disable: &[String]) -> Vec<String> {
    crate::config::merge_config(config, cli_enable, cli_disable, &[]).0.unwrap_or_else(crate::config::get_all_rule_ids)
}

/// hook の 1 回の実行の前提。
#[derive(Debug, Clone)]
pub struct CommitHookOptions {
    /// git の作業木の根(子の linter の cwd・stage の path の基準)。
    pub root: PathBuf,
    /// 設定 file(絶対 path・無ければ子は設定を探す)。
    pub config: Option<PathBuf>,
    pub rules: RuleSplit,
    pub timeout: Duration,
    /// 子として撃つ linter(ふつうは今の binary)。
    pub linter: PathBuf,
}

impl CommitHookOptions {
    /// 設定と引数から前提を組む。
    pub fn new(root: PathBuf, config: Option<(&Config, PathBuf)>, cli_enable: &[String], cli_disable: &[String], cli_timeout: Option<u64>, linter: PathBuf) -> Self {
        let section = config.as_ref().and_then(|(c, _)| c.commit_hook.as_ref());
        let enabled = effective_rules(config.as_ref().map(|(c, _)| *c), cli_enable, cli_disable);
        let whole: &[String] = section.map(|s| s.whole_repo_rules.as_slice()).unwrap_or(&[]);
        CommitHookOptions {
            rules: split_rules(&enabled, whole),
            timeout: resolve_timeout(section, cli_timeout),
            config: config.map(|(_, path)| path),
            root,
            linter,
        }
    }
}

/// 子の linter 1 回の答え。
#[derive(Debug)]
pub enum Measured {
    Report(Value),
    /// 上限を越えた(止めずに通す)。
    TimedOut,
    /// 動かなかった・出力が JSON でない(理由)。
    Failed(String),
}

/// 子の linter を editor-json で撃ち、上限の中で終われば出力を読む。終了コード 0・1・3・4 は測れた(中身で判じる)とし、
/// 他は理由つきの失敗。上限は撃つ前にも見る(0 秒なら撃たずに越えた扱い)。
pub fn run_linter(linter: &Path, cwd: &Path, args: &[String], timeout: Duration) -> Measured {
    let deadline = Instant::now() + timeout;
    if timeout.is_zero() {
        return Measured::TimedOut;
    }
    let spawned = Command::new(linter)
        .args(["--output-format", "editor-json", "--no-log"])
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => return Measured::Failed(format!("{} を起こせない: {}", linter.display(), error)),
    };
    // 出力は大きい(repo 全体の JSON)ので、pipe が詰まらないよう待つ間も別の thread で読み切る。
    let reader = |stream: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut stream) = stream {
                let _ = stream.read_to_end(&mut buf);
            }
            buf
        })
    };
    let stdout = reader(child.stdout.take().map(|s| Box::new(s) as Box<dyn Read + Send>));
    let stderr = reader(child.stderr.take().map(|s| Box::new(s) as Box<dyn Read + Send>));
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Measured::TimedOut;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(error) => return Measured::Failed(format!("子の linter を待てない: {}", error)),
        }
    };
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    let err_line = String::from_utf8_lossy(&err).lines().find(|l| !l.trim().is_empty()).unwrap_or("").to_string();
    match status.code() {
        Some(0 | 1 | 3 | 4) => match serde_json::from_slice::<Value>(&out) {
            Ok(value) => Measured::Report(value),
            Err(error) => Measured::Failed(format!("出力が JSON でない({}){}", error, err_line)),
        },
        code => Measured::Failed(format!("終了コード {:?} — {}", code, err_line)),
    }
}

/// 一時の dir(この実行が作った物だけ — 終わりに消す)。
struct Scratch {
    path: PathBuf,
}

impl Scratch {
    /// std::env::temp_dir() の下に固有の名で作る(既に在る名は使わない)。
    fn create() -> Result<Scratch, String> {
        let base = std::env::temp_dir();
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        for attempt in 0..100u32 {
            let path = base.join(format!("doeff-linter-commit-hook-{}-{}-{}", std::process::id(), nanos, attempt));
            match std::fs::create_dir(&path) {
                Ok(()) => {
                    let path = path.canonicalize().unwrap_or(path);
                    return Ok(Scratch { path });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("一時の dir {} を作れない: {}", path.display(), error)),
            }
        }
        Err("一時の dir の固有の名を作れない".to_string())
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// root で git を撃ち、成功すれば stdout を返す。
fn git(root: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git").args(args).current_dir(root).stdin(Stdio::null()).output().map_err(|e| format!("git を起こせない: {}", e))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(format!("git {} が失敗した: {}", args.join(" "), String::from_utf8_lossy(&output.stderr).trim()))
    }
}

/// 今の dir の git の作業木の根。
pub fn git_toplevel(cwd: &Path) -> Result<PathBuf, String> {
    let out = git(cwd, &["rev-parse", "--show-toplevel"])?;
    Ok(PathBuf::from(String::from_utf8_lossy(&out).trim()))
}

/// stage した path(`git diff --cached --name-only --diff-filter=ACMRD -z`)。
fn staged_paths(root: &Path) -> Result<Vec<String>, String> {
    let out = git(root, &["diff", "--cached", "--name-only", "--diff-filter=ACMRD", "-z"])?;
    Ok(out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect())
}

/// HEAD の木を dest へ 1 度だけ書き出す(`git archive --format=tar HEAD | tar -x -C dest`)。HEAD が無い(最初の commit)なら false。
fn export_head(root: &Path, dest: &Path) -> Result<bool, String> {
    if git(root, &["rev-parse", "--verify", "-q", "HEAD"]).is_err() {
        return Ok(false);
    }
    std::fs::create_dir(dest).map_err(|e| format!("{} を作れない: {}", dest.display(), e))?;
    let mut archive = Command::new("git")
        .args(["archive", "--format=tar", "HEAD"])
        .current_dir(root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .spawn()
        .map_err(|e| format!("git archive を起こせない: {}", e))?;
    let pipe = archive.stdout.take().ok_or("git archive の出力を読めない")?;
    let extracted = Command::new("tar").arg("-x").arg("-C").arg(dest).stdin(pipe).status();
    let archived = archive.wait().map_err(|e| format!("git archive を待てない: {}", e))?;
    let extracted = extracted.map_err(|e| format!("tar を起こせない: {}", e))?;
    if !archived.success() || !extracted.success() {
        return Err(format!("HEAD の木を書き出せない(git archive {}・tar {})", archived, extracted));
    }
    Ok(true)
}

/// 子へ渡す設定の引数(先端 = 設定 file そのもの・HEAD の木 = 木の中の同じ相対の file、無ければ先端の file)。
fn config_args(options: &CommitHookOptions, tree: Option<&Path>) -> Vec<String> {
    let Some(config) = &options.config else { return Vec::new() };
    let chosen = match (tree, config.strip_prefix(&options.root)) {
        (Some(tree), Ok(rel)) if tree.join(rel).is_file() => tree.join(rel),
        _ => config.clone(),
    };
    vec!["--config".to_string(), chosen.to_string_lossy().into_owned()]
}

/// 子を撃つ引数を組む(設定・規則・続きの引数・path)。
fn lint_args(options: &CommitHookOptions, tree: Option<&Path>, rules: &[String], extra: &[String], paths: &[String]) -> Vec<String> {
    let mut args = config_args(options, tree);
    args.push("--enable".to_string());
    args.push(rules.join(","));
    args.extend(extra.iter().cloned());
    args.push("--".to_string());
    args.extend(paths.iter().cloned());
    args
}

/// 止める物の一覧(種類ごと)。
#[derive(Debug, Default)]
struct Blocking {
    staged: Vec<String>,
    fresh_critical: Vec<String>,
    whole: Vec<String>,
}

/// 途中で測れなかった理由(上限越えは止めない・失敗は終了コード 2)。
enum Stop {
    TimedOut,
    Failed(String),
}

fn measured(result: Measured, what: &str) -> Result<Value, Stop> {
    match result {
        Measured::Report(value) => Ok(value),
        Measured::TimedOut => Err(Stop::TimedOut),
        Measured::Failed(reason) => Err(Stop::Failed(format!("{}を doeff-linter で測れなかった: {}", what, reason))),
    }
}

fn empty_report() -> Value {
    serde_json::json!({ "violations": [] })
}

/// hook の本体。終了コード 0 = 通す(測れなかった時を含む)・1 = 止める・2 = git・linter が動かなかった。
pub fn run(options: &CommitHookOptions) -> u8 {
    match judge(options) {
        Ok(blocking) => {
            for line in &blocking.staged {
                eprintln!("{}stage した file の破れ: {}", PREFIX, line);
            }
            for ident in &blocking.fresh_critical {
                eprintln!("{}HEAD に無い critical: {}", PREFIX, ident);
            }
            for ident in &blocking.whole {
                eprintln!("{}repo 全体の規則の HEAD に無い当たり: {}", PREFIX, ident);
            }
            u8::from(!(blocking.staged.is_empty() && blocking.fresh_critical.is_empty() && blocking.whole.is_empty()))
        }
        Err(Stop::TimedOut) => {
            eprintln!("{}doeff-linter が {} 秒で終わらなかった — 測れなかったまま通す", PREFIX, options.timeout.as_secs());
            0
        }
        Err(Stop::Failed(reason)) => {
            eprintln!("{}{}", PREFIX, reason);
            2
        }
    }
}

fn judge(options: &CommitHookOptions) -> Result<Blocking, Stop> {
    let root = &options.root;
    let staged = staged_paths(root).map_err(Stop::Failed)?;
    let mut blocking = Blocking::default();
    if staged.is_empty() {
        return Ok(blocking);
    }
    let RuleSplit { quick, whole } = &options.rules;
    let paths = if quick.is_empty() { Vec::new() } else { linted_paths(&staged, |p| root.join(p).exists()) };
    if paths.is_empty() && whole.is_empty() {
        return Ok(blocking);
    }
    let scratch = Scratch::create().map_err(Stop::Failed)?;
    let tree_dir = scratch.path.join("tree");
    let tree = export_head(root, &tree_dir).map_err(Stop::Failed)?.then_some(tree_dir.as_path());
    let tip_root = root.canonicalize().unwrap_or_else(|_| root.clone());

    if !paths.is_empty() {
        // HEAD の版(HEAD に在る path だけ)を同じ規則で撃ち、先端の根の path へ付け替えて基点にする。
        let head_paths: Vec<String> = tree.map(|t| paths.iter().filter(|p| t.join(p).is_file()).cloned().collect()).unwrap_or_default();
        let base = match tree {
            Some(t) if !head_paths.is_empty() => {
                let mut report = measured(run_linter(&options.linter, t, &lint_args(options, Some(t), quick, &[], &head_paths), options.timeout), "HEAD の版")?;
                rebase_paths(&mut report, &t.to_string_lossy(), &tip_root.to_string_lossy());
                report
            }
            _ => empty_report(),
        };
        let baseline = scratch.path.join("baseline.json");
        std::fs::write(&baseline, base.to_string()).map_err(|e| Stop::Failed(format!("{} を書けない: {}", baseline.display(), e)))?;
        let extra = vec!["--baseline-report".to_string(), baseline.to_string_lossy().into_owned()];
        let tip = measured(run_linter(&options.linter, root, &lint_args(options, None, quick, &extra, &paths), options.timeout), "stage した file ")?;
        blocking.staged = blocking_violations(&tip).into_iter().map(|v| violation_line(&tip, v)).collect();
        blocking.fresh_critical = tip["new_critical"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect();
    }

    if !whole.is_empty() {
        let dot = vec![".".to_string()];
        let head = match tree {
            Some(t) => {
                let mut report = measured(run_linter(&options.linter, t, &lint_args(options, Some(t), whole, &[], &dot), options.timeout), "HEAD の木の repo 全体の比べ")?;
                rebase_paths(&mut report, &t.to_string_lossy(), &tip_root.to_string_lossy());
                report
            }
            None => empty_report(),
        };
        let tip = measured(run_linter(&options.linter, root, &lint_args(options, None, whole, &[], &dot), options.timeout), "repo 全体の比べ")?;
        blocking.whole = fresh_whole_repo_hits(&head, &tip);
    }
    Ok(blocking)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ids(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn commit_hook_split_rules_drops_semantic_and_separates_whole_repo_rules() {
        let enabled = ids(&["DOEFF016", "DOEFF163", "DOEFF201", "DOEFF110", "DOEFF205", "doeff166"]);
        let split = split_rules(&enabled, &ids(&["DOEFF163", "DOEFF166", "DOEFF999"]));
        assert_eq!(split.quick, ids(&["DOEFF016", "DOEFF110"]));
        // repo 全体の比べは whole の列だけ — 列の DOEFF166 が行の名指す規則を同じ実行で当てる(agora-redesign #1999・#2033)。
        assert_eq!(split.whole, ids(&["DOEFF163", "doeff166"]));
        // 宣言が無ければ全部 quick(意味の規則は除いたまま)。
        let split = split_rules(&enabled, &[]);
        assert_eq!(split.quick, ids(&["DOEFF016", "DOEFF163", "DOEFF110", "doeff166"]));
        assert!(split.whole.is_empty());
    }

    #[test]
    fn commit_hook_linted_paths_keep_existing_source_files() {
        let staged = ids(&["a.hy", "b.py", "gone.hy", "registry/keys.txt", "c.hyk", "d.hyp"]);
        assert_eq!(linted_paths(&staged, |p| p != "gone.hy"), ids(&["a.hy", "b.py", "c.hyk", "d.hyp"]));
    }

    fn v(key: &str, severity: &str, level: &str) -> Value {
        json!({ "key": key, "severity": severity, "level": level, "path": "/r/a.hy", "rule": "DOEFF1", "message": "m",
                "range": { "start": { "line": 2, "character": 0 } } })
    }

    #[test]
    fn commit_hook_blocking_ignores_warnings_and_criticals() {
        let report = json!({ "root": "/r", "violations": [
            v("k-error", "error", "major"),
            v("k-warning", "warning", "major"),
            v("k-critical", "error", "critical"),
            v("k-info", "info", "info"),
        ]});
        let picked: Vec<&str> = blocking_violations(&report).iter().map(|v| v["key"].as_str().unwrap()).collect();
        assert_eq!(picked, vec!["k-error"]);
        assert_eq!(violation_line(&report, blocking_violations(&report)[0]), "a.hy:3: DOEFF1 m");
    }

    #[test]
    fn commit_hook_whole_repo_diff_returns_only_new_keys() {
        let head = json!({ "root": "/h", "violations": [v("old-critical", "warning", "critical"), v("old-error", "error", "major")] });
        let tip = json!({ "root": "/r", "violations": [
            v("old-critical", "warning", "critical"),
            v("old-error", "error", "major"),
            v("new-critical", "warning", "critical"),
            v("new-error", "error", "major"),
            v("new-warning", "warning", "major"),
        ]});
        assert_eq!(fresh_whole_repo_hits(&head, &tip), ids(&["new-critical", "new-error"]));
        // 鍵の無い違反は根からの相対 path で比べる(先端と HEAD の木で根が違っても同じ識別子)。
        let keyless = |root: &str| json!({ "root": root, "violations": [
            { "severity": "error", "level": "major", "path": format!("{}/x.py", root), "rule": "DOEFF016", "message": "m" }
        ]});
        assert!(fresh_whole_repo_hits(&keyless("/h"), &keyless("/r")).is_empty());
        assert_eq!(fresh_whole_repo_hits(&empty_report(), &keyless("/r")), ids(&["x.py::DOEFF016::m"]));
    }

    #[test]
    fn commit_hook_rebase_paths_moves_only_paths_under_the_tree() {
        let mut report = json!({ "root": "/tmp/t/tree", "violations": [
            { "path": "/tmp/t/tree/a.hy" }, { "path": "/tmp/t/treeish/b.hy" }, { "path": "/elsewhere/c.hy" }
        ]});
        rebase_paths(&mut report, "/tmp/t/tree", "/repo");
        assert_eq!(report["root"], "/repo");
        assert_eq!(report["violations"][0]["path"], "/repo/a.hy");
        assert_eq!(report["violations"][1]["path"], "/tmp/t/treeish/b.hy");
        assert_eq!(report["violations"][2]["path"], "/elsewhere/c.hy");
    }

    #[test]
    fn commit_hook_timeout_prefers_cli_then_config_then_default() {
        let section = CommitHookSection { whole_repo_rules: Vec::new(), timeout_s: Some(7) };
        assert_eq!(resolve_timeout(Some(&section), Some(0)), Duration::from_secs(0));
        assert_eq!(resolve_timeout(Some(&section), None), Duration::from_secs(7));
        assert_eq!(resolve_timeout(None, None), Duration::from_secs(DEFAULT_TIMEOUT_S));
    }
}
