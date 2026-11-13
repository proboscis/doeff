//! Builds the list of the repo's Hy files by walking each root only once per run.
//!
//! The rule groups (definitions, smells, bare calls, index, headings and bindings, candidates for rewriting to defk) each walked the repo with `collect_hy_files` --
//! about 8 times per run even for a one-file commit hook, and 0.05 seconds per walk in agora-controllers (about 1200 dirs) (node-h).
//! Premise = this crate is used only by a binary that finishes in one run (there is no resident user), and files neither increase nor decrease during a run.
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use doeff_indexer::hy_index;

static WALKED: OnceLock<Mutex<HashMap<PathBuf, Arc<Vec<PathBuf>>>>> = OnceLock::new();
/// While walking, other threads do not walk the same list again (the read that started collecting first and those that look up the list later do not walk the same tree twice).
static WALKING: Mutex<()> = Mutex::new(());
static BY_MODULE: OnceLock<Mutex<HashMap<PathBuf, Arc<ModuleFiles>>>> = OnceLock::new();

/// Module spelling (`a.b.c`) -> that module's Hy files (path from the root and absolute path). All files that match the same module (`a/b.hy` and `a/b.hyk`).
pub use super::module_probe::ModuleProbe as ModuleFiles;

/// The Hy files under root (the same set and order as `hy_index::collect_hy_files`) -- the same root is not walked from the second time.
pub fn collect(root: &Path) -> Vec<PathBuf> {
    let memo = WALKED.get_or_init(Default::default);
    let known = memo.lock().expect("memo of lists").get(root).map(Arc::clone);
    match known {
        Some(paths) => paths.as_ref().clone(),
        None => walked_once_reading(root, |_| ()).into_iter().map(|(path, ())| path).collect(),
    }
}

/// Attaches the result of `read` on each file to the list from `collect` (with the path from the root -- in list order). `read` is applied during the walk that builds the list, so the wait to open dirs and
/// the wait to read files overlap (if a list for the same root already exists, it does not walk and applies it only to the listed files). A shape for the place where a single-file run reads all of the repo's Hy.
pub fn collect_reading<R: Send>(root: &Path, read: impl Fn(&Path) -> R + Sync) -> Vec<(String, PathBuf, R)> {
    walked_once_reading(root, read)
        .into_iter()
        .filter_map(|(path, result)| super::paths::relative_path(root, &path).map(|rel| (rel, path, result)))
        .collect()
}

fn walked_once_reading<R: Send>(root: &Path, read: impl Fn(&Path) -> R + Sync) -> Vec<(PathBuf, R)> {
    let memo = WALKED.get_or_init(Default::default);
    let known = |memo: &Mutex<HashMap<PathBuf, Arc<Vec<PathBuf>>>>| memo.lock().expect("memo of lists").get(root).map(Arc::clone);
    let _walking = if known(memo).is_some() { None } else { Some(WALKING.lock().unwrap_or_else(PoisonError::into_inner)) };
    // While waiting during the walk, another thread may have finished building the list.
    if let Some(paths) = known(memo) {
        let results = super::bulk_read::fan_out_wide(paths.as_slice(), |path| read(path));
        return paths.iter().cloned().zip(results).collect();
    }
    let found = match walk_reading_with(root, &HY_WALK, &read) {
        Some(found) => found,
        // If the root cannot be opened as a dir (a file, or a nonexistent path), leave it to the walker.
        None => {
            let paths = hy_index::collect_hy_files(root);
            let results = super::bulk_read::fan_out_wide(&paths, |path| read(path));
            paths.into_iter().zip(results).collect()
        }
    };
    memo.lock().expect("memo of lists").insert(root.to_path_buf(), Arc::new(found.iter().map(|(path, _)| path.clone()).collect()));
    found
}

/// How to walk -- the names of dirs to descend into and the extensions of files to pick up (the root is descended into regardless of name. Symlinks are not followed).
pub struct WalkRules {
    /// Whether to descend into the dir of this name.
    pub descend: fn(&str) -> bool,
    /// Whether to pick up a file with this extension.
    pub take: fn(&str) -> bool,
}

