//! commit の hook の入口(`doeff-linter --commit-hook`・agora-redesign #1989)。
//!
//! 各 repo(agora-controllers・merge-queue ほか)が hook の論理を写して持たず、この 1 か所を呼ぶ。repo が書くのは設定の
//! `[tool.doeff-linter.commit_hook]`(上限の秒)だけ。元の実装 = agora-controllers の
//! `scripts/commit_hook.hy`(lint-staged・whole-repo-fresh)。
//!
//! 止める物は 4 種:
//! - stage した source(.hy・.hyk・.hyp・.py)の、critical でない規則の登録簿の外の error(登録簿の既知は linter が warning に下げる)。
//! - stage した source の critical のうち HEAD の版に無い物(`--baseline-report` の `new_critical`)。
//! - stage した source の major の warning の数が、規則ごとに HEAD の版より増えた物(agora-redesign #2683 — warning は終了コードを
//!   変えないので、直している間に新しい warning が入っても減らなかった)。数は stage した file の組の合計で比べる(移した・消した
//!   file の HEAD の版も数える)— file ごとに比べると、warning を増やさない移しや分割まで止まる。基点は HEAD の版そのものなので、
//!   減らした commit の後は次の commit の基点が自ずと下がる(基点の file を持たない・下げ忘れが無い)。
//! - repo 全体の比べ(repo 全体が要ると規則が名乗る列 — `ProjectRule::needs_whole_repo`・agora-redesign #2090 — を repo 全体に当てる)の
//!   当たりのうち HEAD の木に無い物 — 当たりが変更の外の file(architecture.hy・登録簿の表・別の source)に付きうる規則は、stage した
//!   path に当てても出ない。source を 1 つも stage しない commit(表だけの変更)にも当てる。この列は stage した path に当てる規則から
//!   外す(以前は設定の手の一覧 whole_repo_rules — 足し忘れた DOEFF149・161 がどちらにも掛からなかった)。列に DOEFF166(当たらない登録簿の行)が在れば、linter が
//!   行の名指す規則を enable に無くても同じ実行で当てるので、列だけで当たらない行を見逃さない(agora-redesign #1999・#2033 — それまでは
//!   全部の規則で撃っていた・#1998)。
//!
//! 既知の一覧を持たない規則(DOEFF206〜209)の critical は、上の 2 つ目と 4 つ目の列で、stage した path に在れば HEAD に同じ鍵が在っても
//! 止める(agora-redesign #3834)。その当たりは「HEAD に無い」でなく「変えた file の既存の当たり(main にも在る…)」と書き、直し方の
//! 案内を 1 行足す(split_by_head — 止める判断は変えず文だけを分ける)。
//!
//! 子の linter は 1 回ごとに上限(既定 20 秒・0 は上限なし)を持ち、越えたら commit を止める(終了コード 1・agora-redesign #3834・
//! 上限は子の CPU 秒で測る — card ki-79532edd43dd: 混んだ機体では wall の秒だけが伸び、上限なしなら 0 件の commit が毎回切れていた。
//! CPU を使わずに終わらない子には wall の予備の上限〔上限 × WALL_BACKSTOP_FACTOR〕を残し、子の CPU 秒を読めない機体〔/proc の無い
//! Mac〕では wall の秒で測る — 判断は cut_measure の 1 か所・
//! 元の issue #2723)。以前は越えたら通していて、何を確かめずに通したかを 1 行で書くだけだった — 作業役は通った commit を
//! 確かめ直さず、宣言の file を変えた commit の DOEFF167 の当たりが main に入った。止める時は、どの比べの、どの木で、どの規則を、
//! 何秒の上限で打ち切ったかを 1 行で書き、上限なしで同じ比べをやり直す命令(unbounded_command — そのまま貼って実行できる形)と、
//! その結果が 0 件である事を確かめてから commit し直す(--no-verify は使わない)案内を続けて出す。repo 全体の比べが打ち切られても、
//! stage した file の当たりは捨てずに並べる。
//! repo 全体の比べの HEAD の木の結果は置き場(head_report_cache — HEAD の commit・子の linter の版・規則の組・木の中の設定が鍵)に残し、
//! 同じ鍵の次の hook は先端の木だけを測る(上限の秒は上げない)。
//! HEAD の木そのものは事実の cache の根の `commit-hook-tree/<repo を表す名>/tree` に repo ごとに 1 つ置き、HEAD が進んだら git の差分で変わった
//! file だけを書き換える(head_tree — 根の path と変わらない file の更新時刻が保たれるので、HEAD の木を測る 1 回にも事実の cache が効く・
//! agora-redesign #3858)。
//! 純粋な部分(規則の分け・識別子の差・止める当たりの選び・測れなかった比べの文・上限なしの命令)は関数に分けて、単体のテストで確かめる。

use crate::config::{CommitHookSection, Config};
use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 子の linter 1 回ごとの既定の上限(秒)。
pub const DEFAULT_TIMEOUT_S: u64 = 20;
/// 上限なしを表す秒の値(引数 --commit-hook-timeout-s・設定の timeout_s)。
pub const UNBOUNDED_TIMEOUT_S: u64 = 0;
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

/// 純粋: 有効な規則から Jev に問う意味の規則(規則の名乗り config::is_semantic — commit では問わない・cache を読むだけでも遅い)を除き、規則が repo 全体が要ると名乗る物(config::needs_whole_repo)を whole、残りを
/// quick に分ける(順は保つ)。以前は設定の手の一覧 whole_repo_rules で分けていて、一覧に足し忘れた規則(DOEFF149・161)の当たりが
/// どちらの段にも掛からず main に入った(agora-redesign #2090)。意味の規則も以前は ID の頭 DOEFF2 で除いていて、頭が同じ決定的な規則
/// (DOEFF206〜209)がどちらにも入らなかった(agora-redesign #3834)。
pub fn split_rules(enabled: &[String]) -> RuleSplit {
    let (whole, quick): (Vec<String>, Vec<String>) = enabled
        .iter()
        .filter(|r| !crate::config::is_semantic(r))
        .cloned()
        .partition(|r| crate::config::needs_whole_repo(r));
    RuleSplit { quick, whole }
}

/// 純粋: 退役した設定の鍵 whole_repo_rules が残っている時の知らせ(読まない — 規則の名乗りで分ける・agora-redesign #2090)。
pub fn retired_whole_repo_rules_note(section: Option<&CommitHookSection>) -> Option<String> {
    section.filter(|s| !s.whole_repo_rules.is_empty()).map(|_| {
        "設定の whole_repo_rules は退役した鍵で読まない — repo 全体の比べの規則は linter の規則が名乗る(`doeff-linter --list-rules`)。鍵を消す".to_string()
    })
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

/// 純粋: 報告の major の warning(severity が warning・level が major — critical は new_critical、登録簿の外の error は
/// blocking_violations が受け持つ)を規則ごとに数える。
fn major_warning_counts(report: &Value) -> std::collections::BTreeMap<String, usize> {
    let mut counts = std::collections::BTreeMap::new();
    for v in violations(report).filter(|v| v["severity"] == "warning" && v["level"] == "major") {
        *counts.entry(v["rule"].as_str().unwrap_or("").to_string()).or_insert(0) += 1;
    }
    counts
}

/// 純粋: stage した file の組で、major の warning の数が HEAD の版(head)より先端(tip)で増えた規則を 1 行ずつ
/// (`<規則> <HEAD の数> → <先端の数>`・規則の辞書順)。同じ数・減った規則は出さない。
pub fn grown_major_warnings(head: &Value, tip: &Value) -> Vec<String> {
    let before = major_warning_counts(head);
    major_warning_counts(tip)
        .into_iter()
        .filter_map(|(rule, n)| {
            let was = before.get(&rule).copied().unwrap_or(0);
            (n > was).then(|| format!("{} {} → {}", rule, was, n))
        })
        .collect()
}

/// 止める当たりの識別子を、HEAD の版に同じ識別子が無い物(新しく増えた当たり)と在る物(既知の一覧を持たない規則の、変えた file の
/// 既存の当たり)に分けた組(どちらも辞書順)。止める・止めないは変えず、出す文だけを分ける(agora-redesign #3834・cisco-c8 の頼み
/// 2026-10-07 — 既存の当たりにも「HEAD に無い」と書いていて、作業役が hook の不具合と読んで hook を飛ばしかけた)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ByHead {
    pub not_on_head: Vec<String>,
    pub already_on_head: Vec<String>,
}

impl ByHead {
    pub fn is_empty(&self) -> bool {
        self.not_on_head.is_empty() && self.already_on_head.is_empty()
    }
}

/// 純粋: 止める当たりの識別子(blocking)を、HEAD の版の識別子の集合(head)に在るかで分ける — 分ける判定の 1 か所(stage した path に
/// 当てる列の new_critical と、repo 全体の比べの両方が使う)。
pub fn split_by_head(head: &BTreeSet<String>, blocking: impl IntoIterator<Item = String>) -> ByHead {
    let all: BTreeSet<String> = blocking.into_iter().collect();
    let (already_on_head, not_on_head) = all.into_iter().partition(|id| head.contains(id));
    ByHead { not_on_head, already_on_head }
}

