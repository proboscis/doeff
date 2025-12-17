#[cfg(test)]
mod tests {
    use crate::{build_index, find_all_envs_for_program};
    use std::fs;
    use std::path::Path;

    struct HomeGuard {
        previous: Option<std::ffi::OsString>,
    }

    impl HomeGuard {
        fn set(home: &Path) -> Self {
            let previous = std::env::var_os("HOME");
            std::env::set_var("HOME", home);
            Self { previous }
        }
    }

    impl Drop for HomeGuard {
        fn drop(&mut self) {
            if let Some(value) = self.previous.take() {
                std::env::set_var("HOME", value);
            } else {
                std::env::remove_var("HOME");
            }
        }
    }

    fn write_file(path: &Path, content: &str) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("failed to create parent directories");
        }
        fs::write(path, content).expect("failed to write file");
    }

    #[test]
    fn env_chain_includes_only_module_hierarchy_envs() {
        let tmp = tempfile::tempdir().expect("failed to create temp dir");

        // Avoid picking up a real ~/.doeff.py from the developer machine.
        let _home_guard = HomeGuard::set(tmp.path());

        write_file(
            &tmp.path().join("placement/__init__.py"),
            r#"
from doeff import Program

# doeff: default
default_env: Program[dict] = Program.pure({"root": 1})

def pure_interpreter(program: Program):
    '''# doeff: interpreter, default'''
    return program
"#,
        );

        write_file(
            &tmp.path().join("placement/analysis/__init__.py"),
            r#"
from doeff import Program

# doeff: default
default_env: Program[dict] = Program.pure({"analysis": 1})
"#,
        );

        write_file(
            &tmp.path().join("placement/analysis/entrypoints.py"),
            r#"
from doeff import Program

p_entrypoint: Program[int] = Program.pure(1)

# doeff: default
default_env: Program[dict] = Program.pure({"entrypoints": 1})
"#,
        );

        // Sibling module envs (should NOT be included)
        write_file(
            &tmp.path().join("placement/labelstudio/__init__.py"),
            r#"
from doeff import Program

# doeff: default
default_env: Program[dict] = Program.pure({"labelstudio": 1})
"#,
        );
        write_file(
            &tmp.path().join("placement/letter_func_replay/__init__.py"),
            r#"
from doeff import Program

# doeff: default
default_env: Program[dict] = Program.pure({"replay": 1})
"#,
        );

        let index = build_index(tmp.path()).expect("build_index failed");
        let result = find_all_envs_for_program(
            &index.entries,
            "placement.analysis.entrypoints.p_entrypoint",
        );

        let qualified_names: Vec<&str> = result
            .env_chain
            .iter()
            .map(|entry| entry.qualified_name.as_str())
            .collect();

        assert_eq!(
            qualified_names,
            vec![
                "placement.default_env",
                "placement.analysis.default_env",
                "placement.analysis.entrypoints.default_env",
            ]
        );
        assert!(!qualified_names.contains(&"placement.pure_interpreter"));
    }
}