/// How to walk the repo's Hy (same as `hy_index::collect_hy_files` -- excluding the names of dirs not descended into, and picking up files with the Hy extension).
pub const HY_WALK: WalkRules = WalkRules { descend: hy_descends, take: hy_takes };

/// Whether the walk of `rules` would pick up the regular file at path `rel` from the root (separator `/`) -- it descends into all intermediate dirs and picks up by extension (so that the lines of git's index are selected
/// consistently with the files seen by walking).
pub fn walks_to(rules: &WalkRules, rel: &str) -> bool {
    let (dirs, name) = rel.rsplit_once('/').unwrap_or(("", rel));
    let taken = Path::new(name).extension().and_then(|extension| extension.to_str()).is_some_and(rules.take);
    taken && dirs.split('/').filter(|dir| !dir.is_empty()).all(rules.descend)
}

fn hy_descends(name: &str) -> bool {
    !hy_index::SKIPPED_DIRS.contains(&name)
}

fn hy_takes(extension: &str) -> bool {
    hy_index::HY_EXTENSIONS.contains(&extension)
}

/// One unit of work -- opening a dir, or applying `read` to a file that was found.
enum Step {
    List(PathBuf),
    Read(PathBuf),
}

/// Builds the list walked with `rules` (with the result of applying `read` to each file -- for `HY_WALK`, the same set and order as `hy_index::collect_hy_files`) by overlapping the wait to open dirs and the wait to read
/// files.
///
/// Walking in order waits on disk once per dir (in agora-controllers about 1200 dirs -- 9 to 15 seconds in a cold working tree on a busy Pod, with 0.1 seconds of CPU).
/// The contents of one dir do not depend on those of other dirs, and reading the files found does not depend on others either, so everything is run as work whose waits overlap (`bulk_read::pipeline`).
/// The names of dirs not descended into, not following symlinks, silently skipping unreadable dirs, and sorting by path at the end are the same as the walker. None when the root cannot be opened as a dir.
pub fn walk_reading_with<R: Send>(root: &Path, rules: &WalkRules, read: &(impl Fn(&Path) -> R + Sync)) -> Option<Vec<(PathBuf, R)>> {
    // If the resident process (`fsmonitor`) remembers the working tree walk, the list is received without opening a single dir (the answer is the same set and order as the walk).
    if let Some(session) = super::fsmonitor::session(root) {
        let paths = session.listing(rules);
        let results = super::bulk_read::fan_out_wide(&paths, |path| read(path));
        return Some(paths.into_iter().zip(results).collect());
    }
    let first = list_dir(root, rules)?;
    let mut found = super::bulk_read::pipeline(super::bulk_read::WIDE_READERS, steps_of(first), |step| match step {
        Step::List(dir) => (list_dir(&dir, rules).map(steps_of).unwrap_or_default(), None),
        Step::Read(path) => {
            let result = read(&path);
            (Vec::new(), Some((path, result)))
        }
    });
    found.sort_by(|a, b| a.0.cmp(&b.0));
    Some(found)
}

/// The work produced by the contents of a dir. Later work is picked up first, so dirs are placed at the back (opening dirs first makes the work that can be lined up grow earlier).
fn steps_of(listing: Listing) -> Vec<Step> {
    listing.files.into_iter().map(Step::Read).chain(listing.dirs.into_iter().map(Step::List)).collect()
}

/// The contents of one dir -- dirs to descend into and Hy files.
struct Listing {
    dirs: Vec<PathBuf>,
    files: Vec<PathBuf>,
}

/// Opens a dir and splits the contents with `rules`. None if it cannot be opened.
fn list_dir(dir: &Path, rules: &WalkRules) -> Option<Listing> {
    let mut listing = Listing { dirs: Vec::new(), files: Vec::new() };
    for entry in std::fs::read_dir(dir).ok()?.filter_map(Result::ok) {
        let Ok(kind) = entry.file_type() else { continue };
        let name = entry.file_name();
        if kind.is_dir() {
            if (rules.descend)(name.to_string_lossy().as_ref()) {
                listing.dirs.push(entry.path());
            }
        } else if kind.is_file() && Path::new(&name).extension().and_then(|extension| extension.to_str()).is_some_and(rules.take) {
            listing.files.push(entry.path());
        }
    }
    Some(listing)
}

