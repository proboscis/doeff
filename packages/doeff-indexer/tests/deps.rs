use std::collections::HashMap;
use std::fs;
use std::path::Path;

use doeff_indexer::analyze_dependencies;

fn write_file<P: AsRef<Path>>(path: P, contents: &str) {
    if let Some(parent) = path.as_ref().parent() {
        fs::create_dir_all(parent).expect("create parent directories");
    }
    fs::write(path, contents).expect("write test file");
}

#[test]
fn tracks_deps_recursively_with_partials() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    let file_path = root.join("doeff").join("deps_sample.py");

    write_file(
        &file_path,
        r#"from doeff import Ask, Dep, do
from doeff.cache import cache

@do
def fetch_config():
    effect = Dep("config")
    env = yield Ask("environment")
    value = yield effect
    return value, env

@do
def fetch_user():
    user_id = yield Dep("user_id")
    config_program = fetch_config()
    config_value, env = yield config_program
    ask_effect = Ask("locale")
    locale = yield ask_effect
    return user_id, config_value, env, locale

@do
def fetch_wrapped():
    alias = fetch_user
    program = alias()
    result = yield program
    return result

@do
def use_partial():
    partially = fetch_user.partial(user_id=42)
    other_partial = partially.partial()
    first = yield other_partial()
    prebuilt = fetch_user.partial(user_id=99)
    yield prebuilt()
    bound = fetch_user.partial(user_id=1)
    alias = bound
    second = yield alias()
    return first, second

@do
def entrypoint():
    yield fetch_wrapped()
    yield use_partial()


cached_fetch_user = cache()(fetch_user)
"#,
    );

    let mut results = analyze_dependencies(root).expect("analyze dependencies");
    results.sort_by(|a, b| a.qualified_name.cmp(&b.qualified_name));

    let map: HashMap<_, _> = results
        .into_iter()
        .map(|entry| (entry.qualified_name.clone(), entry))
        .collect();

    let fetch_config = map
        .get("doeff.deps_sample.fetch_config")
        .expect("fetch_config result");
    assert_eq!(fetch_config.direct_dep_keys, vec!["config"]);
    assert_eq!(fetch_config.all_dep_keys, vec!["config"]);
    assert_eq!(fetch_config.direct_ask_keys, vec!["environment"]);
    assert_eq!(fetch_config.all_ask_keys, vec!["environment"]);
    assert!(fetch_config.direct_calls.is_empty());
    assert!(fetch_config.unresolved_calls.is_empty());

    let fetch_user = map
        .get("doeff.deps_sample.fetch_user")
        .expect("fetch_user result");
    assert_eq!(fetch_user.direct_dep_keys, vec!["user_id"]);
    assert_eq!(fetch_user.all_dep_keys, vec!["config", "user_id"]);
    assert_eq!(fetch_user.direct_ask_keys, vec!["locale"]);
    assert_eq!(fetch_user.all_ask_keys, vec!["environment", "locale"]);
    assert_eq!(
        fetch_user.direct_calls,
        vec!["doeff.deps_sample.fetch_config".to_string()]
    );

    let fetch_wrapped = map
        .get("doeff.deps_sample.fetch_wrapped")
        .expect("fetch_wrapped result");
    assert!(fetch_wrapped.direct_dep_keys.is_empty());
    assert_eq!(fetch_wrapped.all_dep_keys, vec!["config", "user_id"]);
    assert!(fetch_wrapped.direct_ask_keys.is_empty());
    assert_eq!(fetch_wrapped.all_ask_keys, vec!["environment", "locale"]);
    assert_eq!(
        fetch_wrapped.direct_calls,
        vec!["doeff.deps_sample.fetch_user".to_string()]
    );

    let use_partial = map
        .get("doeff.deps_sample.use_partial")
        .expect("use_partial result");
    assert!(use_partial.direct_dep_keys.is_empty());
    assert_eq!(use_partial.all_dep_keys, vec!["config", "user_id"]);
    assert!(use_partial.direct_ask_keys.is_empty());
    assert_eq!(use_partial.all_ask_keys, vec!["environment", "locale"]);
    assert_eq!(
        use_partial.direct_calls,
        vec!["doeff.deps_sample.fetch_user".to_string()]
    );

    let entrypoint = map
        .get("doeff.deps_sample.entrypoint")
        .expect("entrypoint result");
    assert!(entrypoint.direct_dep_keys.is_empty());
    assert_eq!(entrypoint.all_dep_keys, vec!["config", "user_id"]);
    assert!(entrypoint.direct_ask_keys.is_empty());
    assert_eq!(entrypoint.all_ask_keys, vec!["environment", "locale"]);
    assert_eq!(
        entrypoint.direct_calls,
        vec![
            "doeff.deps_sample.fetch_wrapped".to_string(),
            "doeff.deps_sample.use_partial".to_string(),
        ]
    );

    let cached = map
        .get("doeff.deps_sample.cached_fetch_user")
        .expect("cached alias result");
    assert!(cached.direct_dep_keys.is_empty());
    assert_eq!(cached.all_dep_keys, vec!["config", "user_id"]);
    assert!(cached.direct_ask_keys.is_empty());
    assert_eq!(cached.all_ask_keys, vec!["environment", "locale"]);
    assert_eq!(
        cached.direct_calls,
        vec!["doeff.deps_sample.fetch_user".to_string()]
    );
    assert!(cached.unresolved_calls.is_empty());
}