/// 純粋: repo 全体の規則の当たりのうち止める物の識別子 — 先端(tip)に在って HEAD(head)に無い物と、stage した path(`staged`
/// — repo の根からの相対)に在る、基点の差で下げずに止める当たり(baseline::blocks_regardless_of_baseline — 既知の一覧を持たない
/// DOEFF206〜209 の critical)。後者は HEAD に同じ鍵が在っても止める(細かさは file 単位・agora-redesign #3834)ので、HEAD に在るかで
/// 分けて返す(split_by_head)。
pub fn fresh_whole_repo_hits(head: &Value, tip: &Value, staged: &[String]) -> ByHead {
    let before = report_idents(head);
    let unlisted = violations(tip)
        .filter(|v| {
            let level = v["level"].as_str().and_then(crate::project::rule::RuleLevel::parse);
            level.is_some_and(|level| crate::baseline::blocks_regardless_of_baseline(v["rule"].as_str().unwrap_or(""), level))
                && staged.iter().any(|p| p == relative_to_root(tip, v["path"].as_str().unwrap_or("")))
        })
        .map(|v| violation_identity(tip, v));
    split_by_head(&before, report_idents(tip).into_iter().filter(|id| !before.contains(id)).chain(unlisted))
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

/// 純粋: 設定と引数から子の linter 1 回ごとの上限を選ぶ(引数が勝つ・無ければ設定・無ければ既定)。0 秒は上限なし(None)。
pub fn resolve_timeout(section: Option<&CommitHookSection>, cli: Option<u64>) -> Option<Duration> {
    match cli.or_else(|| section.and_then(|s| s.timeout_s)).unwrap_or(DEFAULT_TIMEOUT_S) {
        UNBOUNDED_TIMEOUT_S => None,
        secs => Some(Duration::from_secs(secs)),
    }
}

/// 純粋: 設定の enable・disable と引数を当てた有効な規則の列(ふだんの実行と同じ merge_config・None は全部の規則)。
pub fn effective_rules(config: Option<&Config>, cli_enable: &[String], cli_disable: &[String]) -> Vec<String> {
    crate::config::merge_config(config, cli_enable, cli_disable, &[]).0.unwrap_or_else(crate::config::get_all_rule_ids)
}

/// 宣言の file と dir(repo の根からの path)— 設定 file・architecture.hy・登録簿の表。ここを変えた commit は、file 1 つで判じる規則の
/// 当たりも変更の外の file に付けうる(層に禁じた module を足すと、触っていない core の file が赤になる — agora-redesign #2127)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Declarations {
    pub files: Vec<String>,
    pub dirs: Vec<String>,
    /// 規則の入り切りと層を決める宣言(設定 file と architecture.hy)— 基点を今の宣言で測る 1 回限りの指定(Lint-Baseline)で基点の木へ
    /// 写す物(登録簿は写さない — 登録簿の変化は今までどおり比べる)。
    pub switches: Vec<String>,
}

/// 基点を今の宣言で測る 1 回限りの指定の commit 本文の行(trailer)— `Lint-Baseline: declarations <理由>`。規則を鳴らし始める commit
/// (enable に足す・層の宣言を足す)は、既存の当たりが全部「基点に無い当たり」になり、その commit 自身が門と hook を通れない。この行の
/// 在る変更だけ、基点の木を今の宣言(switches)で測り、既存の当たりを基点に在る物として新しい当たりだけを止める(既知の一覧に載せる
/// 形ではない — agora-redesign #2143・cisco-c8 の決め 2026-10-01)。宣言だけで触っていない file に当たりを付ける変更(#2127)を見逃す
/// 形なので、理由の無い行は効かない(誰が何のために使ったかを本文に残す)。
pub const BASELINE_TRAILER: &str = "Lint-Baseline: declarations";

/// 純粋: commit 本文に理由つきの Lint-Baseline の行が在れば、基点の木へ写す宣言の file の列(無ければ空)。
pub fn baseline_overlay(message: &str, declarations: &Declarations) -> Vec<String> {
    let requested = message.lines().any(|line| {
        line.trim_start().strip_prefix(BASELINE_TRAILER).is_some_and(|reason| !reason.trim_matches(|c: char| c.is_whitespace() || c == '—' || c == '-' || c == ':').is_empty())
    });
    if requested { declarations.switches.clone() } else { Vec::new() }
}

/// 設定から宣言の file と dir を集める。architecture.hy の置き場は linter のふだんの実行と同じ決め方(設定の architecture か、根の
/// architecture.hy)。登録簿の dir・file は根から、設定の登録簿の file は設定 file の dir から。
pub fn declarations_of(root: &Path, config: Option<(&Config, &Path)>) -> Declarations {
    let rel = |path: &Path| crate::project::paths::declared_rel(root, path);
    let config_dir = config.and_then(|(_, path)| path.parent().map(Path::to_path_buf)).unwrap_or_else(|| root.to_path_buf());
    let architecture = match config.and_then(|(c, _)| c.architecture.clone()) {
        Some(path) => config_dir.join(path),
        None => root.join("architecture.hy"),
    };
    let registry = config.and_then(|(c, _)| c.registry.as_ref());
    let files = config
        .map(|(_, path)| rel(path))
        .into_iter()
        .chain(std::iter::once(rel(&architecture)))
        .chain(registry.into_iter().flat_map(|r| r.files.iter().cloned()))
        .chain(registry.into_iter().flat_map(|r| r.config_files.iter().map(|f| rel(&config_dir.join(f)))))
        .collect();
    let dirs = registry.map(|r| r.dirs.iter().map(|d| d.trim_end_matches('/').to_string()).collect()).unwrap_or_default();
    let switches = config.map(|(_, path)| rel(path)).into_iter().chain(std::iter::once(rel(&architecture))).collect();
    Declarations { files, dirs, switches }
}

/// 純粋: 変えた path(根から)に宣言の file か宣言の dir の下の file が在るか。
pub fn touches_declaration(changed: &[String], declarations: &Declarations) -> bool {
    changed.iter().any(|path| {
        declarations.files.iter().any(|f| f == path) || declarations.dirs.iter().any(|d| path.starts_with(&format!("{}/", d)))
    })
}

/// 純粋: 変更に当てる規則の分け — 門と commit の hook が共有する 1 か所(agora-redesign #2127)。ふだんは規則の名乗り(split_rules)で
/// 分け、宣言の file を変えた変更では file 1 つで判じる規則も repo 全体の比べに入れる(quick は空)。手の一覧は持たない。
pub fn split_for_change(enabled: &[String], changed: &[String], declarations: &Declarations) -> RuleSplit {
    let split = split_rules(enabled);
    if !touches_declaration(changed, declarations) {
        return split;
    }
    let whole = enabled.iter().filter(|r| split.quick.contains(r) || split.whole.contains(r)).cloned().collect();
    RuleSplit { quick: Vec::new(), whole }
}

/// hook の 1 回の実行の前提。
#[derive(Debug, Clone)]
pub struct CommitHookOptions {
    /// git の作業木の根(子の linter の cwd・stage の path の基準)。
    pub root: PathBuf,
    /// 設定 file(絶対 path・無ければ子は設定を探す)。
    pub config: Option<PathBuf>,
    /// 有効な規則(分けは stage した path を見て split_for_change が決める)。
    pub enabled: Vec<String>,
    pub declarations: Declarations,
    /// 基点(HEAD の木)へ写す今の宣言の file(commit 本文の Lint-Baseline の行が在る時だけ — 空なら写さない)。
    pub overlay: Vec<String>,
    /// commit 本文の file(引数 --commit-message — 上限なしの命令にも同じ file を渡す)。
    pub message: Option<PathBuf>,
    /// 引数の --enable・--disable(上限なしの命令に同じ物を渡す)。
    pub cli_enable: Vec<String>,
    pub cli_disable: Vec<String>,
    /// 子の linter 1 回ごとの上限(None = 上限なし)。
    pub timeout: Option<Duration>,
    /// 子として撃つ linter(ふつうは今の binary)。
    pub linter: PathBuf,
    /// HEAD の木の結果の置き場の根(facts_cache の根 — None なら置き場を使わずに毎回 HEAD の木を測る)。
    pub cache: Option<PathBuf>,
}

impl CommitHookOptions {
    /// 設定と引数から前提を組む。
    pub fn new(root: PathBuf, config: Option<(&Config, PathBuf)>, cli_enable: &[String], cli_disable: &[String], cli_timeout: Option<u64>, linter: PathBuf) -> Self {
        let section = config.as_ref().and_then(|(c, _)| c.commit_hook.as_ref());
        let enabled = effective_rules(config.as_ref().map(|(c, _)| *c), cli_enable, cli_disable);
        if let Some(note) = retired_whole_repo_rules_note(section) {
            eprintln!("{}{}", PREFIX, note);
        }
        CommitHookOptions {
            declarations: declarations_of(&root, config.as_ref().map(|(c, path)| (*c, path.as_path()))),
            enabled,
            overlay: Vec::new(),
            message: None,
            cli_enable: cli_enable.to_vec(),
            cli_disable: cli_disable.to_vec(),
            timeout: resolve_timeout(section, cli_timeout),
            config: config.map(|(_, path)| path),
            root,
            linter,
            cache: crate::project::facts_cache::cache_base(),
        }
    }
}

/// 子の linter を撃つ木。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    /// HEAD の木(事実の cache の根の commit-hook-tree/<repo を表す名>/tree に置いた基点 — head_tree)。
    Head,
    /// 先端の木(git の作業木そのもの)。
    Tip,
}

impl Side {
    fn name(self) -> &'static str {
        match self {
            Side::Head => "HEAD の木",
            Side::Tip => "先端の木(作業木)",
        }
    }
}

/// 子の linter 1 回の頼み — どの木で、どの規則を当てるかと、子へ渡す引数。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LintRun {
    pub side: Side,
    pub rules: Vec<String>,
    pub args: Vec<String>,
}

/// 打ち切りを判じた物差し(card ki-79532edd43dd — 上限は子の CPU 秒で測る)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Measure {
    /// 子の CPU 秒(子とその待ち終えた子孫の utime・stime)が上限を越えた。
    Cpu,
    /// 子の CPU 秒を読めない機体(/proc の無い Mac)で、wall の秒が上限を越えた。
    Wall,
    /// CPU 秒は上限の内だが、wall の秒が予備の上限(上限 × WALL_BACKSTOP_FACTOR)を越えた — CPU を使わずに終わらない子。
    WallBackstop,
}

/// CPU を使わずに終わらない子を打ち切る wall の予備の上限の倍率(CPU 秒の上限の何倍か)。混んだ機体で 1 core の取り分が 1/4 を
/// 割るまで、CPU 秒の上限の内の子を wall で切らない(zeus は 36 core・load 73 で取り分 約 1/2 — card ki-79532edd43dd)。
pub const WALL_BACKSTOP_FACTOR: u32 = 4;