/// An index that looks up the Hy files under root by module spelling (the inverse of `names::module_of`). It does not walk the tree and probes only the paths of the requested modules (`ModuleProbe`) --
/// the search range is the same as `collect(root)` (regular Hy files under the root; nothing under dirs that `collect_hy_files` does not descend into is searched). An index for a single-file run to look up only "the file of the
/// module the written file names", and the same root uses the same index (modules that were probed are remembered).
pub fn by_module(root: &Path) -> Arc<ModuleFiles> {
    let memo = BY_MODULE.get_or_init(Default::default);
    let mut memo = memo.lock().expect("memo of module index");
    Arc::clone(
        memo.entry(root.to_path_buf())
            .or_insert_with(|| Arc::new(ModuleFiles::new(root, hy_index::HY_EXTENSIONS, hy_index::SKIPPED_DIRS))),
    )
}

/// The Hy files in the walked range and their claimed names (relative to the root) -- under the root (`collect(root)`, names relative to the root), and under the dirs of the config's `include`
/// (names relative to the root, including `..` = `paths::declared_rel` -- the same form as the key of a declaration file). A single definition of the walked range, which the population of the rules on how definitions are written
/// and the judgment of "outside the target" read. If the same file is in both, only the root side.
pub fn walked(root: &Path, include: &[PathBuf]) -> Vec<(String, PathBuf)> {
    let under_root: Vec<(String, PathBuf)> =
        collect(root).into_iter().filter_map(|path| super::paths::relative_path(root, &path).map(|rel| (rel, path))).collect();
    let seen: BTreeSet<String> = under_root.iter().map(|(rel, _)| rel.clone()).collect();
    let extra: Vec<(String, PathBuf)> = include
        .iter()
        .flat_map(|dir| collect(dir))
        .map(|path| (super::paths::declared_rel(root, &path), path))
        .filter(|(rel, _)| !seen.contains(rel))
        .collect();
    under_root.into_iter().chain(extra).collect()
}

/// The claimed name of one file (Some if within the walked range -- relative to the root under the root, relative including `..` under an `include` dir). None outside the range.
pub fn walked_rel(root: &Path, include: &[PathBuf], path: &Path) -> Option<String> {
    let real = |p: &Path| p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    super::paths::relative_path(root, path)
        .or_else(|| include.iter().any(|dir| real(path).starts_with(real(dir))).then(|| super::paths::declared_rel(root, path)))
}

