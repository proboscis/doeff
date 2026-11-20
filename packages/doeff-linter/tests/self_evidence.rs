//! The design-evidence mechanism passes the rule it enforces: DOEFF212 over doeff-linter's own modules of the mechanism (the population declared in
//! self-evidence/architecture.hy) gives 0 hits, and every type of those modules is judged. A type added or reshaped without its evidence in the sibling
//! <stem>.evidence.toml, or evidence whose review is not of the declared spelling, turns this test red, naming the type. (DOEFF213 reads .hy files only and is not
//! applied to these Rust modules.)

use serde_json::Value;
use std::path::Path;
use std::process::Command;

#[test]
fn the_mechanisms_own_types_carry_their_design_evidence() {
    let doeff = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("the doeff root exists");
    let config = Path::new(env!("CARGO_MANIFEST_DIR")).join("self-evidence/pyproject.toml");
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log", "--output-format", "editor-json", "--enable", "DOEFF212", "--config"])
        .arg(&config)
        .current_dir(&doeff)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|error| panic!("not JSON ({}): {}\n{}", error, stdout, String::from_utf8_lossy(&output.stderr)));
    let judged: Vec<&str> = report["judged_rules"].as_array().expect("judged rules").iter().filter_map(Value::as_str).collect();
    assert!(judged.contains(&"DOEFF212"), "DOEFF212 is not judged: {}", report);
    let hits: Vec<String> = report["violations"]
        .as_array()
        .expect("violations list")
        .iter()
        .filter(|v| v["rule"] == "DOEFF212")
        .map(|v| format!("{} {}", v["key"].as_str().unwrap_or(""), v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(hits.is_empty(), "{} hits:\n{}", hits.len(), hits.join("\n"));
    assert!(report["errors"].as_array().is_none_or(|e| e.is_empty()), "errors: {}", report["errors"]);

    // Every type is judged: the reader finds the 25 types in the declared population (a module dropped from it, or a reader that reads nothing, gives fewer — and
    // DOEFF212 would then pass by judging nothing).
    let shapes = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).args(["--no-log", "--print-existing-shapes", "--config"]).arg(&config).current_dir(&doeff).output().unwrap();
    let ledger = String::from_utf8_lossy(&shapes.stdout);
    let read = ledger.lines().filter(|line| line.starts_with("\"packages/doeff-linter/src/project/")).count();
    assert_eq!(read, TYPES, "the population's types as read:\n{}{}", ledger, String::from_utf8_lossy(&shapes.stderr));
}

/// The top-level structs and enums of the three modules (tests excluded).
const TYPES: usize = 25;