/// 純粋: 子の linter 1 回を打ち切るかの判断(card ki-79532edd43dd)— wall = 起こしてからの wall の秒・cpu = 子の CPU 秒(読めない機体は
/// None)・limit = 上限。CPU 秒が読めれば CPU 秒で測り、wall は予備の上限(limit × WALL_BACKSTOP_FACTOR)だけを見る。読めなければ wall で測る。
pub fn cut_measure(wall: Duration, cpu: Option<Duration>, limit: Duration) -> Option<Measure> {
    match cpu {
        Some(cpu) if cpu >= limit => Some(Measure::Cpu),
        Some(_) if wall >= limit * WALL_BACKSTOP_FACTOR => Some(Measure::WallBackstop),
        Some(_) => None,
        None if wall >= limit => Some(Measure::Wall),
        None => None,
    }
}

/// 純粋: /proc/<pid>/stat の本文から CPU 秒を読む — utime・stime・cutime・cstime(全体の 14〜17 番目の欄・単位は USER_HZ = 100 分の
/// 1 秒 — kernel の ABI で機体に依らない)の和。名の欄(2 番目)は ')' を含みうるので、最後の ')' の後ろから数える。
pub fn cpu_of_stat(text: &str) -> Option<Duration> {
    let rest = &text[text.rfind(')')? + 1..];
    let fields: Vec<&str> = rest.split_whitespace().collect();
    // ')' の後ろの 1 つ目が state(全体の 3 番目)— 全体の 14〜17 番目はここの 11〜14 番目(0 から数えて)。
    let ticks = fields.get(11..15)?.iter().map(|field| field.parse::<u64>().ok()).sum::<Option<u64>>()?;
    Some(Duration::from_millis(ticks * 10))
}

/// 子の process の CPU 秒(/proc の在る Linux だけ — 他の機体と読めない時は None で、打ち切りは wall で測る)。
#[cfg(target_os = "linux")]
fn child_cpu(pid: u32) -> Option<Duration> {
    std::fs::read_to_string(format!("/proc/{}/stat", pid)).ok().as_deref().and_then(cpu_of_stat)
}

#[cfg(not(target_os = "linux"))]
fn child_cpu(_pid: u32) -> Option<Duration> {
    None
}

/// 上限で打ち切った子の linter 1 回 — どの木で、どの規則を、何秒の上限で、どの物差しで。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cut {
    pub side: Side,
    pub rules: Vec<String>,
    pub limit: Duration,
    pub measure: Measure,
}

/// 測れなかった比べ — 比べの名(「HEAD の木の repo 全体の比べ」など)と、打ち切った 1 回。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unmeasured {
    pub what: String,
    pub cut: Cut,
}

/// 純粋: 測れなかった比べを 1 行で書く(何を確かめずに commit を止めたか — agora-redesign #2723・#3834)。
pub fn unmeasured_line(unmeasured: &Unmeasured) -> String {
    let Cut { side, rules, limit, measure } = &unmeasured.cut;
    let secs = limit.as_secs();
    let how = match measure {
        Measure::Cpu => format!("CPU 秒の上限 {} 秒で打ち切った", secs),
        Measure::Wall => format!("wall の上限 {} 秒で打ち切った(この機体では子の CPU 秒を読めない)", secs),
        Measure::WallBackstop => format!(
            "wall の予備の上限 {} 秒で打ち切った(CPU 秒の上限 {} 秒の {} 倍 — CPU を使わずに終わらない子)",
            secs * u64::from(WALL_BACKSTOP_FACTOR),
            secs,
            WALL_BACKSTOP_FACTOR
        ),
    };
    format!(
        "{}を測れなかった — {}で当てた規則 {} 個({})の子の linter を{}。この規則の当たりを確かめていないので commit を止める(上限 = 設定の [tool.doeff-linter.commit_hook] timeout_s — 子の CPU 秒・0 は上限なし)",
        unmeasured.what,
        side.name(),
        rules.len(),
        rules.join(","),
        how
    )
}

/// 純粋: shell にそのまま貼れる形の 1 語(安全な字だけならそのまま・他は ' で囲む)。
fn shell_word(word: &str) -> String {
    let plain = !word.is_empty() && word.bytes().all(|b| b.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&b));
    if plain { word.to_string() } else { format!("'{}'", word.replace('\'', "'\\''")) }
}

/// 純粋: 上限なしで同じ hook の判じをやり直す命令(作業役がそのまま貼って実行できる 1 行)— 同じ linter・同じ根・同じ設定・同じ
/// --enable / --disable・同じ commit 本文の file に、上限なし(--commit-hook-timeout-s 0)を足す。stage は同じ index を読む。
pub fn unbounded_command(options: &CommitHookOptions) -> String {
    let path = |p: &Path| p.to_string_lossy().into_owned();
    let mut words = vec![path(&options.linter), "--commit-hook".to_string(), "--root".to_string(), path(&options.root)];
    match &options.config {
        Some(config) => words.extend(["--config".to_string(), path(config)]),
        None => words.push("--no-config".to_string()),
    }
    if !options.cli_enable.is_empty() {
        words.extend(["--enable".to_string(), options.cli_enable.join(",")]);
    }
    if !options.cli_disable.is_empty() {
        words.extend(["--disable".to_string(), options.cli_disable.join(",")]);
    }
    if let Some(message) = &options.message {
        words.extend(["--commit-message".to_string(), path(message)]);
    }
    words.extend(["--commit-hook-timeout-s".to_string(), UNBOUNDED_TIMEOUT_S.to_string()]);
    words.iter().map(|w| shell_word(w)).collect::<Vec<_>>().join(" ")
}

/// 純粋: 打ち切って commit を止めた時の行 — 測れなかった比べの 1 行・上限なしの命令・やり直しの案内。
pub fn cut_lines(options: &CommitHookOptions, unmeasured: &Unmeasured) -> Vec<String> {
    vec![
        unmeasured_line(unmeasured),
        format!("上限なしで同じ比べをやり直す命令: {}", unbounded_command(options)),
        "この命令の終了コードが 0(止める当たりが 0 件)である事を確かめてから commit し直す。--no-verify は使わない".to_string(),
    ]
}

/// 子の linter 1 回の答え。
#[derive(Debug)]
pub enum Measured {
    Report(Value),
    /// 上限を越えた(commit を止める — 打ち切った 1 回を書く)。
    TimedOut(Cut),
    /// 動かなかった・出力が JSON でない(理由)。
    Failed(String),
}

/// 子の linter を editor-json で実行し、上限の中で終われば出力を読む(timeout が None なら終わるまで待つ・上限は子の CPU 秒で測る —
/// cut_measure)。終了コード 0・1・3・4 は測れた(中身で判じる)とし、他は理由つきの失敗。
pub fn run_linter(linter: &Path, cwd: &Path, run: &LintRun, timeout: Option<Duration>) -> Measured {
    let started = Instant::now();
    let spawned = Command::new(linter)
        .args(["--output-format", "editor-json", "--no-log"])
        .args(&run.args)
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
            Ok(None) => {
                if let Some(limit) = timeout {
                    if let Some(measure) = cut_measure(started.elapsed(), child_cpu(child.id()), limit) {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Measured::TimedOut(Cut { side: run.side, rules: run.rules.clone(), limit, measure });
                    }
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Measured::Failed(format!("子の linter を待てない: {}", error)),
        }
    };
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    // 子の stderr は全部の行を理由に運ぶ(設定の誤りは 1 行目が「設定の誤り:」の見出しだけで、名指しは次の行から — 1 行目だけでは
    // どの宣言が誤りかが消える・agora-redesign #2377)。
    let err_line = String::from_utf8_lossy(&err).lines().map(str::trim).filter(|l| !l.is_empty()).collect::<Vec<_>>().join(" / ");
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
        let path = unique_dir(&std::env::temp_dir(), &format!("doeff-linter-commit-hook-{}", std::process::id()))?;
        let path = path.canonicalize().unwrap_or(path);
        Ok(Scratch { path })
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// base の下に `<prefix>-<ns>-<n>` の固有の名の dir を作る(既に在る名は使わない)。
fn unique_dir(base: &Path, prefix: &str) -> Result<PathBuf, String> {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    for attempt in 0..100u32 {
        let path = base.join(format!("{}-{}-{}", prefix, nanos, attempt));
        match std::fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("一時の dir {} を作れない: {}", path.display(), error)),
        }
    }
    Err("一時の dir の固有の名を作れない".to_string())
}

// HEAD の木を置く場所(agora-redesign #3858)。doeff-linter の事実の cache(project::facts_cache)は根の path ごとの dir に、file ごとの
// 大きさと更新時刻を鍵にして置かれる。木を一時 dir や sha ごとの dir に書き出すと、commit のたびに HEAD が進むふだんの使い方では毎回が
// 新しい根 = cache 無しの 1 回目になる(agora-controllers で CPU 約 66 秒)。木は repo ごとに 1 つ決まった path に置き、HEAD が進んだ時は
// git の差分で変わった file だけを書き換える — 変わらない file には触らないので更新時刻が保たれ、事実の cache が当たる。
// 形は agora-controllers の main へのマージ前の検査(scripts/land_lint_gate.hy の repo-base-tree)と揃える:
//   場所 = 事実の cache の根(facts_cache::cache_base — 既定 `$XDG_CACHE_HOME/doeff-linter`、無ければ `~/.cache/doeff-linter`)の
//   `commit-hook-tree/<repo を表す名>/` の下に、木 `tree/`・木の今の sha と重ねた宣言の file の記録 `tree.sha`・lock の file `lock`・
//   repo の git の共有 dir の絶対 path の記録 `repo.path`。
//   木は根(--root)の dir の中身で、根が作業木の根の下の dir なら、名は共有 dir にその位置(`git rev-parse --show-prefix`)を繋いだ path の
//   sha256 の 16 桁(agora-redesign #3934 — 位置の違う根が 1 つの木を取り合わない)。
//   repo を表す名 = repo の git の共有 dir(`git rev-parse --git-common-dir`・正規化した絶対 path)の sha256 の 16 桁。同じ repo の別の
//   作業木は同じ名で、1 つの木を分け合う — 作業木は数百ある(zeus の agora-controllers で 561)ので作業木ごとでは disk が足りず(木 1 つ
//   約 35MB)、新しい作業木の最初の commit も、ほかの作業木が進めた木(本線の近く)から差分だけで進められる。
//   書き換えた file の更新時刻は書いた時刻(tar の -m)にする — commit の時刻(秒の粒度)だと、同じ秒の 2 つの commit で同じ大きさの
//   別の中身の file が同じ鍵になり、前の中身の事実を読む。
//   書き換えの間は記録 `tree.sha` を消しておき、書き終えてから一時 file の rename で置く。記録が無い・読めない・記録の sha から差分を
//   取れない木は、途中で落ちた木として全部を書き出し直す(全部を書くのはこの時と最初の 1 回だけ)。重ねた宣言の file(1 回限りの指定
//   Lint-Baseline)は記録に載せ、次に進める時に木の sha の中身へ戻す。
//   同時の 2 本: 木を使う間(書き換えから子の linter が測り終えるまで)は `lock` の排他の lock を持つ。LOCK_WAIT 待っても取れなければ、
//   この実行の一時 dir に木を書き出して使う(置いた木には触らない — 事実の cache は効かないが、止まらない)。
//   片づけ: 前の形の sha ごとの木(`<sha>`・`<sha>-<16 桁>`・書き出しの残り `.partial-…`)は見つけたら消す。記録 `repo.path` の
//   共有 dir が無くなった repo の dir も消す(lock を取れた物だけ)。消すのはこの名の形の dir だけ(この場所に在るほかの物には触らない)。
//   場所の dir そのものには facts_cache の使った印を付け、事実の cache の根の全体の上限の片づけ(sweep_over_cap)に入れる。