/// Of the paths named on the command line (files or dirs), returns the Hy files outside the range the linter walks (`walked` -- under the root and the config's `include`).
/// Rules judge only walked files, so naming a file outside the range judges nothing, and staying silent reads as "measured and passed" --
/// the caller names this list and separates it from green. A dir is expanded into the Hy files under it (walked the same way as `collect`). Non-Hy files and nonexistent
/// paths are not returned (Python rules gather files from the named paths and apply to them, so they measure even outside the range). `declaration` (the service and layer
/// declaration files that were read -- e.g. an architecture.hy outside the package root) are judged by the declaration rules, so they are not returned. The returned path is the form under the named path (the caller passes a
/// normalized absolute path -- the same form as the violation's path).
pub fn outside_walk(root: &Path, include: &[PathBuf], named: &[PathBuf], declaration: &[PathBuf]) -> Vec<PathBuf> {
    let walked: BTreeSet<PathBuf> = walked(root, include)
        .into_iter()
        .map(|(_, path)| path)
        .chain(declaration.iter().cloned())
        .filter_map(|path| path.canonicalize().ok())
        .collect();
    let outside: BTreeSet<PathBuf> = named
        .iter()
        .flat_map(|path| hy_index::collect_hy_files(path))
        .filter(|file| !file.canonicalize().is_ok_and(|real| walked.contains(&real)))
        .collect();
    outside.into_iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A list to use in place of the walker (if the root is not a dir, leave it to the walker).
    fn overlapped(root: &Path) -> Vec<PathBuf> {
        match walk_reading_with(root, &HY_WALK, &|_: &Path| ()) {
            Some(found) => found.into_iter().map(|(path, ())| path).collect(),
            None => hy_index::collect_hy_files(root),
        }
    }

    fn tree(files: &[&str]) -> tempfile::TempDir {
        let dir = tempfile::TempDir::new().unwrap();
        for rel in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        dir
    }

    /// The list built by lining up and opening is the same set and the same order as the walker (`hy_index::collect_hy_files`) -- also for dirs not descended into, symlinks, non-Hy files, and dirs whose name ends in `.hy`.
    #[test]
    fn the_overlapped_walk_lists_what_the_plain_walk_lists_in_the_same_order() {
        let dir = tree(&[
            "top.hy",
            "z_last.hyk",
            "a/b/c/deep.hyp",
            "a/b/other.hy",
            "a/notes.txt",
            "app/v1.2/x.hy",
            "app/.venv/lib/y.hy",
            "app/node_modules/n.hy",
            "app/target/z.hy",
            ".git/hooks/h.hy",
            "pkg/__pycache__/c.hy",
            "pkg/real.hy",
            "many/d00/f.hy",
            "many/d01/f.hy",
            "many/d02/f.hy",
            "many/d03/f.hy",
            "many/d04/f.hy",
            "many/d05/f.hy",
            "target",
        ]);
        std::fs::create_dir_all(dir.path().join("folder.hy/inside")).unwrap();
        std::fs::write(dir.path().join("folder.hy/inside/in.hy"), "").unwrap();
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(dir.path().join("a"), dir.path().join("linked")).unwrap();
            std::os::unix::fs::symlink(dir.path().join("top.hy"), dir.path().join("alias.hy")).unwrap();
        }
        let walked = hy_index::collect_hy_files(dir.path());
        assert!(walked.len() >= 10, "not enough test material: {walked:?}");
        assert_eq!(overlapped(dir.path()), walked);
    }

    /// Even if the root has a name that is not descended into, the root itself is descended into (same as the walker). Dirs that cannot be opened are silently skipped.
    #[test]
    fn the_root_is_entered_even_when_its_name_is_skipped_and_unreadable_dirs_are_left_out() {
        let parent = tree(&["target/inner/x.hy", "target/y.hy"]);
        let root = parent.path().join("target");
        assert_eq!(overlapped(&root), hy_index::collect_hy_files(&root));
        assert_eq!(overlapped(&root).len(), 2);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let locked = root.join("inner");
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
            assert_eq!(overlapped(&root), hy_index::collect_hy_files(&root));
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
    }

    /// If the root is not a dir (a file, or a nonexistent path), the walker's answer stands.
    #[test]
    fn a_root_that_is_not_a_directory_gives_what_the_plain_walk_gives() {
        let dir = tree(&["one.hy", "note.txt"]);
        for root in [dir.path().join("one.hy"), dir.path().join("note.txt"), dir.path().join("missing")] {
            assert_eq!(overlapped(&root), hy_index::collect_hy_files(&root), "{root:?}");
        }
    }

    /// The list read while walking is the same set and order as the walker, and each file is read once. The second time (when the list already exists) gives the same answer and again reads each once.
    #[test]
    fn reading_while_walking_reads_every_listed_file_once_in_the_listed_order() {
        let dir = tree(&["b/two.hy", "a/one.hy", "a/deep/three.hyk", "a/.venv/skipped.hy", "c/notes.txt", "top.hyp"]);
        let walked = hy_index::collect_hy_files(dir.path());
        for _ in 0..2 {
            let reads = std::sync::atomic::AtomicUsize::new(0);
            let found = collect_reading(dir.path(), |path| {
                reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                path.file_name().unwrap().to_string_lossy().into_owned()
            });
            assert_eq!(found.iter().map(|(_, path, _)| path.clone()).collect::<Vec<_>>(), walked);
            assert_eq!(reads.load(std::sync::atomic::Ordering::SeqCst), walked.len());
            for (rel, path, name) in &found {
                assert_eq!(path, &dir.path().join(rel));
                assert!(rel.ends_with(name.as_str()));
            }
        }
        assert_eq!(collect(dir.path()), walked);
    }
}
