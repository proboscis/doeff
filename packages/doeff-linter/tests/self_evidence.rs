//! The design-evidence mechanism passes the rules it enforces: DOEFF212 and DOEFF213 over doeff-linter's own modules of the mechanism (the population declared in
//! self-evidence/architecture.hy) give 0 hits. A type of those modules added or reshaped without its evidence in the sibling <stem>.evidence.toml turns this test red,
//! naming the type.

use serde_json::Value;
use std::path::Path;
use std::process::Command;

#[test]
fn the_mechanisms_own_types_carry_their_design_evidence() {
    let doeff = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("the doeff root exists");
    let config = Path::new(env!("CARGO_MANIFEST_DIR")).join("self-evidence/pyproject.toml");
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log", "--output-format", "editor-json", "--enable", "DOEFF212", "--enable", "DOEFF213", "--config"])
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
        .filter(|v| v["rule"] == "DOEFF212" || v["rule"] == "DOEFF213")
        .map(|v| format!("{} {}", v["key"].as_str().unwrap_or(""), v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(hits.is_empty(), "{} hits:\n{}", hits.len(), hits.join("\n"));
    assert!(report["errors"].as_array().is_none_or(|e| e.is_empty()), "errors: {}", report["errors"]);
}