/// HEAD の木を置く場所の dir の名(事実の cache の根の直下)。
pub const TREE_HOME_DIR: &str = "commit-hook-tree";
/// repo の dir の中の木の dir の名。
pub const TREE_DIR: &str = "tree";
/// repo の dir の中の、木の今の sha と重ねた宣言の file の記録。
pub const TREE_RECORD: &str = "tree.sha";
/// repo の dir の中の lock の file。
pub const TREE_LOCK: &str = "lock";
/// repo の dir の中の、repo の git の共有 dir の絶対 path の記録。
pub const REPO_RECORD: &str = "repo.path";
/// 同時の別の実行が木を使っている時に待つ上限。
pub const LOCK_WAIT: Duration = Duration::from_secs(30);
/// 書き換える path がこれより多ければ、差分で進めずに全部を書き出す(git の引数の長さの上限の手前)。
const REWRITE_LIMIT: usize = 4000;
/// git archive に 1 回で渡す path の数。
const ARCHIVE_CHUNK: usize = 500;

/// 木をどう用意したか。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TreeUpdate {
    /// 全部を書き出した(最初の 1 回・記録が無い / 読めない・差分を取れなかった・変わった path が多すぎた)。
    Rebuilt,
    /// 記録の sha から差分で進めた — rewritten = 書いた file の数・removed = 消した path の数。
    Advanced { rewritten: usize, removed: usize },
    /// 記録の sha が HEAD と同じで、戻す・重ねる宣言の file も無い — 何も書いていない。
    Unchanged,
    /// 同時の別の実行が木を使っていた — この実行の一時 dir に書き出した木(置いた木には触らない)。
    Temporary,
}

/// 用意した HEAD の木 — path = 木の根。lock は木を使い終わる(この値を落とす)まで持つ。
#[derive(Debug)]
pub struct HeadTree {
    pub path: PathBuf,
    pub update: TreeUpdate,
    _lock: Option<std::fs::File>,
}

/// 木の記録 — 木の今の sha と、その上に先端から重ねた宣言の file。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TreeRecord {
    pub sha: String,
    pub overlay: Vec<String>,
}

/// 純粋: 16 進の n 桁か。
fn is_hex(text: &str, n: usize) -> bool {
    text.len() == n && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// 純粋: 記録の file の中身を読む — 1 行目が sha(40 か 64 桁の 16 進)、続く行が重ねた宣言の file。形が違えば None(落ちた木)。
pub fn parse_tree_record(text: &str) -> Option<TreeRecord> {
    let mut lines = text.lines();
    let sha = lines.next()?.trim();
    (is_hex(sha, 40) || is_hex(sha, 64)).then(|| TreeRecord { sha: sha.to_string(), overlay: lines.filter(|l| !l.is_empty()).map(str::to_string).collect() })
}

/// 純粋: 記録の file の中身。
pub fn render_tree_record(record: &TreeRecord) -> String {
    std::iter::once(record.sha.as_str()).chain(record.overlay.iter().map(String::as_str)).map(|line| format!("{}\n", line)).collect()
}

/// 純粋: repo を表す名 — git の共有 dir の絶対 path の sha256 の 16 桁(上の註)。
pub fn repo_key(common_dir: &Path) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(common_dir.as_os_str().as_encoded_bytes());
    digest.iter().take(8).map(|b| format!("{:02x}", b)).collect()
}

/// 純粋: 前の形の sha ごとの木の名か(`<40 桁>`・`<40 桁>-<16 桁>`・書き出しの残り `.partial-…`)。
pub fn is_sha_tree_name(name: &str) -> bool {
    name.starts_with(".partial-")
        || match name.split_once('-') {
            None => is_hex(name, 40),
            Some((sha, suffix)) => is_hex(sha, 40) && is_hex(suffix, 16),
        }
}

/// 場所 home の dir の一覧(symlink は除く)。
fn home_dirs(home: &Path) -> Vec<(String, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(home) else { return Vec::new() };
    entries
        .flatten()
        .filter(|entry| std::fs::symlink_metadata(entry.path()).is_ok_and(|meta| meta.is_dir()))
        .map(|entry| (entry.file_name().to_string_lossy().into_owned(), entry.path()))
        .collect()
}

/// 場所 home の前の形の sha ごとの木を消し、消した数を返す(上の註)。
pub fn retire_sha_trees(home: &Path) -> usize {
    home_dirs(home).into_iter().filter(|(name, path)| is_sha_tree_name(name) && std::fs::remove_dir_all(path).is_ok()).count()
}

/// 場所 home の、記録した git の共有 dir が無くなった repo の dir を消し、消した数を返す(上の註)— keep(今の repo)と、記録の無い dir・
/// lock を取れない dir には触らない。
pub fn retire_vanished_repos(home: &Path, keep: &str) -> usize {
    home_dirs(home)
        .into_iter()
        .filter(|(name, path)| {
            name != keep
                && is_hex(name, 16)
                && std::fs::read_to_string(path.join(REPO_RECORD)).is_ok_and(|recorded| !Path::new(recorded.trim()).exists())
                && acquire_lock(&path.join(TREE_LOCK), Duration::ZERO).is_ok_and(|lock| lock.is_some())
                && std::fs::remove_dir_all(path).is_ok()
        })
        .count()
}

/// lock の file を開き、排他の lock を wait まで待って取る。取れなければ None。
fn acquire_lock(path: &Path, wait: Duration) -> Result<Option<std::fs::File>, String> {
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path)
        .map_err(|e| format!("{} を開けない: {}", path.display(), e))?;
    let deadline = Instant::now() + wait;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(Some(file)),
            Err(std::fs::TryLockError::WouldBlock) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            Err(std::fs::TryLockError::WouldBlock) => return Ok(None),
            Err(std::fs::TryLockError::Error(error)) => return Err(format!("{} の lock を取れない: {}", path.display(), error)),
        }
    }
}

/// HEAD の木を repo ごとに 1 つの決まった path(場所 home の `<repo を表す名>/tree`)に用意し、その根を返す(上の註)。木が在れば記録の
/// sha から HEAD まで変わった file だけを書き換え、無い・落ちた木なら全部を書き出す。同時の別の実行が wait まで木を放さなければ、
/// scratch の下に書き出した一時の木を返す。HEAD が無い(最初の commit)なら None。
pub fn head_tree(root: &Path, overlay: &[String], home: &Path, scratch: &Path, wait: Duration) -> Result<Option<HeadTree>, String> {
    let Ok(head) = git(root, &["rev-parse", "--verify", "-q", "HEAD^{commit}"]) else { return Ok(None) };
    let head = String::from_utf8_lossy(&head).trim().to_string();
    let common = git(root, &["rev-parse", "--path-format=absolute", "--git-common-dir"])?;
    let common = PathBuf::from(String::from_utf8_lossy(&common).trim());
    let common = common.canonicalize().unwrap_or(common);
    std::fs::create_dir_all(home).map_err(|e| format!("{} を作れない: {}", home.display(), e))?;
    let home = home.canonicalize().map_err(|e| format!("{} を読めない: {}", home.display(), e))?;
    retire_sha_trees(&home);
    // 木は根の dir の中身なので、同じ repo でも根の位置が違えば別の木(位置を名に入れる — 作業木の根なら今までと同じ名)。
    let prefix = root_prefix(root)?;
    let key = if prefix.is_empty() { repo_key(&common) } else { repo_key(&common.join(prefix.trim_end_matches('/'))) };
    retire_vanished_repos(&home, &key);
    let repo_dir = home.join(&key);
    std::fs::create_dir_all(&repo_dir).map_err(|e| format!("{} を作れない: {}", repo_dir.display(), e))?;
    let Some(lock) = acquire_lock(&repo_dir.join(TREE_LOCK), wait)? else {
        let path = scratch.join("head-tree");
        std::fs::create_dir_all(&path).map_err(|e| format!("{} を作れない: {}", path.display(), e))?;
        write_whole_tree(root, &head, overlay, &path)?;
        return Ok(Some(HeadTree { path, update: TreeUpdate::Temporary, _lock: None }));
    };
    let recorded = common.to_string_lossy();
    if std::fs::read_to_string(repo_dir.join(REPO_RECORD)).ok().as_deref().map(str::trim) != Some(recorded.as_ref()) {
        std::fs::write(repo_dir.join(REPO_RECORD), format!("{}\n", recorded)).map_err(|e| format!("{} を書けない: {}", REPO_RECORD, e))?;
    }
    let tree = repo_dir.join(TREE_DIR);
    let record_path = repo_dir.join(TREE_RECORD);
    let record = std::fs::read_to_string(&record_path).ok().and_then(|text| parse_tree_record(&text)).filter(|_| tree.is_dir());
    let update = match record {
        Some(record) => match advance_tree(root, &record, &head, overlay, &tree, &record_path) {
            Ok(update) => update,
            // 差分を取れない・書き換えの途中で失敗した木は、全部を書き出し直す(記録は消えている)。
            Err(_) => rebuild_tree(root, &head, overlay, &tree, &record_path)?,
        },
        None => rebuild_tree(root, &head, overlay, &tree, &record_path)?,
    };
    Ok(Some(HeadTree { path: tree, update, _lock: Some(lock) }))
}

