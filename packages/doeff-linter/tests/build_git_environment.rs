//! 本物の build.rs を Cargo で動かし、呼び手の repo を版へ埋めないことを確かめる(#1480)。
//! 依存の無い小さな crate なので、linter 全体を反例ごとに組み直さない。

use std::path::{Path, PathBuf};
use std::process::Command;

fn command(name: &str) -> Command {
    let mut command = Command::new(name);
    // 検の準備自体は、検を起動した hook の repo や版の上書きを引き継がない。
    for (key, _) in std::env::vars_os() {
        if key.to_str().is_some_and(|key| key.starts_with("GIT_")) {
            command.env_remove(key);
        }
    }
    command.env_remove("DOEFF_LINTER_BUILD_COMMIT");
    command
}

fn output(command: &mut Command) -> String {
    let output = command.output().expect("子processを起動できる");
    assert!(
        output.status.success(),
        "{command:?}\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_string()
}

struct Repo {
    dir: tempfile::TempDir,
    crate_dir: PathBuf,
    head: String,
}

impl Repo {
    fn new(identity: &str) -> Self {
        let dir = tempfile::TempDir::new().unwrap();
        let crate_dir = dir.path().join("packages/doeff-linter");
        std::fs::create_dir_all(crate_dir.join("src")).unwrap();
        std::fs::create_dir_all(dir.path().join("packages/doeff-indexer/src")).unwrap();
        std::fs::write(dir.path().join("identity"), identity).unwrap();
        std::fs::write(dir.path().join(".gitignore"), "target/\n").unwrap();
        std::fs::write(crate_dir.join("build.rs"), include_str!("../build.rs")).unwrap();
        std::fs::write(
            crate_dir.join("Cargo.toml"),
            "[package]\nname = \"build-commit-probe\"\nversion = \"0.0.0\"\nedition = \"2021\"\n",
        )
        .unwrap();
        std::fs::write(
            crate_dir.join("src/main.rs"),
            "fn main() { println!(\"{}\", env!(\"DOEFF_LINTER_COMMIT\")); }\n",
        )
        .unwrap();
        std::fs::write(
            dir.path().join("packages/doeff-indexer/src/lib.rs"),
            "// clean\n",
        )
        .unwrap();
        output(
            command("git")
                .arg("-C")
                .arg(dir.path())
                .args(["init", "--quiet"]),
        );
        // 一時repoには、この検専用の空のhooks directoryを使う。
        let hooks = dir.path().join(".git/fixture-hooks");
        std::fs::create_dir(&hooks).unwrap();
        output(
            command("git")
                .arg("-C")
                .arg(dir.path())
                .args(["config", "core.hooksPath"])
                .arg(hooks),
        );
        output(command("git").arg("-C").arg(dir.path()).args(["add", "."]));
        output(command("git").arg("-C").arg(dir.path()).args([
            "-c",
            "user.name=Build fixture",
            "-c",
            "user.email=build-fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            identity,
        ]));
        let head = output(
            command("git")
                .arg("-C")
                .arg(dir.path())
                .args(["rev-parse", "HEAD"]),
        );
        Self {
            dir,
            crate_dir,
            head,
        }
    }

    fn build(&self, foreign: &Repo, given: Option<&str>) -> (String, String) {
        let target = self.dir.path().join("target");
        let foreign_git = foreign.dir.path().join(".git");
        let mut cargo = command("cargo");
        cargo
            .current_dir(&self.crate_dir)
            .args(["build", "--quiet", "--offline"])
            .env("CARGO_TARGET_DIR", &target)
            .env("GIT_DIR", &foreign_git)
            .env("GIT_WORK_TREE", foreign.dir.path())
            .env("GIT_INDEX_FILE", foreign_git.join("index"))
            .env("GIT_COMMON_DIR", &foreign_git)
            .env("GIT_OBJECT_DIRECTORY", foreign_git.join("objects"));
        if let Some(given) = given {
            cargo.env("DOEFF_LINTER_BUILD_COMMIT", given);
        }
        output(&mut cargo);
        let stamp = output(&mut Command::new(target.join("debug").join(format!(
            "build-commit-probe{}",
            std::env::consts::EXE_SUFFIX
        ))));
        let directives = std::fs::read_dir(target.join("debug/build"))
            .unwrap()
            .map(|entry| entry.unwrap().path().join("output"))
            .find(|path| path.is_file())
            .expect("Cargoがbuild.rsの出力を残す");
        (stamp, std::fs::read_to_string(directives).unwrap())
    }

    fn modify_indexer(&self) {
        std::fs::write(
            self.dir.path().join("packages/doeff-indexer/src/lib.rs"),
            "// modified\n",
        )
        .unwrap();
    }
}

fn assert_watches_repo(directives: &str, root: &Path) {
    let git_dir = output(
        command("git")
            .arg("-C")
            .arg(root)
            .args(["rev-parse", "--absolute-git-dir"]),
    );
    for name in ["HEAD", "index", "logs/HEAD"] {
        assert!(
            directives.contains(&format!(
                "cargo:rerun-if-changed={}",
                Path::new(&git_dir).join(name).display()
            )),
            "{directives}"
        );
    }
}

#[test]
fn foreign_git_environment_does_not_replace_head_dirty_state_or_watched_paths() {
    let own = Repo::new("doeff fixture");
    let foreign = Repo::new("consumer fixture");
    assert_ne!(own.head, foreign.head);
    foreign.modify_indexer();
    let (stamp, directives) = own.build(&foreign, None);
    assert_eq!(stamp, own.head);
    assert_watches_repo(&directives, own.dir.path());
    assert!(
        !directives.contains(&foreign.dir.path().display().to_string()),
        "{directives}"
    );
}

#[test]
fn own_indexer_changes_remain_dirty_under_foreign_git_environment() {
    let own = Repo::new("doeff fixture");
    let foreign = Repo::new("consumer fixture");
    own.modify_indexer();
    let (stamp, _) = own.build(&foreign, None);
    assert_eq!(stamp, format!("{}+dirty", own.head));
}

#[test]
fn explicit_build_commit_still_takes_precedence_and_watches_own_repo() {
    let own = Repo::new("doeff fixture");
    let foreign = Repo::new("consumer fixture");
    let given = "0123456789abcdef0123456789abcdef01234567";
    let (stamp, directives) = own.build(&foreign, Some(given));
    assert_eq!(stamp, given);
    assert_watches_repo(&directives, own.dir.path());
}
