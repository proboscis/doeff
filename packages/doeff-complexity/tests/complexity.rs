use std::fs;
use std::path::Path;
use std::process::Command;

use doeff_complexity::counts::CountsFile;
use doeff_complexity::measure::{is_prod_hy, measure_source};
use tempfile::TempDir;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_doeff-complexity")
}

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .expect("git must run");
    assert!(
        out.status.success(),
        "git {:?} failed:\nstdout: {}\nstderr: {}",
        args,
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn write_file(root: &Path, rel: &str, content: &str) {
    let p = root.join(rel);
    if let Some(parent) = p.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(&p, content).unwrap();
}

fn init_repo(charter: &str) -> TempDir {
    let dir = TempDir::new().unwrap();
    git(dir.path(), &["init", "-q", "-b", "main"]);
    git(dir.path(), &["config", "user.name", "Test"]);
    git(dir.path(), &["config", "user.email", "test@example.com"]);
    write_file(dir.path(), "repo-charter.toml", charter);
    dir
}

const DEFAULT_CHARTER: &str = r#"
schema = 1

[budget]
max_fn_cc = 25
max_type_wmc = 47
max_module_wmc = 200
ceiling_god_wmc_max = 170
ceiling_fn_cc_max = 374
"#;

/// Generates a Hy function with exact cyclomatic complexity `cc` (>= 1).
fn hy_fn(name: &str, cc: usize) -> String {
    assert!(cc >= 1);
    let mut body = String::new();
    for i in 0..(cc - 1) {
        body.push_str(&format!("  (when x{} (setv y {}))\n", i, i));
    }
    if body.is_empty() {
        body.push_str("  1\n");
    }
    format!("(defk {} [x]\n{})\n", name, body)
}

/// Generates a Hy file with `n` simple functions of complexity `each_cc` each (total module WMC = n * each_cc).
fn hy_module_of(prefix: &str, n: usize, each_cc: usize) -> String {
    let mut s = String::new();
    for i in 0..n {
        s.push_str(&hy_fn(&format!("{}-{}", prefix, i), each_cc));
    }
    s
}

/// Generates a Hy class with methods of specified CCs.
fn hy_class(name: &str, methods: &[(&str, usize)]) -> String {
    let mut s = format!("(defclass {} []\n", name);
    for (mname, cc) in methods {
        let mut body = String::new();
        for i in 0..(*cc - 1) {
            body.push_str(&format!("    (when a{} (setv b {}))\n", i, i));
        }
        if body.is_empty() {
            body.push_str("    1\n");
        }
        s.push_str(&format!("  (defn {} [self]\n  {})\n", mname, body));
    }
    s.push_str(")\n");
    s
}

fn run_cmd(dir: &Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(args)
        .current_dir(dir)
        .output()
        .expect("doeff-complexity must run");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn prod_hy_filter_matches_design_section_2() {
    assert!(is_prod_hy("controllers/kanban/core/board.hy"));
    assert!(is_prod_hy("./scripts/check.hy"));
    assert!(!is_prod_hy("controllers/kanban/tests/test_board.hy"));
    assert!(!is_prod_hy("controllers/kanban/test/foo.hy"));
    assert!(!is_prod_hy("e2e/smoke.hy"));
    assert!(!is_prod_hy("__tests__/unit.hy"));
    assert!(!is_prod_hy("controllers/generated/stubs.hy"));
    assert!(!is_prod_hy("docs/sample.hy"));
    assert!(!is_prod_hy(".agents/helper.hy"));
    assert!(!is_prod_hy("controllers/test_helper.hy"));
    assert!(!is_prod_hy("controllers/conftest.hy"));
    assert!(!is_prod_hy("controllers/foo_test.hy"));
    assert!(!is_prod_hy("controllers/foo.test.hy"));
    assert!(!is_prod_hy("controllers/foo.spec.hy"));
    assert!(!is_prod_hy("controllers/board.py"));
}

#[test]
fn branch_counting_table_and_exclusions() {
    let src = r#"
(defk branches [a b xs]
  {:pre [(: a int)] :post [(if True True False)]}
  (if a 1 2)
  (when b 3)
  (unless a 4)
  (and a b 1)
  (or a b)
  (cond a 1 b 2 True 3)
  (case a 1 "one" 2 "two" _ "other")
  (match a
    1 :if b "guarded"
    2 "two"
    _ "wild")
  (lfor x xs :if (> x 0) x)
  (for [x xs :if (> x 0)] x)
  (on-raise (do 1) (ValueError 2) (KeyError 3))
  '(if a (when b (and 1 2 3)))
  (fn [z] (when z 1)))

(deftest ignored-test []
  (if 1 (when 2 (unless 3 4))))

(defhandler my-handler [x]
  (EffA [k] (if x 1 2))
  (EffB [k] 1))

(defclass MyClass []
  (defn m1 [self x] (when x 1))
  (defk m2 [self x] (if x 1 (unless x 2))))

(when top-flag 1)
"#;
    let m = measure_source("pkg/mod.hy", src).unwrap();
    // branches:
    // base 1 + if(1) + when(1) + unless(1) + and(3-1=2) + or(2-1=1) + cond(2) + case(2)
    // + match(arm1: +1 non-wild +1 :if, arm2: +1 non-wild, arm3: wild 0 = 3)
    // + lfor(1 iter + 1 :if = 2) + for(1 iter + 1 :if = 2) + on-raise(2 clauses)
    // + quote(0) + fn(when: 1) + contract(0)
    // = 1 + 1 + 1 + 1 + 2 + 1 + 2 + 2 + 3 + 2 + 2 + 2 + 1 = 21
    assert_eq!(m.fns[0].name, "branches");
    assert_eq!(m.fns[0].cc, 21);

    // deftest skipped -> total fns = 1 + 2 clauses + 2 methods = 5
    assert_eq!(m.fns.len(), 5);
    // my-handler: EffA(2) + EffB(1) = 3
    assert_eq!(m.types.get("my-handler"), Some(&3));
    // MyClass: m1(2) + m2(3) = 5
    assert_eq!(m.types.get("MyClass"), Some(&5));
    // loose top-level: (when top-flag 1) = 1
    assert_eq!(m.loose_top_cc, 1);
    // module_wmc = 21 + 3 + 5 + 1 = 30
    assert_eq!(m.module_wmc, 30);
    // fn_sum excludes loose top-level: 21 + 3 + 5 = 29
    assert_eq!(m.fn_sum(), 29);
}

#[test]
fn six_required_gate_scenarios_cfeb656c3() {
    let dir = init_repo(DEFAULT_CHARTER);
    let root = dir.path();

    // Base repo setup:
    // - pkg/small.hy: module WMC 10
    // - pkg/near_limit.hy: module WMC 190 (19 fns x 10 CC)
    // - pkg/big.hy: module WMC 210 (21 fns x 10 CC — already >= 200)
    // - pkg/with_class.hy: class Holder with method heavy-method (CC 30) -> type WMC 30, module WMC 30
    // - pkg/source_move.hy: free fn movable-fn (CC 30) + small helper (CC 5) -> module WMC 35
    // - pkg/dest_move.hy: helper (CC 5) -> module WMC 5
    // - pkg/rename_old.hy: module WMC 220 (20 fns x 10 + 1 fn x 20? Let's include a 30-CC fn and 50-WMC class!)
    write_file(root, "pkg/small.hy", &hy_fn("small-fn", 10));
    write_file(root, "pkg/near_limit.hy", &hy_module_of("near", 19, 10));
    write_file(root, "pkg/big.hy", &hy_module_of("big", 21, 10));
    write_file(root, "pkg/with_class.hy", &hy_class("Holder", &[("heavy-method", 30)]));
    write_file(
        root,
        "pkg/source_move.hy",
        &format!("{}{}", hy_fn("movable-fn", 30), hy_fn("stay-small", 5)),
    );
    write_file(root, "pkg/dest_move.hy", &hy_fn("dest-helper", 5));
    write_file(
        root,
        "pkg/rename_old.hy",
        &format!(
            "{}{}{}",
            hy_module_of("ren", 14, 10),
            hy_fn("ren-heavy", 30),
            hy_class("RenBigClass", &[("m1", 25), ("m2", 25)])
        ),
    );
    git(root, &["add", "."]);
    git(root, &["commit", "-q", "-m", "base"]);
    let base = git(root, &["rev-parse", "HEAD"]);

    let counts_path = root.join(".counts.json");
    let counts_str = counts_path.to_str().unwrap();
    let (rc, _, err) = run_cmd(root, &["count", "--out", counts_str]);
    assert_eq!(rc, 0, "count failed: {}", err);

    // Self-check with empty --paths must pass
    let (rc, _, err) = run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths"]);
    assert_eq!(rc, 0, "self-check failed: {}", err);

    // Scenario 1: Add a function exceeding line 25 (CC 26) -> REJECT (exit 1)
    let orig_small = fs::read_to_string(root.join("pkg/small.hy")).unwrap();
    write_file(root, "pkg/small.hy", &format!("{}{}", orig_small, hy_fn("too-complex", 26)));
    let (rc, out, _) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/small.hy"]);
    assert_eq!(rc, 1, "scenario 1 should reject, stdout: {}", out);
    assert!(out.contains("too-complex") && out.contains("0 -> 26"), "stdout: {}", out);
    write_file(root, "pkg/small.hy", &orig_small);

    // Scenario 2: Newly make a module exceed 200 (190 -> 205) -> REJECT (exit 1)
    let orig_near = fs::read_to_string(root.join("pkg/near_limit.hy")).unwrap();
    write_file(root, "pkg/near_limit.hy", &format!("{}{}", orig_near, hy_fn("tip-over", 15)));
    let (rc, out, _) = run_cmd(
        root,
        &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/near_limit.hy"],
    );
    assert_eq!(rc, 1, "scenario 2 should reject, stdout: {}", out);
    assert!(out.contains("module pkg/near_limit.hy: 190 -> 205"), "stdout: {}", out);
    write_file(root, "pkg/near_limit.hy", &orig_near);

    // Scenario 3: Grow an already-exceeding module (210 -> 211) -> PASS (exit 0)
    let orig_big = fs::read_to_string(root.join("pkg/big.hy")).unwrap();
    write_file(root, "pkg/big.hy", &format!("{}{}", orig_big, hy_fn("tiny-fix", 1)));
    let (rc, out, err) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/big.hy"]);
    assert_eq!(rc, 0, "scenario 3 should pass, stdout: {}, stderr: {}", out, err);
    write_file(root, "pkg/big.hy", &orig_big);

    // Scenario 4: Move a method out of a class to a free function in the same file -> PASS (exit 0)
    let orig_cls = fs::read_to_string(root.join("pkg/with_class.hy")).unwrap();
    write_file(
        root,
        "pkg/with_class.hy",
        &format!("(defclass Holder []\n  (defn stub [self] 1))\n{}", hy_fn("heavy-method", 30)),
    );
    let (rc, out, err) = run_cmd(
        root,
        &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/with_class.hy"],
    );
    assert_eq!(rc, 0, "scenario 4 should pass, stdout: {}, stderr: {}", out, err);
    write_file(root, "pkg/with_class.hy", &orig_cls);

    // Scenario 5: Move a function (CC 30) to another file -> PASS (exit 0)
    let orig_src = fs::read_to_string(root.join("pkg/source_move.hy")).unwrap();
    let orig_dst = fs::read_to_string(root.join("pkg/dest_move.hy")).unwrap();
    write_file(root, "pkg/source_move.hy", &hy_fn("stay-small", 5));
    write_file(root, "pkg/dest_move.hy", &format!("{}{}", orig_dst, hy_fn("movable-fn", 30)));
    let (rc, out, err) = run_cmd(
        root,
        &[
            "gate",
            "--base",
            &base,
            "--counts",
            counts_str,
            "--paths",
            "pkg/source_move.hy",
            "pkg/dest_move.hy",
        ],
    );
    assert_eq!(rc, 0, "scenario 5 should pass, stdout: {}, stderr: {}", out, err);
    write_file(root, "pkg/source_move.hy", &orig_src);
    write_file(root, "pkg/dest_move.hy", &orig_dst);

    // Scenario 6: Rename a file (`git mv pkg/rename_old.hy pkg/rename_new.hy`) -> PASS (exit 0)
    git(root, &["mv", "pkg/rename_old.hy", "pkg/rename_new.hy"]);
    let (rc, out, err) = run_cmd(
        root,
        &[
            "gate",
            "--base",
            &base,
            "--counts",
            counts_str,
            "--paths",
            "pkg/rename_old.hy",
            "pkg/rename_new.hy",
        ],
    );
    assert_eq!(rc, 0, "scenario 6 should pass, stdout: {}, stderr: {}", out, err);
    // Also test rename when only new path is passed!
    let (rc, out, err) = run_cmd(
        root,
        &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/rename_new.hy"],
    );
    assert_eq!(rc, 0, "scenario 6 (new path only) should pass, stdout: {}, stderr: {}", out, err);
}

#[test]
fn type_ratchet_ceilings_and_copy_paste_rejected() {
    let dir = init_repo(DEFAULT_CHARTER);
    let root = dir.path();
    write_file(root, "pkg/types.hy", &hy_class("AlmostGod", &[("m1", 23), ("m2", 23)]));
    write_file(root, "pkg/src.hy", &hy_fn("existing-heavy", 30));
    write_file(root, "pkg/dst.hy", &hy_fn("clean", 2));
    git(root, &["add", "."]);
    git(root, &["commit", "-q", "-m", "base"]);
    let base = git(root, &["rev-parse", "HEAD"]);
    let counts = root.join(".counts.json");
    let counts_str = counts.to_str().unwrap();
    assert_eq!(run_cmd(root, &["count", "--out", counts_str]).0, 0);

    // Growing type from 46 -> 47 (>= max_type_wmc 47) -> REJECT
    let orig_types = fs::read_to_string(root.join("pkg/types.hy")).unwrap();
    write_file(root, "pkg/types.hy", &hy_class("AlmostGod", &[("m1", 23), ("m2", 24)]));
    let (rc, out, _) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/types.hy"]);
    assert_eq!(rc, 1, "46 -> 47 type must reject: {}", out);
    assert!(out.contains("AlmostGod: 46 -> 47"), "stdout: {}", out);
    write_file(root, "pkg/types.hy", &orig_types);

    // Copy-pasting an existing 30-CC function without deleting it from src.hy -> REJECT
    write_file(
        root,
        "pkg/dst.hy",
        &format!("{}{}", hy_fn("clean", 2), hy_fn("existing-heavy", 30)),
    );
    let (rc, out, _) = run_cmd(
        root,
        &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/src.hy", "pkg/dst.hy"],
    );
    assert_eq!(rc, 1, "copy-paste must reject: {}", out);

    // Exceeding ceiling_fn_cc_max (374) even if somehow matched -> REJECT
    write_file(root, "pkg/dst.hy", &hy_fn("mega", 375));
    let (rc, out, _) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/dst.hy"]);
    assert_eq!(rc, 1);
    assert!(out.contains("ceiling_fn_cc_max"), "stdout: {}", out);
}

#[test]
fn unreadable_files_and_bad_inputs_exit_2() {
    let dir = init_repo(DEFAULT_CHARTER);
    let root = dir.path();
    write_file(root, "pkg/ok.hy", &hy_fn("good", 2));
    git(root, &["add", "."]);
    git(root, &["commit", "-q", "-m", "base"]);
    let base = git(root, &["rev-parse", "HEAD"]);
    let counts = root.join(".counts.json");
    let counts_str = counts.to_str().unwrap();
    assert_eq!(run_cmd(root, &["count", "--out", counts_str]).0, 0);

    // Syntax error (unclosed paren) -> exit 2
    write_file(root, "pkg/ok.hy", "(defk broken [x]\n  (if x 1\n");
    let (rc, _, err) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/ok.hy"]);
    assert_eq!(rc, 2, "syntax error must exit 2, stderr: {}", err);
    assert!(err.contains("Hy syntax error"), "stderr: {}", err);

    // Non-UTF-8 bytes -> exit 2
    fs::write(root.join("pkg/ok.hy"), [0xffu8, 0xfe, 0x00]).unwrap();
    let (rc, _, err) =
        run_cmd(root, &["gate", "--base", &base, "--counts", counts_str, "--paths", "pkg/ok.hy"]);
    assert_eq!(rc, 2, "non-utf8 must exit 2, stderr: {}", err);

    // Corrupt counts file -> exit 2 even with empty --paths (self-check!)
    write_file(root, "pkg/ok.hy", &hy_fn("good", 2));
    write_file(root, ".bad-counts.json", "{not valid json");
    let bad_counts = root.join(".bad-counts.json");
    let (rc, _, err) = run_cmd(
        root,
        &["gate", "--base", &base, "--counts", bad_counts.to_str().unwrap(), "--paths"],
    );
    assert_eq!(rc, 2, "corrupt counts must exit 2, stderr: {}", err);
}

#[test]
fn hook_staged_mode_compares_head_and_index() {
    let dir = init_repo(DEFAULT_CHARTER);
    let root = dir.path();
    write_file(root, "pkg/mod.hy", &hy_fn("base-fn", 5));
    write_file(root, "pkg/big.hy", &hy_module_of("big", 21, 10));
    git(root, &["add", "."]);
    git(root, &["commit", "-q", "-m", "base"]);

    // Unstaged violation in working tree is ignored when index is clean -> exit 0
    write_file(root, "pkg/mod.hy", &hy_fn("unstaged-bad", 30));
    let (rc, out, err) = run_cmd(root, &["gate", "--staged"]);
    assert_eq!(rc, 0, "unstaged change should not fail --staged: {} {}", out, err);

    // Stage the bad function (replaces base-fn CC 5 -> unstaged-bad CC 30) -> exit 1
    git(root, &["add", "pkg/mod.hy"]);
    let (rc, out, _) = run_cmd(root, &["gate", "--staged"]);
    assert_eq!(rc, 1, "staged bad fn must reject: {}", out);
    assert!(out.contains("unstaged-bad: 5 -> 30"), "stdout: {}", out);

    // Reset mod.hy, stage +1 on already-big module -> exit 0
    git(root, &["reset", "-q", "HEAD", "pkg/mod.hy"]);
    git(root, &["checkout", "--", "pkg/mod.hy"]);
    let orig_big = fs::read_to_string(root.join("pkg/big.hy")).unwrap();
    write_file(root, "pkg/big.hy", &format!("{}{}", orig_big, hy_fn("safe-add", 1)));
    git(root, &["add", "pkg/big.hy"]);
    let (rc, out, err) = run_cmd(root, &["gate", "--staged"]);
    assert_eq!(rc, 0, "staged +1 on already >=200 module must pass: {} {}", out, err);

    // Verify counts summary JSON fields include module sum and max
    let (rc, out, err) = run_cmd(root, &["count"]);
    assert_eq!(rc, 0, "count stdout failed: {}", err);
    let parsed: CountsFile = serde_json::from_str(&out).expect("valid counts JSON");
    assert_eq!(parsed.summary.modules.count, 2);
    assert_eq!(parsed.summary.modules.sum, 216);
    assert_eq!(parsed.summary.modules.max, 211);
    assert_eq!(parsed.summary.modules.over_count, Some(1));
    assert_eq!(parsed.summary.modules.over_sum, Some(211));
}