/// 木を消して HEAD の全部を書き出し直し、記録を置く。
fn rebuild_tree(root: &Path, head: &str, overlay: &[String], tree: &Path, record_path: &Path) -> Result<TreeUpdate, String> {
    remove_record(record_path)?;
    if std::fs::symlink_metadata(tree).is_ok() {
        std::fs::remove_dir_all(tree).map_err(|e| format!("{} を消せない: {}", tree.display(), e))?;
    }
    std::fs::create_dir_all(tree).map_err(|e| format!("{} を作れない: {}", tree.display(), e))?;
    write_whole_tree(root, head, overlay, tree)?;
    write_record(record_path, &TreeRecord { sha: head.to_string(), overlay: overlay.to_vec() })?;
    Ok(TreeUpdate::Rebuilt)
}

/// 在る dir tree へ HEAD の全部を書き出し、宣言の file を先端から重ねる。
fn write_whole_tree(root: &Path, head: &str, overlay: &[String], tree: &Path) -> Result<(), String> {
    extract(root, head, &[], tree)?;
    lay_overlay(root, overlay, tree)
}

/// 記録の sha から HEAD まで変わった path と、前に重ねた・今重ねる宣言の file だけを、HEAD の中身へ書き換える(消えた path は消す)。
/// 書き換えの間は記録を消しておく。変わった path が多すぎれば Err(呼び手が全部を書き出し直す)。
fn advance_tree(root: &Path, record: &TreeRecord, head: &str, overlay: &[String], tree: &Path, record_path: &Path) -> Result<TreeUpdate, String> {
    // 根からの相対で読む(木は根の dir の中身 — git archive は根の dir から撃つと、その dir の中身を根からの相対で書き出す)。
    let changed = git(root, &["diff-tree", "-r", "-z", "--no-renames", "--name-only", "--relative", &record.sha, head])?;
    let refresh: BTreeSet<String> = split_z(&changed).into_iter().chain(record.overlay.iter().cloned()).chain(overlay.iter().cloned()).collect();
    if refresh.is_empty() {
        return Ok(TreeUpdate::Unchanged);
    }
    if refresh.len() > REWRITE_LIMIT {
        return Err(format!("変わった path が {} 個(上限 {})", refresh.len(), REWRITE_LIMIT));
    }
    let refresh: Vec<String> = refresh.into_iter().collect();
    let present: Vec<String> = present_paths(root, head, &refresh)?.into_iter().collect::<BTreeSet<String>>().into_iter().collect();
    remove_record(record_path)?;
    for rel in &refresh {
        remove_path(tree, rel)?;
    }
    if !present.is_empty() {
        extract(root, head, &present, tree)?;
    }
    lay_overlay(root, overlay, tree)?;
    write_record(record_path, &TreeRecord { sha: head.to_string(), overlay: overlay.to_vec() })?;
    Ok(TreeUpdate::Advanced { rewritten: present.len(), removed: refresh.len().saturating_sub(present.len()) })
}

/// NUL 区切りの git の出力を path の列へ。
fn split_z(out: &[u8]) -> Vec<String> {
    out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect()
}

/// paths のうち commit sha に在る物(git ls-tree)。
fn present_paths(root: &Path, sha: &str, paths: &[String]) -> Result<Vec<String>, String> {
    let mut present = Vec::new();
    for chunk in paths.chunks(ARCHIVE_CHUNK) {
        let args: Vec<&str> = ["--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--"].into_iter().chain(chunk.iter().map(String::as_str)).collect();
        present.extend(split_z(&git(root, &args)?));
    }
    Ok(present)
}

/// 木の中の path を消し(dir なら丸ごと)、空になった親の dir を木の根の手前まで消す。
fn remove_path(tree: &Path, rel: &str) -> Result<(), String> {
    let path = tree.join(rel);
    let removed = match std::fs::symlink_metadata(&path) {
        Ok(meta) if meta.is_dir() => std::fs::remove_dir_all(&path),
        Ok(_) => std::fs::remove_file(&path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    };
    removed.map_err(|e| format!("{} を消せない: {}", path.display(), e))?;
    let mut parent = path.parent();
    while let Some(dir) = parent.filter(|dir| *dir != tree && dir.starts_with(tree)) {
        if std::fs::remove_dir(dir).is_err() {
            break;
        }
        parent = dir.parent();
    }
    Ok(())
}

/// commit sha の paths(空なら全部)を在る dir dest へ書き出す(`git archive --format=tar <sha> -- <paths> | tar -x -m -C dest` —
/// file の更新時刻は書いた時刻)。
fn extract(root: &Path, sha: &str, paths: &[String], dest: &Path) -> Result<(), String> {
    let whole: [Vec<String>; 1] = [Vec::new()];
    let chunks: Vec<&[String]> = if paths.is_empty() { whole.iter().map(Vec::as_slice).collect() } else { paths.chunks(ARCHIVE_CHUNK).collect() };
    for chunk in chunks {
        let mut archive = Command::new("git")
            .args(["--literal-pathspecs", "archive", "--format=tar", sha, "--"])
            .args(chunk)
            .current_dir(root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .spawn()
            .map_err(|e| format!("git archive を起こせない: {}", e))?;
        let pipe = archive.stdout.take().ok_or("git archive の出力を読めない")?;
        let extracted = Command::new("tar").arg("-x").arg("-m").arg("-C").arg(dest).stdin(pipe).status();
        let archived = archive.wait().map_err(|e| format!("git archive を待てない: {}", e))?;
        let extracted = extracted.map_err(|e| format!("tar を起こせない: {}", e))?;
        if !archived.success() || !extracted.success() {
            return Err(format!("HEAD の木を書き出せない(git archive {}・tar {})", archived, extracted));
        }
    }
    Ok(())
}

/// 宣言の file を先端から木へ写す(先端に無い file は写さない)。
fn lay_overlay(root: &Path, overlay: &[String], tree: &Path) -> Result<(), String> {
    for rel in overlay {
        let from = root.join(rel);
        if from.is_file() {
            let to = tree.join(rel);
            if let Some(parent) = to.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("{} を作れない: {}", parent.display(), e))?;
            }
            std::fs::copy(&from, &to).map_err(|e| format!("{} を HEAD の木へ写せない: {}", rel, e))?;
        }
    }
    Ok(())
}

/// 記録を消す(無ければ何もしない)。
fn remove_record(path: &Path) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(format!("{} を消せない: {}", path.display(), error)),
        _ => Ok(()),
    }
}

