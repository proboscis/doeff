//! Test of the scope read by a named run (`-- <path>` — the form where the commit hook applies to staged paths).
//!
//! A named run that applies only rules claiming to be decided per file (`ProjectRule::needs_whole_repo` is false) gives the same answer without reading outside the named file.
//! Previously DOEFF106, 131, and 108 built the Hy index of the whole repo even for a one-file run (once in agora-controllers: CPU 20-60 s,
//! 0.8-1 GB), taking most of the memory of one commit hook run. The files read remain in the Hy index cache (`hy-index.bin.d/`) by path spelling,
//! so "what was read" is checked from outside using the spellings in the cache.

use serde_json::Value;
use std::path::Path;
use std::process::Command;

const RULES: &str = "DOEFF106,DOEFF131,DOEFF108";

fn tags(context: &str, role: &str) -> String {
    format!("(val MODULE-TAGS {{:context \"{}\" :role \"{}\"}})\n", context, role)
}

/// A repo with an allow-list (:world-handlers) and environment-word declarations (layers core, foundation, entry, service billing).
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let architecture = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])
           (layer foundation :roles [foundation] :imports [foundation])
           (layer entry :roles [entry] :imports [core foundation entry])]
  :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file]
                     :wraps ["doeff_core_effects._os_file:os-file-handler"])])
(defservice billing "Billing" {:layers [core entry]})
"#;
    std::fs::write(dir.path().join("architecture.hy"), architecture).unwrap();
    let config = format!(
        "[tool.doeff-linter]\nenable = [{}]\n\n[tool.doeff-linter.environment_names]\nwords = [\"fake\", \"local\", \"wire\"]\npaths = [\"app/billing\"]\n",
        RULES.split(',').map(|r| format!("\"{}\"", r)).collect::<Vec<_>>().join(", ")
    );
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation")
                + "(import httpx)\n(import doeff_core_effects._os_file [os-file-handler])\n(import doeff_core_effects._file_effects [ReadText])\n\
                   (defk with-host [body] (with-handlers [os-file-handler (fn [] (httpx.AsyncClient))] body))\n\
                   (defk token-in-file [path] (with-handlers [os-file-handler] (ReadText path)))\n",
        ),
        ("app/foundation/sockets.hy", tags("shared", "foundation") + "(import socket)\n(defn open-one [] (socket.socket))\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry")
                + "(import httpx)\n(import doeff_core_effects._os_file [os-file-handler])\n\
                   (defn fetch [] (httpx.get \"http://x\"))\n(defk run [body] (with-handlers [os-file-handler] body))\n",
        ),
        ("app/billing/entry/fake_charge.hy", tags("billing", "entry") + "(defhandler fake-charge [] (Charge [e k] (k 1)))\n"),
        // A file outside the layer locations (the allow-list rule hits — judge_unplaced_world).
        ("app/tools/probe.hy", "(import socket)\n(import doeff_core_effects._os_file [os-file-handler])\n(defn dial [] (socket.socket))\n(defk wrap [body] (with-handlers [os-file-handler] body))\n".to_string()),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

const FILES: [&str; 5] =
    ["app/foundation/host.hy", "app/foundation/sockets.hy", "app/billing/entry/main.hy", "app/billing/entry/fake_charge.hy", "app/tools/probe.hy"];

/// Runs the binary at root (naming a path makes it a named run) and lists the keys of the hits of the 3 rules.
fn hit_keys(root: &Path, cache: &Path, paths: &[&str]) -> Vec<String> {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--enable", RULES, "--output-format", "editor-json", "--no-log", "--"])
        .args(paths)
        .current_dir(root)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .env("DOEFF_LINTER_CACHE_DIR", cache).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let report: Value = serde_json::from_str(&stdout)
        .unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, String::from_utf8_lossy(&output.stderr)));
    let mut keys: Vec<String> = report["violations"].as_array().unwrap().iter().map(|v| v["key"].as_str().unwrap_or("").to_string()).collect();
    keys.sort();
    keys
}

/// Files (among FILES) whose spelling remains in the cache's Hy index (chunks of `hy-index.bin.d/`).
fn indexed_files(cache: &Path) -> Vec<&'static str> {
    let shards: Vec<Vec<u8>> = walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.path().parent().and_then(|p| p.file_name()).is_some_and(|n| n == "hy-index.bin.d"))
        .map(|e| std::fs::read(e.path()).unwrap())
        .collect();
    FILES.into_iter().filter(|rel| shards.iter().any(|bytes| bytes.windows(rel.len()).any(|w| w == rel.as_bytes()))).collect()
}

#[test]
fn a_focused_run_answers_like_the_whole_run_for_that_file() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    let whole = hit_keys(dir.path(), cache.path(), &[]);
    // The repo must be one where all 3 rules hit (if there are no hits the comparison passes vacuously empty).
    for rule in ["::DOEFF106::", "::DOEFF131::", "::DOEFF108"] {
        assert!(whole.iter().any(|k| k.contains(rule)), "{} does not hit: {:?}", rule, whole);
    }
    for rel in FILES {
        let alone = tempfile::TempDir::new().unwrap();
        let expected: Vec<String> = whole.iter().filter(|k| k.starts_with(&format!("{}::", rel))).cloned().collect();
        assert_eq!(hit_keys(dir.path(), alone.path(), &[rel]), expected, "named run of {}", rel);
        // The named run after the cache has warmed gives the same answer.
        assert_eq!(hit_keys(dir.path(), cache.path(), &[rel]), expected, "named run of {} (with cache)", rel);
    }
}

#[test]
fn a_focused_run_of_file_local_rules_reads_only_the_named_file() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    hit_keys(dir.path(), cache.path(), &["app/billing/entry/main.hy"]);
    let others: Vec<&str> = indexed_files(cache.path()).into_iter().filter(|rel| *rel != "app/billing/entry/main.hy").collect();
    assert!(others.is_empty(), "files outside the named ones were read into the index: {:?}", others);
}

#[test]
fn a_focused_run_keeps_the_whole_index_cache() {
    // A named run does not remove files outside the named ones from the Hy index cache placed by a whole run (if removed, the next whole run re-reads them).
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    hit_keys(dir.path(), cache.path(), &[]);
    assert_eq!(indexed_files(cache.path()), FILES.to_vec());
    std::thread::sleep(std::time::Duration::from_millis(20));
    std::fs::write(dir.path().join("app/foundation/sockets.hy"), tags("shared", "foundation") + "(defn open-one [] 1)\n").unwrap();
    hit_keys(dir.path(), cache.path(), &["app/foundation/sockets.hy"]);
    assert_eq!(indexed_files(cache.path()), FILES.to_vec());
}
