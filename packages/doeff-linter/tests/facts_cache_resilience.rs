//! End-to-end resilience tests for `facts_cache` defaults, copied-binary sharing, fallbacks, and partial invalidation.

use std::path::Path;
use std::process::Command;
use std::time::{Duration, SystemTime};

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF106", "DOEFF107", "DOEFF146", "DOEFF148", "DOEFF150", "DOEFF151", "DOEFF161", "DOEFF209"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const ARCHITECTURE_V1: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "Core") (layer foundation :summary "Foundation")]
  :foundation foundation
  :retired-words [
    (retired-words "legacy-word" :words ["OLD_TOKEN"] :files ["app/**"] :instead "NEW_TOKEN")
    (retired-words "legacy-name" :words ["legacy_fn"] :in names :files ["app/**"] :instead "new_fn")
  ]
  :retired-calls [
    (retired-calls "clock" :calls ["Now"] :files ["app/**"] :instead "GetTime")
  ]
  :confined-spellings [
    (confined-spelling "confined-a" :patterns ["FORBIDDEN_ROUTE"] :files ["app/**"] :except ["app/foundation/**"] :why "confined")
    (confined-spelling "confined-b" :patterns ["SECRET_VERB"] :files ["app/**"] :except [] :why "confined")
  ]
  :single-point-vocabulary [
    (vocabulary-scope "vocab-a" :patterns [r"\bSTATE_RAW\b"] :files ["app/**"] :except ["app/foundation/**"] :instead "use helper")
  ]
  :counted-spellings [
    (counted-spelling "count-a" :pattern "COUNT_MARKER" :files ["app/**"] :count 1 :why "single point")
  ]
  :business-fakes {:production ["app/**"] :simulation ["sim/**"] :tests ["tests/**"] :business-modules ["app.core"]})
"#;

const ARCHITECTURE_V2: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "Core") (layer foundation :summary "Foundation")]
  :foundation foundation
  :retired-words [
    (retired-words "legacy-word" :words ["OLD_TOKEN" "EXTRA_TOKEN"] :files ["app/**"] :instead "NEW_TOKEN")
    (retired-words "legacy-name" :words ["legacy_fn"] :in names :files ["app/**"] :instead "new_fn")
  ]
  :retired-calls [
    (retired-calls "clock" :calls ["Now"] :files ["app/**"] :instead "GetTime")
  ]
  :confined-spellings [
    (confined-spelling "confined-a" :patterns ["FORBIDDEN_ROUTE"] :files ["app/**"] :except ["app/foundation/**"] :why "confined")
    (confined-spelling "confined-b" :patterns ["SECRET_VERB"] :files ["app/**"] :except [] :why "confined")
  ]
  :single-point-vocabulary [
    (vocabulary-scope "vocab-a" :patterns [r"\bSTATE_RAW\b"] :files ["app/**"] :except ["app/foundation/**"] :instead "use helper")
  ]
  :counted-spellings [
    (counted-spelling "count-a" :pattern "COUNT_MARKER" :files ["app/**"] :count 1 :why "single point")
  ]
  :business-fakes {:production ["app/**"] :simulation ["sim/**"] :tests ["tests/**"] :business-modules ["app.core"]})
"#;

fn make_repo(dir: &Path) {
    std::fs::create_dir_all(dir).unwrap();
    std::fs::write(dir.join("pyproject.toml"), CONFIG).unwrap();
    std::fs::write(dir.join("architecture.hy"), ARCHITECTURE_V1).unwrap();
    std::fs::create_dir_all(dir.join("app/core")).unwrap();
    std::fs::create_dir_all(dir.join("app/foundation")).unwrap();
    std::fs::write(
        dir.join("app/core/a.hy"),
        "(defk legacy_fn []\n  (setv x \"OLD_TOKEN\")\n  (setv r \"FORBIDDEN_ROUTE\")\n  (when (= s STATE_RAW) (Now))\n  (while True (<- (Delay 5.0))))\n",
    )
    .unwrap();
    std::fs::write(
        dir.join("app/core/b.hy"),
        "(defk clean_fn []\n  (setv m \"COUNT_MARKER\")\n  (setv e \"EXTRA_TOKEN\")\n  1)\n",
    )
    .unwrap();
    std::fs::write(dir.join("app/foundation/c.hy"), "(defk ok [] 1)\n").unwrap();
}

