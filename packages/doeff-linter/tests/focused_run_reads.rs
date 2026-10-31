//! 名指しの実行(`-- <path>` — commit の hook が stage した path に当てる形)が読む範囲の検。
//!
//! file 1 つで決まると名乗る規則(`ProjectRule::needs_whole_repo` が偽)だけを当てる名指しの実行は、名指しの file の外を読まなくても
//! 答えが同じ。以前は DOEFF106・131・108 が 1 file の実行でも repo 全体の Hy の索引を組み(agora-controllers で 1 回 CPU 20〜60 秒・
//! 0.8〜1 GB)、commit の hook 1 回の memory の大半を占めていた。読んだ file は Hy の索引の cache(`hy-index.bin.d/`)に path の綴りで
//! 残るので、cache の中の綴りで「何を読んだか」を外から確かめる。

use serde_json::Value;
use std::path::Path;
use std::process::Command;

const RULES: &str = "DOEFF106,DOEFF131,DOEFF108";

fn tags(context: &str, role: &str) -> String {
    format!("(val MODULE-TAGS {{:context \"{}\" :role \"{}\"}})\n", context, role)
}

/// 許可名簿(:world-handlers)と環境の語の宣言を持つ repo(層 core・foundation・entry・service billing)。
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
                     :wraps ["doeff_core_effects.os_file:os-file-handler"])])
(defservice billing "請求" {:layers [core entry]})
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
                + "(import httpx)\n(import doeff_core_effects.os_file [os-file-handler])\n(import doeff_core_effects.file_effects [ReadText])\n\
                   (defk with-host [body] (with-handlers [os-file-handler (fn [] (httpx.AsyncClient))] body))\n\
                   (defk token-in-file [path] (with-handlers [os-file-handler] (ReadText path)))\n",
        ),
        ("app/foundation/sockets.hy", tags("shared", "foundation") + "(import socket)\n(defn open-one [] (socket.socket))\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry")
                + "(import httpx)\n(import doeff_core_effects.os_file [os-file-handler])\n\
                   (defn fetch [] (httpx.get \"http://x\"))\n(defk run [body] (with-handlers [os-file-handler] body))\n",
        ),
        ("app/billing/entry/fake_charge.hy", tags("billing", "entry") + "(defhandler fake-charge [] (Charge [e k] (k 1)))\n"),
        // 層の置き場の外の file(許可名簿の規則は当たる — judge_unplaced_world)。
        ("app/tools/probe.hy", "(import socket)\n(import doeff_core_effects.os_file [os-file-handler])\n(defn dial [] (socket.socket))\n(defk wrap [body] (with-handlers [os-file-handler] body))\n".to_string()),
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

/// binary を root で走らせ(path を名指せば名指しの実行)、3 規則の当たりの鍵を並べる。
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
        .unwrap_or_else(|e| panic!("JSON でない({}): {}\n{}", e, stdout, String::from_utf8_lossy(&output.stderr)));
    let mut keys: Vec<String> = report["violations"].as_array().unwrap().iter().map(|v| v["key"].as_str().unwrap_or("").to_string()).collect();
    keys.sort();
    keys
}

/// cache の Hy の索引(`hy-index.bin.d/` の塊)に綴りが残る file(FILES のうち)。
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
    // 3 規則がどれも当たる repo であること(当たりが無いと比べが空で通る)。
    for rule in ["::DOEFF106::", "::DOEFF131::", "::DOEFF108"] {
        assert!(whole.iter().any(|k| k.contains(rule)), "{} が当たらない: {:?}", rule, whole);
    }
    for rel in FILES {
        let alone = tempfile::TempDir::new().unwrap();
        let expected: Vec<String> = whole.iter().filter(|k| k.starts_with(&format!("{}::", rel))).cloned().collect();
        assert_eq!(hit_keys(dir.path(), alone.path(), &[rel]), expected, "{} の名指しの実行", rel);
        // cache の温まった後の名指しの実行も同じ答え。
        assert_eq!(hit_keys(dir.path(), cache.path(), &[rel]), expected, "{} の名指しの実行(cache あり)", rel);
    }
}

#[test]
fn a_focused_run_of_file_local_rules_reads_only_the_named_file() {
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    hit_keys(dir.path(), cache.path(), &["app/billing/entry/main.hy"]);
    let others: Vec<&str> = indexed_files(cache.path()).into_iter().filter(|rel| *rel != "app/billing/entry/main.hy").collect();
    assert!(others.is_empty(), "名指しの外の file を索引に読んだ: {:?}", others);
}

#[test]
fn a_focused_run_keeps_the_whole_index_cache() {
    // 名指しの実行は、全体の実行が置いた Hy の索引の cache から名指しの外の file を消さない(消えると次の全体の実行が読み直す)。
    let dir = repo();
    let cache = tempfile::TempDir::new().unwrap();
    hit_keys(dir.path(), cache.path(), &[]);
    assert_eq!(indexed_files(cache.path()), FILES.to_vec());
    std::thread::sleep(std::time::Duration::from_millis(20));
    std::fs::write(dir.path().join("app/foundation/sockets.hy"), tags("shared", "foundation") + "(defn open-one [] 1)\n").unwrap();
    hit_keys(dir.path(), cache.path(), &["app/foundation/sockets.hy"]);
    assert_eq!(indexed_files(cache.path()), FILES.to_vec());
}
