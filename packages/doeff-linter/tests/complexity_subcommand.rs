use std::fs;
use std::path::Path;
use std::process::Command;

use tempfile::TempDir;

fn linter_bin() -> &'static str {
    env!("CARGO_BIN_EXE_doeff-linter")
}

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .expect("git must run");
    assert!(out.status.success(), "git {:?} failed", args);
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn hy_fn(name: &str, cc: usize) -> String {
    let mut body = String::new();
    for i in 0..(cc - 1) {
        body.push_str(&format!("  (when x{} (setv y {}))\n", i, i));
    }
    if body.is_empty() {
        body.push_str("  1\n");
    }
    format!("(defk {} [x]\n{})\n", name, body)
}

#[test]
fn doeff_linter_complexity_subcommand_count_and_staged_gate() {
    let dir = TempDir::new().unwrap();
    let root = dir.path();
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["config", "user.name", "Test"]);
    git(root, &["config", "user.email", "test@example.com"]);
    fs::write(
        root.join("repo-charter.toml"),
        "schema = 1\n[budget]\nmax_fn_cc = 25\nmax_type_wmc = 47\nmax_module_wmc = 200\n",
    )
    .unwrap();
    fs::create_dir_all(root.join("pkg")).unwrap();
    fs::write(root.join("pkg/app.hy"), hy_fn("ok-fn", 3)).unwrap();
    git(root, &["add", "."]);
    git(root, &["commit", "-q", "-m", "base"]);

    // `doeff-linter complexity count` succeeds and prints valid JSON summary
    let count_out = Command::new(linter_bin())
        .args(["complexity", "count"])
        .current_dir(root)
        .output()
        .expect("doeff-linter complexity count must run");
    assert!(count_out.status.success(), "stderr: {}", String::from_utf8_lossy(&count_out.stderr));
    let json: serde_json::Value =
        serde_json::from_slice(&count_out.stdout).expect("valid counts JSON");
    assert_eq!(json["summary"]["functions"]["count"], 1);
    assert_eq!(json["summary"]["functions"]["max"], 3);

    // Stage a function exceeding CC 25 -> `doeff-linter complexity gate --staged` exits 1
    fs::write(root.join("pkg/app.hy"), format!("{}{}", hy_fn("ok-fn", 3), hy_fn("too-big", 26)))
        .unwrap();
    git(root, &["add", "pkg/app.hy"]);
    let gate_out = Command::new(linter_bin())
        .args(["complexity", "gate", "--staged"])
        .current_dir(root)
        .output()
        .expect("doeff-linter complexity gate must run");
    assert_eq!(gate_out.status.code(), Some(1));
    let stdout = String::from_utf8_lossy(&gate_out.stdout);
    assert!(stdout.contains("too-big: 0 -> 26"), "stdout: {}", stdout);
}