fn run_bin(bin: &Path, root: &Path, configure: impl FnOnce(&mut Command)) -> (String, String) {
    let mut cmd = Command::new(bin);
    cmd.args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .env_remove("DOEFF_LINTER_TIMING")
        .env_remove("DOEFF_LINTER_CACHE_DIR");
    configure(&mut cmd);
    let out = cmd.output().unwrap();
    assert!(
        out.status.code().is_some_and(|c| c <= 1),
        "unexpected exit {:?}: stderr={}",
        out.status.code(),
        String::from_utf8_lossy(&out.stderr)
    );
    (
        String::from_utf8(out.stdout).unwrap(),
        String::from_utf8(out.stderr).unwrap(),
    )
}

fn timing_count(stderr: &str, stage: &str, field: &str) -> Option<usize> {
    let prefix = format!("doeff-linter-timing: facts-cache.{stage} {field}=");
    stderr
        .lines()
        .find_map(|line| line.strip_prefix(&prefix)?.trim().parse::<usize>().ok())
}

#[test]
fn copied_binary_with_different_mtime_hits_shared_objects_for_all_new_stages() {
    let dir = tempfile::tempdir().unwrap();
    let repo = dir.path().join("repo");
    let cache = dir.path().join("cache");
    make_repo(&repo);

    let orig = Path::new(env!("CARGO_BIN_EXE_doeff-linter"));
    let bin_a = dir.path().join("venv_a/doeff-linter");
    let bin_b = dir.path().join("venv_b/doeff-linter");
    std::fs::create_dir_all(bin_a.parent().unwrap()).unwrap();
    std::fs::create_dir_all(bin_b.parent().unwrap()).unwrap();
    std::fs::copy(orig, &bin_a).unwrap();
    std::fs::copy(orig, &bin_b).unwrap();
    std::fs::File::options()
        .write(true)
        .open(&bin_b)
        .unwrap()
        .set_modified(SystemTime::now() + Duration::from_secs(120))
        .unwrap();

    // 1. Cold run with bin_a populates cache.
    let (out_a, err_a) = run_bin(&bin_a, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache).env("DOEFF_LINTER_TIMING", "1");
    });
    for stage in ["retired", "confined-spellings", "single-point-vocabulary", "counted-spellings", "timed-refetch", "hy-index"] {
        assert_eq!(timing_count(&err_a, stage, "hits"), Some(0), "{stage} cold hits:\n{err_a}");
        assert!(timing_count(&err_a, stage, "misses").unwrap_or(0) > 0, "{stage} cold misses:\n{err_a}");
    }

    // 2. Warm run with copied bin_b (different path & mtime) hits cache with 0 misses and identical output.
    let (out_b, err_b) = run_bin(&bin_b, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache).env("DOEFF_LINTER_TIMING", "1");
    });
    assert_eq!(out_a, out_b);
    for stage in ["retired", "confined-spellings", "single-point-vocabulary", "counted-spellings", "timed-refetch", "hy-index"] {
        assert_eq!(timing_count(&err_b, stage, "misses"), Some(0), "{stage} warm misses:\n{err_b}");
        assert!(timing_count(&err_b, stage, "hits").unwrap_or(0) > 0, "{stage} warm hits:\n{err_b}");
    }
}