/// 記録を一時 file から rename で置く(書きかけの記録を読ませない)。
fn write_record(path: &Path, record: &TreeRecord) -> Result<(), String> {
    let partial = path.with_extension(format!("partial-{}", std::process::id()));
    std::fs::write(&partial, render_tree_record(record)).map_err(|e| format!("{} を書けない: {}", partial.display(), e))?;
    std::fs::rename(&partial, path).map_err(|e| format!("{} を置けない: {}", path.display(), e))
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

/// 根 root の、git の作業木の根からの位置(`git rev-parse --show-prefix` — 根が作業木の根なら空・下の dir なら `pkg/` の形)。
/// 根が作業木の根の下の dir の時(dotfiles の agentcli/ に設定を置いた形 — agora-redesign #3934)、git が答える path の根と木の置き場を
/// 根に合わせるために読む 1 か所。
fn root_prefix(root: &Path) -> Result<String, String> {
    let out = git(root, &["rev-parse", "--show-prefix"])?;
    Ok(String::from_utf8_lossy(&out).trim().to_string())
}

/// stage した path(`git diff --cached --name-only --relative --diff-filter=ACMRD -z` — 根からの相対・根の外の path は入らない)。
fn staged_paths(root: &Path) -> Result<Vec<String>, String> {
    let out = git(root, &["diff", "--cached", "--name-only", "--relative", "--diff-filter=ACMRD", "-z"])?;
    Ok(out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect())
}

/// stage で消えた path(名前替えの旧い側を含む — `--no-renames` の D)。major の warning の数の HEAD の側に、移した・消した file の
/// HEAD の版も数えるため(数えないと、warning を増やさない移しが増えに見える)。
fn removed_paths(root: &Path) -> Result<Vec<String>, String> {
    let out = git(root, &["diff", "--cached", "--name-only", "--relative", "--no-renames", "--diff-filter=D", "-z"])?;
    Ok(out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect())
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

/// 子を撃つ頼みを組む(設定・規則・続きの引数・path)。木(tree)が在れば HEAD の木、無ければ先端の木。
fn lint_args(options: &CommitHookOptions, tree: Option<&Path>, rules: &[String], extra: &[String], paths: &[String]) -> LintRun {
    let args = config_args(options, tree)
        .into_iter()
        .chain(["--enable".to_string(), rules.join(",")])
        .chain(extra.iter().cloned())
        .chain(std::iter::once("--".to_string()))
        .chain(paths.iter().cloned())
        .collect();
    LintRun { side: if tree.is_some() { Side::Head } else { Side::Tip }, rules: rules.to_vec(), args }
}

/// 止める物の一覧(種類ごと)と、repo 全体の比べを上限で測れなかった時の 1 回(これも止める)。
#[derive(Debug, Default)]
struct Blocking {
    staged: Vec<String>,
    fresh_critical: ByHead,
    grown_warnings: Vec<String>,
    whole: ByHead,
    unmeasured: Option<Unmeasured>,
    /// stage した Hy の file のうち linter が歩く範囲の外の物 — 測っていない事を名指すだけで止めない(agora-redesign #2821)。
    out_of_scope: Vec<String>,
}

/// 純粋: 子の linter の報告(editor-json)の `out_of_scope` — 名指した Hy の file のうち linter が歩く範囲の外の物(仕様 1 節
/// 「名指しの範囲の外」・agora-redesign #2821)。欄が無い・null なら空。
pub fn out_of_scope_of(report: &Value) -> Vec<String> {
    report["out_of_scope"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect()
}

/// 途中で測れなかった理由(上限越えは終了コード 1・失敗は終了コード 2)。
enum Stop {
    TimedOut(Unmeasured),
    Failed(String),
}

fn measured(result: Measured, what: &str) -> Result<Value, Stop> {
    match result {
        Measured::Report(value) => Ok(value),
        Measured::TimedOut(cut) => Err(Stop::TimedOut(Unmeasured { what: what.trim_end().to_string(), cut })),
        Measured::Failed(reason) => Err(Stop::Failed(format!("{}を doeff-linter で測れなかった: {}", what, reason))),
    }
}

fn empty_report() -> Value {
    serde_json::json!({ "violations": [] })
}

/// hook の 1 回の判じ — 終了コードと、出す行(行の頭の `doeff-linter commit-hook: ` を除く)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Assessment {
    pub code: u8,
    pub lines: Vec<String>,
}

/// hook の判じ(印字しない)。終了コード 0 = 通す・1 = 止める(上限で打ち切って測れなかった時を含む — 上限なしの命令を添える)・
/// 2 = git・linter が動かなかった。
pub fn assess(options: &CommitHookOptions) -> Assessment {
    match judge(options) {
        Ok(blocking) => {
            let code = u8::from(
                !(blocking.staged.is_empty()
                    && blocking.fresh_critical.is_empty()
                    && blocking.grown_warnings.is_empty()
                    && blocking.whole.is_empty()
                    && blocking.unmeasured.is_none()),
            );
            let lines = blocking
                .staged
                .iter()
                .map(|line| format!("stage した file の破れ: {}", line))
                .chain(blocking.fresh_critical.not_on_head.iter().map(|ident| format!("HEAD に無い critical: {}", ident)))
                .chain(blocking.grown_warnings.iter().map(|line| format!("major の warning が HEAD の版より増えた(stage した file の組の数): {}", line)))
                .chain(blocking.whole.not_on_head.iter().map(|ident| format!("repo 全体の規則の HEAD に無い当たり: {}", ident)))
                .chain(already_on_head_lines(&blocking))
                .chain(blocking.unmeasured.iter().flat_map(|unmeasured| cut_lines(options, unmeasured)))
                .chain(blocking.out_of_scope.iter().map(|path| format!("対象の外(linter が歩く範囲の外 — 層の規則はこの file を測っていない): {}", path)))
                .collect();
            Assessment { code, lines }
        }
        Err(Stop::TimedOut(unmeasured)) => Assessment { code: 1, lines: cut_lines(options, &unmeasured) },
        Err(Stop::Failed(reason)) => Assessment { code: 2, lines: vec![reason] },
    }
}

/// hook の本体 — 判じ(assess)の行を stderr に出し、終了コードを返す。
pub fn run(options: &CommitHookOptions) -> u8 {
    let assessment = assess(options);
    for line in &assessment.lines {
        eprintln!("{}{}", PREFIX, line);
    }
    assessment.code
}

/// 純粋: HEAD の版にも同じ識別子が在るのに止める当たり(既知の一覧を持たない規則の、変えた file の既存の当たり)の行と、在れば直し方の
/// 案内を 1 行。
fn already_on_head_lines(blocking: &Blocking) -> Vec<String> {
    let hits: Vec<String> = blocking
        .fresh_critical
        .already_on_head
        .iter()
        .chain(blocking.whole.already_on_head.iter())
        .map(|ident| format!("変えた file の既存の当たり(main にも在る・この規則は既知の一覧で下げない): {}", ident))
        .collect();
    let guide = (!hits.is_empty()).then(|| "この file を変えるなら、その当たりも同じ変更で直す(hook を飛ばさない)".to_string());
    hits.into_iter().chain(guide).collect()
}

fn judge(options: &CommitHookOptions) -> Result<Blocking, Stop> {
    let root = &options.root;
    let staged = staged_paths(root).map_err(Stop::Failed)?;
    let mut blocking = Blocking::default();
    if staged.is_empty() {
        return Ok(blocking);
    }
    // 宣言の file を変えた commit は、file 1 つで判じる規則も repo 全体の比べに当てる(門と同じ判定 split_for_change・#2127)。
    let split = split_for_change(&options.enabled, &staged, &options.declarations);
    let RuleSplit { quick, whole } = &split;
    let paths = if quick.is_empty() { Vec::new() } else { linted_paths(&staged, |p| root.join(p).exists()) };
    if paths.is_empty() && whole.is_empty() {
        return Ok(blocking);
    }
    let scratch = Scratch::create().map_err(Stop::Failed)?;
    // HEAD の木は事実の cache の根の commit-hook-tree/<repo を表す名>/tree に repo ごとに 1 つ置き、差分で進める(head_tree・#3858)。
    // 木の lock はこの関数を抜けるまで(子の linter が木を測り終えるまで)持つ。cache を使わない実行(DOEFF_LINTER_NO_CACHE)は、
    // この実行の一時 dir を場所にする(終わりに消える)。1 回限りの指定(Lint-Baseline)の木は、HEAD の木を今の宣言で測る — 既存の当たりは
    // 基点に在る物になり、新しい当たりだけが止まる(重ねた宣言の file は次に進める時に HEAD の中身へ戻す)。
    let home = match &options.cache {
        Some(base) => {
            let home = base.join(TREE_HOME_DIR);
            crate::project::facts_cache::mark_used(&home);
            home
        }
        None => scratch.path.clone(),
    };
    let held = head_tree(root, &options.overlay, &home, &scratch.path, LOCK_WAIT).map_err(Stop::Failed)?;
    let tree = held.as_ref().map(|h| h.path.as_path());
    if tree.is_some() && !options.overlay.is_empty() {
        eprintln!("{}{} — HEAD の木を今の宣言({})で測る(1 回限り)", PREFIX, BASELINE_TRAILER, options.overlay.join("・"));
    }
    let tip_root = root.canonicalize().unwrap_or_else(|_| root.clone());

    if !paths.is_empty() {
        // HEAD の版(HEAD に在る path だけ)を同じ規則で撃ち、先端の根の path へ付け替えて基点にする。stage で消えた file(名前替えの
        // 旧い側を含む)の HEAD の版も撃つ — major の warning の数を、移した・消した file の分も含めて比べるため。
        let removed = removed_paths(root).map_err(Stop::Failed)?;
        let head_paths: Vec<String> = tree
            .map(|t| {
                paths
                    .iter()
                    .chain(linted_paths(&removed, |p| t.join(p).is_file()).iter())
                    .filter(|p| t.join(p).is_file())
                    .cloned()
                    .collect::<BTreeSet<String>>()
                    .into_iter()
                    .collect()
            })
            .unwrap_or_default();
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
        // 子の linter が基点の file を読むのと同じ綴り(baseline::baseline_identities)で HEAD の版の識別子を読み、止める当たりを分ける。
        let head_criticals = crate::baseline::baseline_identities(&base).map_err(|reason| Stop::Failed(format!("HEAD の版の報告を読めない: {}", reason)))?;
        blocking.fresh_critical =
            split_by_head(&head_criticals, tip["new_critical"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)));
        blocking.grown_warnings = grown_major_warnings(&base, &tip);
        blocking.out_of_scope = out_of_scope_of(&tip);
    }

    if !whole.is_empty() {
        // 上限で打ち切られても、stage した file の当たりは捨てずに並べ、測れなかった比べを書いて止める(agora-redesign #2723・#3834)。
        match whole_repo_hits(options, tree, whole, &tip_root, &staged) {
            Ok(hits) => blocking.whole = hits,
            Err(Stop::TimedOut(unmeasured)) => blocking.unmeasured = Some(unmeasured),
            Err(failed) => return Err(failed),
        }
    }
    Ok(blocking)
}

/// repo 全体の比べ — 先端の木の当たりのうち HEAD の木に無い物と、stage した path の基点の差で下げない当たり(fresh_whole_repo_hits)。HEAD の木の結果は置き場から読めれば測らない(head_whole_report)。
fn whole_repo_hits(options: &CommitHookOptions, tree: Option<&Path>, whole: &[String], tip_root: &Path, staged: &[String]) -> Result<ByHead, Stop> {
    let head = match tree {
        Some(t) => head_whole_report(options, t, whole, tip_root)?,
        None => empty_report(),
    };
    let dot = vec![".".to_string()];
    let tip = measured(run_linter(&options.linter, &options.root, &lint_args(options, None, whole, &[], &dot), options.timeout), "先端の木の repo 全体の比べ")?;
    Ok(fresh_whole_repo_hits(&head, &tip, staged))
}

/// HEAD の木の repo 全体の比べの結果(違反の path は先端の根へ付け替え済み)。置き場に同じ鍵の結果が在ればそれを使い、無ければ木を測って
/// 置き場に残す(head_report_cache・agora-redesign #2723)。
fn head_whole_report(options: &CommitHookOptions, tree: &Path, whole: &[String], tip_root: &Path) -> Result<Value, Stop> {
    let place = options.cache.as_deref().and_then(|base| head_key(options, tree, whole).map(|key| (base, key)));
    if let Some(stored) = place.as_ref().and_then(|(base, key)| crate::head_report_cache::load(base, key)) {
        let mut report = stored.report;
        rebase_paths(&mut report, &stored.tree, &tip_root.to_string_lossy());
        return Ok(report);
    }
    let dot = vec![".".to_string()];
    let mut report = measured(run_linter(&options.linter, tree, &lint_args(options, Some(tree), whole, &[], &dot), options.timeout), "HEAD の木の repo 全体の比べ")?;
    if let Some((base, key)) = &place {
        crate::head_report_cache::store(base, key, tree, &report);
    }
    rebase_paths(&mut report, &tree.to_string_lossy(), &tip_root.to_string_lossy());
    Ok(report)
}

/// 純粋: 設定 file の、HEAD の木の中の相対 path — 設定 file が無ければ Some(None)、木の外(根の外・HEAD に無い)なら None
/// (子は先端の設定を読むので、木の中身では答えが決まらない — config_args と同じ選び)。
pub fn config_in_tree(options: &CommitHookOptions, tree: &Path) -> Option<Option<String>> {
    match &options.config {
        None => Some(None),
        Some(config) => {
            let rel = config.strip_prefix(&options.root).ok()?;
            tree.join(rel).is_file().then(|| Some(rel.to_string_lossy().into_owned()))
        }
    }
}

/// HEAD の木の結果の鍵(HEAD の commit・子の linter の版・規則の組・木の中の設定)。鍵に入らない入力が答えを変えうる時は None
/// (置き場を使わない): 基点へ今の宣言を写す 1 回限りの指定(Lint-Baseline)・設定 file が HEAD の木の外・HEAD や linter を読めない。
fn head_key(options: &CommitHookOptions, tree: &Path, rules: &[String]) -> Option<crate::head_report_cache::HeadKey> {
    if !options.overlay.is_empty() {
        return None;
    }
    // 設定の path は作業木の根からの相対で鍵に入れる(根の位置が違う 2 つの実行の結果を取り違えない — agora-redesign #3934)。
    let prefix = root_prefix(&options.root).ok()?;
    let config = config_in_tree(options, tree)?.map(|rel| format!("{}{}", prefix, rel));
    let head = git(&options.root, &["rev-parse", "--verify", "-q", "HEAD"]).ok()?;
    let head = String::from_utf8_lossy(&head).trim().to_string();
    let linter = crate::head_report_cache::linter_identity(&options.linter)?;
    Some(crate::head_report_cache::HeadKey { head, linter, rules: rules.to_vec(), config })
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
        let enabled = ids(&["DOEFF016", "DOEFF163", "DOEFF201", "DOEFF110", "DOEFF149", "DOEFF205", "doeff166", "DOEFF999"]);
        let split = split_rules(&enabled);
        // 知らない ID(DOEFF999)は file 1 つの側 — 有効な規則の一覧には載らず、DOEFF100 が知らせる。
        assert_eq!(split.quick, ids(&["DOEFF016", "DOEFF110", "DOEFF999"]));
        // repo 全体の比べは規則が名乗る列 — 手の一覧に無かった DOEFF149 も入る(agora-redesign #2090)。列の DOEFF166 が行の名指す規則を
        // 同じ実行で当てる(agora-redesign #1999・#2033)。
        assert_eq!(split.whole, ids(&["DOEFF163", "DOEFF149", "doeff166"]));
    }

    /// agora-redesign #3834: Jev の規則かどうかは規則の名乗り(ProjectRule::is_semantic)で決まり、ID の頭では決まらない — 頭が DOEFF2 の
    /// 決定的な規則(DOEFF206〜209)は quick か whole に入り、Jev の規則(DOEFF201〜205)だけがどちらにも入らない。
    #[test]
    fn commit_hook_split_rules_keeps_deterministic_rules_whose_id_starts_with_doeff2() {
        let enabled = ids(&["DOEFF201", "DOEFF202", "DOEFF203", "DOEFF204", "DOEFF205", "DOEFF206", "DOEFF207", "DOEFF208", "DOEFF209"]);
        let split = split_rules(&enabled);
        assert_eq!(split.quick, ids(&["DOEFF208"]));
        assert_eq!(split.whole, ids(&["DOEFF206", "DOEFF207", "DOEFF209"]));
    }

    #[test]
    fn commit_hook_a_declaration_change_moves_every_rule_to_the_whole_repo() {
        let declarations = Declarations {
            files: ids(&["pyproject.toml", "architecture.hy"]),
            dirs: ids(&["scripts/doeff_lint/REG"]),
            switches: ids(&["pyproject.toml", "architecture.hy"]),
        };
        let enabled = ids(&["DOEFF016", "DOEFF102", "DOEFF201", "DOEFF149"]);
        // 宣言に触れない変更は規則の名乗りの分けのまま。
        let plain = split_for_change(&enabled, &ids(&["app/core/x.hy"]), &declarations);
        assert_eq!(plain, RuleSplit { quick: ids(&["DOEFF016", "DOEFF102"]), whole: ids(&["DOEFF149"]) });
        // 宣言の file・登録簿の dir の下の file を変えた変更は、file 1 つで判じる規則も whole へ(意味の規則は除いたまま・順は enable)。
        for changed in [ids(&["architecture.hy"]), ids(&["pyproject.toml", "app/core/x.hy"]), ids(&["scripts/doeff_lint/REG/k.txt"])] {
            let moved = split_for_change(&enabled, &changed, &declarations);
            assert_eq!(moved, RuleSplit { quick: Vec::new(), whole: ids(&["DOEFF016", "DOEFF102", "DOEFF149"]) }, "{:?}", changed);
        }
        // 名の前方一致だけでは触れない(dir の名を頭に持つ別の dir)。
        assert!(!touches_declaration(&ids(&["scripts/doeff_lint/REGISTRY-OTHER/k.txt", "architecture.hy.bak"]), &declarations));
    }

    #[test]
    fn commit_hook_baseline_overlay_needs_a_reasoned_trailer() {
        let declarations = Declarations { files: Vec::new(), dirs: Vec::new(), switches: ids(&["pyproject.toml", "architecture.hy"]) };
        // 理由つきの行だけが効く(本文のどこに在ってもよい・行の頭の空白は読む)。
        let reasoned = "規則を鳴らす\n\nLint-Baseline: declarations — DOEFF170〜172 を鳴らし始める(#2143)\nCo-Authored-By: x\n";
        assert_eq!(baseline_overlay(reasoned, &declarations), ids(&["pyproject.toml", "architecture.hy"]));
        // 反例: 理由の無い行・行の途中の言及・違う trailer は効かない(基点は HEAD の宣言のまま — #2127 の見逃しを作らない)。
        for message in ["x\n\nLint-Baseline: declarations\n", "x\n\nLint-Baseline: declarations — \n", "本文で Lint-Baseline: declarations に触れる\n", "Registry-Grows: 1 理由\n"] {
            assert!(baseline_overlay(message, &declarations).is_empty(), "{:?}", message);
        }
    }

    #[test]
    fn commit_hook_names_a_retired_hand_list() {
        let kept = CommitHookSection { whole_repo_rules: ids(&["DOEFF163"]), timeout_s: None };
        assert!(retired_whole_repo_rules_note(Some(&kept)).is_some_and(|line| line.contains("退役")));
        assert_eq!(retired_whole_repo_rules_note(Some(&CommitHookSection::default())), None);
        assert_eq!(retired_whole_repo_rules_note(None), None);
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

    fn w(rule: &str, path: &str, severity: &str, level: &str) -> Value {
        json!({ "rule": rule, "path": path, "severity": severity, "level": level, "message": "m" })
    }

    /// agora-redesign #2683: major の warning は規則ごとの数で HEAD の版と比べる — 1 つ増えれば名指す・同じ数と減った数は出さない・
    /// 別の file へ移っただけ(移し・分割)は数が同じなので出さない。critical・error・minor・info は数えない(別の段か対象の外)。
    #[test]
    fn commit_hook_grown_major_warnings_counts_per_rule() {
        let head = json!({ "violations": [
            w("DOEFF172", "/h/a.hy", "warning", "major"),
            w("DOEFF113", "/h/old.hy", "warning", "major"),
            w("DOEFF113", "/h/old.hy", "warning", "major"),
        ]});
        // 増えた: DOEFF172 が 1 → 2(同じ file)。新しい規則 DOEFF105 が 0 → 1。DOEFF113 は old.hy から new.hy へ 2 つとも移った(同じ数)。
        let grown = json!({ "violations": [
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF105", "/r/b.hy", "warning", "major"),
            w("DOEFF113", "/r/new.hy", "warning", "major"),
            w("DOEFF113", "/r/new.hy", "warning", "major"),
        ]});
        assert_eq!(grown_major_warnings(&head, &grown), ids(&["DOEFF105 0 → 1", "DOEFF172 1 → 2"]));
        // 同じ数・減った数は止めない(減った commit の次は、その HEAD が基点になる)。
        assert!(grown_major_warnings(&head, &head).is_empty());
        let fewer = json!({ "violations": [w("DOEFF113", "/r/new.hy", "warning", "major")] });
        assert!(grown_major_warnings(&head, &fewer).is_empty());
        // major の warning でない物は数えない: critical(new_critical の段)・error(blocking_violations の段)・minor・info。
        let others = json!({ "violations": [
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF999", "/r/a.hy", "warning", "critical"),
            w("DOEFF998", "/r/a.hy", "error", "major"),
            w("DOEFF997", "/r/a.hy", "warning", "minor"),
            w("DOEFF996", "/r/a.hy", "info", "info"),
        ]});
        assert!(grown_major_warnings(&head, &others).is_empty());
        // HEAD に版が無い(新しい file だけ)なら、基点は 0。
        assert_eq!(grown_major_warnings(&json!({ "violations": [] }), &fewer), ids(&["DOEFF113 0 → 1"]));
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
        assert_eq!(fresh_whole_repo_hits(&head, &tip, &[]), ByHead { not_on_head: ids(&["new-critical", "new-error"]), already_on_head: vec![] });
        // 鍵の無い違反は根からの相対 path で比べる(先端と HEAD の木で根が違っても同じ識別子)。
        let keyless = |root: &str| json!({ "root": root, "violations": [
            { "severity": "error", "level": "major", "path": format!("{}/x.py", root), "rule": "DOEFF016", "message": "m" }
        ]});
        assert!(fresh_whole_repo_hits(&keyless("/h"), &keyless("/r"), &[]).is_empty());
        assert_eq!(fresh_whole_repo_hits(&empty_report(), &keyless("/r"), &[]).not_on_head, ids(&["x.py::DOEFF016::m"]));
    }

    /// agora-redesign #3834: 既知の一覧を持たない規則(DOEFF206〜209)の critical は、stage した path に在れば HEAD に同じ鍵が在っても止める。
    /// stage していない file の当たり・登録簿で下げる規則の HEAD に在る critical は止めない。
    #[test]
    fn commit_hook_whole_repo_diff_keeps_unlisted_rule_hits_in_staged_files() {
        let hit = |rule: &str, path: &str, key: &str| json!({ "rule": rule, "path": format!("/r/{}", path), "key": key, "severity": "error", "level": "critical" });
        let all = json!({ "root": "/r", "violations": [
            hit("DOEFF209", "a.hy", "a.hy::DOEFF209::poll::Delay::periodic"),
            hit("DOEFF209", "b.hy", "b.hy::DOEFF209::poll::Delay::periodic"),
            hit("DOEFF163", "a.hy", "a.hy::DOEFF163::queue"),
        ]});
        // HEAD にも同じ鍵が在る当たりなので「HEAD に在る」側に分ける(文が「HEAD に無い」にならない・cisco-c8 の頼み 2026-10-07)。
        assert_eq!(
            fresh_whole_repo_hits(&all, &all, &ids(&["a.hy"])),
            ByHead { not_on_head: vec![], already_on_head: ids(&["a.hy::DOEFF209::poll::Delay::periodic"]) }
        );
        assert!(fresh_whole_repo_hits(&all, &all, &ids(&["c.hy"])).is_empty());
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
    fn commit_hook_unmeasured_line_names_the_comparison_tree_rules_and_limit() {
        let cut = Cut { side: Side::Head, rules: ids(&["DOEFF163", "DOEFF167"]), limit: Duration::from_secs(20), measure: Measure::Cpu };
        let line = unmeasured_line(&Unmeasured { what: "HEAD の木の repo 全体の比べ".to_string(), cut });
        for part in ["HEAD の木の repo 全体の比べを測れなかった", "HEAD の木で当てた規則 2 個(DOEFF163,DOEFF167)", "CPU 秒の上限 20 秒で打ち切った", "確かめていないので commit を止める"] {
            assert!(line.contains(part), "{:?} が無い: {}", part, line);
        }
        let tip = Cut { side: Side::Tip, rules: ids(&["DOEFF016"]), limit: Duration::from_secs(1), measure: Measure::Wall };
        assert!(unmeasured_line(&Unmeasured { what: "repo 全体の比べ".to_string(), cut: tip })
            .contains("先端の木(作業木)で当てた規則 1 個(DOEFF016)の子の linter をwall の上限 1 秒で打ち切った(この機体では子の CPU 秒を読めない)"));
        let idle = Cut { side: Side::Tip, rules: ids(&["DOEFF016"]), limit: Duration::from_secs(2), measure: Measure::WallBackstop };
        assert!(unmeasured_line(&Unmeasured { what: "repo 全体の比べ".to_string(), cut: idle })
            .contains("wall の予備の上限 8 秒で打ち切った(CPU 秒の上限 2 秒の 4 倍"));
    }

    /// card ki-79532edd43dd: CPU 秒が読めれば CPU 秒で測る — 混んで wall だけ伸びた子(wall 9 秒・CPU 3 秒・上限 4 秒)は切らず、CPU 秒が
    /// 上限に届けば切る。wall は予備の上限(上限の 4 倍)だけを見る。読めない機体は今までどおり wall で測る。
    #[test]
    fn commit_hook_cut_measure_reads_cpu_seconds_and_keeps_a_wall_backstop() {
        let s = Duration::from_secs;
        assert_eq!(cut_measure(s(9), Some(s(3)), s(4)), None);
        assert_eq!(cut_measure(s(5), Some(s(4)), s(4)), Some(Measure::Cpu));
        assert_eq!(cut_measure(s(15), Some(s(1)), s(4)), None);
        assert_eq!(cut_measure(s(16), Some(s(1)), s(4)), Some(Measure::WallBackstop));
        assert_eq!(cut_measure(s(3), None, s(4)), None);
        assert_eq!(cut_measure(s(4), None, s(4)), Some(Measure::Wall));
    }

    /// /proc/<pid>/stat の utime・stime・cutime・cstime(14〜17 番目・100 分の 1 秒)の和を読む。名の欄の ')' と空白に惑わされない。
    #[test]
    fn commit_hook_cpu_of_stat_sums_own_and_waited_children_ticks() {
        let stat = "4242 (doeff (lin) ter) R 1 4242 4242 0 -1 4194304 100 0 0 0 250 30 15 5 20 0 8 0 12345 1000 200";
        assert_eq!(cpu_of_stat(stat), Some(Duration::from_millis(3000)));
        assert_eq!(cpu_of_stat("4242 (x) R 1"), None);
        assert_eq!(cpu_of_stat("no paren"), None);
    }

    #[test]
    fn commit_hook_head_cache_is_used_only_when_the_tree_holds_the_config() {
        let tree = tempfile::TempDir::new().unwrap();
        std::fs::write(tree.path().join("pyproject.toml"), "").unwrap();
        let options = |config: Option<&str>| CommitHookOptions {
            root: PathBuf::from("/repo"),
            config: config.map(PathBuf::from),
            enabled: Vec::new(),
            declarations: Declarations::default(),
            overlay: Vec::new(),
            message: None,
            cli_enable: Vec::new(),
            cli_disable: Vec::new(),
            timeout: Some(Duration::from_secs(20)),
            linter: PathBuf::from("/bin/doeff-linter"),
            cache: None,
        };
        // 設定が木の中に在れば木の中の相対 path・設定が無ければ Some(None)。
        assert_eq!(config_in_tree(&options(Some("/repo/pyproject.toml")), tree.path()), Some(Some("pyproject.toml".to_string())));
        assert_eq!(config_in_tree(&options(None), tree.path()), Some(None));
        // 反例: HEAD の木に無い設定・根の外の設定は、子が先端の設定を読むので鍵で答えが決まらない(置き場を使わない)。
        assert_eq!(config_in_tree(&options(Some("/repo/sub/pyproject.toml")), tree.path()), None);
        assert_eq!(config_in_tree(&options(Some("/elsewhere/pyproject.toml")), tree.path()), None);
        // 反例: 1 回限りの指定(Lint-Baseline)は今の宣言を基点の木へ写すので、置き場を使わない。
        let mut overlaid = options(None);
        overlaid.overlay = ids(&["architecture.hy"]);
        assert!(head_key(&overlaid, tree.path(), &ids(&["DOEFF167"])).is_none());
    }

    #[test]
    fn commit_hook_timeout_prefers_cli_then_config_then_default() {
        let section = CommitHookSection { whole_repo_rules: Vec::new(), timeout_s: Some(7) };
        assert_eq!(resolve_timeout(Some(&section), Some(3)), Some(Duration::from_secs(3)));
        assert_eq!(resolve_timeout(Some(&section), None), Some(Duration::from_secs(7)));
        assert_eq!(resolve_timeout(None, None), Some(Duration::from_secs(DEFAULT_TIMEOUT_S)));
        // 0 秒は上限なし(引数でも設定でも)。
        assert_eq!(resolve_timeout(Some(&section), Some(0)), None);
        assert_eq!(resolve_timeout(Some(&CommitHookSection { whole_repo_rules: Vec::new(), timeout_s: Some(0) }), None), None);
    }

    /// agora-redesign #3834: 上限なしの命令は、同じ linter・根・設定・--enable / --disable・commit 本文の file に上限なしを足した 1 行で、
    /// 空白や ' を含む path も shell にそのまま貼れる形で書く。設定が無ければ --no-config。
    #[test]
    fn commit_hook_unbounded_command_repeats_the_same_hook_without_a_limit() {
        let mut options = CommitHookOptions {
            root: PathBuf::from("/repo"),
            config: Some(PathBuf::from("/repo/pyproject.toml")),
            enabled: Vec::new(),
            declarations: Declarations::default(),
            overlay: Vec::new(),
            message: None,
            cli_enable: Vec::new(),
            cli_disable: Vec::new(),
            timeout: Some(Duration::from_secs(20)),
            linter: PathBuf::from("/bin/doeff-linter"),
            cache: None,
        };
        assert_eq!(unbounded_command(&options), "/bin/doeff-linter --commit-hook --root /repo --config /repo/pyproject.toml --commit-hook-timeout-s 0");
        options.root = PathBuf::from("/my repo/it's");
        options.config = None;
        options.cli_enable = ids(&["DOEFF163", "DOEFF016"]);
        options.cli_disable = ids(&["DOEFF110"]);
        options.message = Some(PathBuf::from("/my repo/it's/.git/COMMIT_EDITMSG"));
        assert_eq!(
            unbounded_command(&options),
            "/bin/doeff-linter --commit-hook --root '/my repo/it'\\''s' --no-config --enable DOEFF163,DOEFF016 --disable DOEFF110 \
             --commit-message '/my repo/it'\\''s/.git/COMMIT_EDITMSG' --commit-hook-timeout-s 0"
        );
    }

    #[test]
    fn commit_hook_tree_record_round_trips_and_refuses_a_broken_record() {
        let record = TreeRecord { sha: "a".repeat(40), overlay: ids(&["architecture.hy", "pyproject.toml"]) };
        assert_eq!(parse_tree_record(&render_tree_record(&record)), Some(record));
        let plain = TreeRecord { sha: "b".repeat(64), overlay: Vec::new() };
        assert_eq!(parse_tree_record(&render_tree_record(&plain)), Some(plain));
        for broken in ["", "\n", "abc\n", &format!("{}\n", "g".repeat(40))] {
            assert_eq!(parse_tree_record(broken), None, "{:?}", broken);
        }
    }

    #[test]
    fn commit_hook_names_only_the_old_sha_trees_and_keys_repos_by_the_common_dir() {
        let sha = "0".repeat(40);
        for old in [sha.clone(), format!("{}-{}", sha, "f".repeat(16)), ".partial-x".to_string()] {
            assert!(is_sha_tree_name(&old), "{}", old);
        }
        for other in ["notes", "0123456789abcdef", &format!("{}-x", sha), &"0".repeat(39)] {
            assert!(!is_sha_tree_name(other), "{}", other);
        }
        let key = repo_key(Path::new("/repos/a/.git"));
        assert!(is_hex(&key, 16), "{}", key);
        assert_eq!(key, repo_key(Path::new("/repos/a/.git")));
        assert_ne!(key, repo_key(Path::new("/repos/b/.git")));
    }
}
