# /// script
# requires-python = ">=3.11"
# ///
"""設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW — 着地した版(doeff main 9119bb92)の packages を写し、盲検 A・B の失敗例の再試行と検の感度の変種を 1 か所ずつ当てる。

使い方: make_landed_copies.py <着地した版の packages dir> <exp dir>  — exp の下へ l-a1・l-b・l-bbeh・l-unbind・l-nobind・mut-panic・l-priv を作る。
"""
import shutil
import sys
from pathlib import Path

packages = Path(sys.argv[1])
exp = Path(sys.argv[2])
PROJECT = "packages/doeff-linter/src/project"


def fresh(name: str, with_hy_data: bool = False) -> Path:
    target = exp / name
    shutil.rmtree(target, ignore_errors=True)
    (target / "packages").mkdir(parents=True)
    for pkg in ("doeff-linter", "doeff-indexer"):
        shutil.copytree(packages / pkg, target / "packages" / pkg, ignore=shutil.ignore_patterns("target", ".git"))
    if with_hy_data:
        data = target / "packages/doeff-hy/tests/data"
        data.mkdir(parents=True)
        shutil.copy(packages / "doeff-hy/tests/data/record_field_cases.json", data)
    return target


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, (path.name, old)
    path.write_text(text.replace(old, new))


# 盲検 A の失敗例の再試行: 事実の置き場の配置を 1 行だけ替える(fan の段を 1 つ増やす)— 配置を知るのは store.rs だけのはず。
a1 = fresh("l-a1", with_hy_data=True)
replace_once(
    a1 / PROJECT / "facts_cache/store.rs",
    "objects.join(&name[..2]).join(&name[2..])",
    "objects.join(&name[..2]).join(&name[2..4]).join(&name[4..])",
)

# 盲検 B の失敗例の再試行 1: 差分をそのまま当てる(store の事実の path を作る口は非公開 — 当てた木は組み立てが通らないはず)。
anchor = """    for rel in known.keys().filter(|rel| !present.contains(rel.as_str())) {
        dirty[shard_of(rel)] = true;
    }
"""
block = """    let current: HashMap<&str, Option<ObjectKey>> =
        resolved.iter().filter_map(|one| one.row.as_ref()).map(|row| (row.rel.as_str(), row.object)).collect();
    for (rel, row) in &known {
        let replaced = row.object.filter(|old| current.get(rel.as_str()).copied().flatten() != Some(*old));
        if let Some(old) = replaced {
            let _ = std::fs::remove_file(store::object_path(&place.objects, &old));
        }
    }
"""
b = fresh("l-b")
replace_once(b / PROJECT / "facts_cache.rs", anchor, anchor + block)

# 盲検 B の失敗例の再試行 2: 非公開の守りを外した(object_path を pub(super) に緩めた)木に同じ差分 — 動作の検が第 2 の守りとして赤になるはず。
bbeh = fresh("l-bbeh")
replace_once(bbeh / PROJECT / "facts_cache/store.rs", "fn object_path(objects: &Path, key: &ObjectKey) -> PathBuf {", "pub(super) fn object_path(objects: &Path, key: &ObjectKey) -> PathBuf {")
replace_once(bbeh / PROJECT / "facts_cache.rs", anchor, anchor + block)

# 新しい種類が bind の約束を忘れた変種 1: compute で path を空にし忘れる(値に根の絶対 path が残る — 見張りが置かずに作り直すはず)。
unbind = fresh("l-unbind")
replace_once(unbind / PROJECT / "signatures.rs", '                        keyed.facts.set_path("");\n', "")

# 変種 2: 読んだ後に今の根の path を付け忘れる(別の作業木の path を指さず、path が空のまま答えに出る — 答えの比べが赤になるはず)。
nobind = fresh("l-nobind")
replace_once(nobind / PROJECT / "signatures.rs", "|keyed, rel, _path| keyed.facts.set_path(&root.join(rel).to_string_lossy()),", "|_keyed, _rel, _path| {},")

# 検の感度: 壊れた事実を読んだ時に止まる変種(今は None = 無い物として作り直す)。
panic = fresh("mut-panic")
store = panic / PROJECT / "facts_cache/store.rs"
replace_once(store, "        Format::Json => serde_json::from_slice(bytes).ok(),\n        Format::Compact => bincode::deserialize(bytes).ok(),", '        Format::Json => Some(serde_json::from_slice(bytes).expect("壊れた事実")),\n        Format::Compact => Some(bincode::deserialize(bytes).expect("壊れた事実")),')

# 非公開の確かめ用の写し(名ごとの追記は run_privacy.sh が行う)。
fresh("l-priv")
print("着地した版の写しと変種を作った")