#[test]
fn cache_fallbacks_warn_on_stderr_without_timing_and_preserve_exact_violations() {
    let dir = tempfile::tempdir().unwrap();
    let repo = dir.path().join("repo");
    let cache = dir.path().join("cache");
    make_repo(&repo);
    let bin = Path::new(env!("CARGO_BIN_EXE_doeff-linter"));

    // Reference run with working cache.
    let (cached_out, _) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache);
    });
    let (nocache_out, _) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_NO_CACHE", "1");
    });
    assert_eq!(cached_out, nocache_out, "cached output must match uncached output");

    // (2a) Cache dir completely absent (DOEFF_LINTER_CACHE_DIR, XDG_CACHE_HOME, HOME unset) -> 1-line stderr notice without DOEFF_LINTER_TIMING, same violations.
    let (out_absent, err_absent) = run_bin(bin, &repo, |c| {
        c.env_remove("XDG_CACHE_HOME").env_remove("HOME");
    });
    assert_eq!(out_absent, cached_out);
    assert!(
        err_absent.contains("doeff-linter: facts cache:"),
        "missing stderr fallback notice when cache dir is unset; stderr={err_absent:?}"
    );

    // (2b) Unwritable cache dir (a regular file where a dir is expected) -> 1-line stderr notice without DOEFF_LINTER_TIMING, same violations.
    let unwritable = dir.path().join("unwritable-file");
    std::fs::write(&unwritable, b"not a dir").unwrap();
    let (out_unwritable, err_unwritable) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &unwritable);
    });
    assert_eq!(out_unwritable, cached_out);
    assert!(
        err_unwritable.contains("doeff-linter: facts cache:") && err_unwritable.contains("not writable"),
        "missing stderr fallback notice when cache dir is unwritable; stderr={err_unwritable:?}"
    );

    // (3a) Format version mismatch in objects/format-version -> 1-line stderr notice without DOEFF_LINTER_TIMING, same violations.
    let version_file = cache.join("objects/format-version");
    let saved_version = std::fs::read_to_string(&version_file).unwrap();
    std::fs::write(&version_file, "999\n").unwrap();
    let (out_bad_ver, err_bad_ver) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache);
    });
    assert_eq!(out_bad_ver, cached_out);
    assert!(
        err_bad_ver.contains("doeff-linter: facts cache:") && err_bad_ver.contains("format version mismatch"),
        "missing stderr fallback notice on format version mismatch; stderr={err_bad_ver:?}"
    );
    std::fs::write(&version_file, saved_version).unwrap();

    // (3b) Corrupt object file inside objects/ -> stderr notice without DOEFF_LINTER_TIMING, same violations.
    let first_object = walkdir::WalkDir::new(cache.join("objects/retired.bin"))
        .into_iter()
        .filter_map(Result::ok)
        .find(|e| e.file_type().is_file())
        .expect("expected cached retired object")
        .into_path();
    std::fs::write(&first_object, b"\xff\xfe\xfdcorrupt").unwrap();
    let (out_corrupt, err_corrupt) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache);
    });
    assert_eq!(out_corrupt, cached_out);
    assert!(
        err_corrupt.contains("doeff-linter: facts cache:") && err_corrupt.contains("corrupt"),
        "missing stderr fallback notice on corrupt object; stderr={err_corrupt:?}"
    );

    // (4a) Editing 1 file misses ONLY that 1 file across retired / confined-spellings / timed-refetch.
    std::thread::sleep(Duration::from_millis(20));
    std::fs::write(repo.join("app/foundation/c.hy"), "(defk ok [] 2)\n").unwrap();
    let (out_edited, err_edited) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache).env("DOEFF_LINTER_TIMING", "1");
    });
    let (out_edited_nocache, _) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_NO_CACHE", "1");
    });
    assert_eq!(out_edited, out_edited_nocache);
    for stage in ["retired", "timed-refetch"] {
        assert_eq!(timing_count(&err_edited, stage, "misses"), Some(1), "{stage} should miss only 1 edited file:\n{err_edited}");
        assert_eq!(timing_count(&err_edited, stage, "hits"), Some(2), "{stage} should hit 2 unchanged files:\n{err_edited}");
    }

    // (4b) Changing group definition in architecture.hy invalidates cached group results and matches uncached violations.
    std::fs::write(repo.join("architecture.hy"), ARCHITECTURE_V2).unwrap();
    let (out_v2_cached, _) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_CACHE_DIR", &cache);
    });
    let (out_v2_nocache, _) = run_bin(bin, &repo, |c| {
        c.env("DOEFF_LINTER_NO_CACHE", "1");
    });
    assert_eq!(out_v2_cached, out_v2_nocache);
    assert_ne!(out_v2_cached, out_edited, "adding EXTRA_TOKEN to retired-words must report new violation");
}
