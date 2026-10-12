# /// script
# requires-python = ">=3.11"
# ///
"""設計検証の検の感度を見る変種(1 か所だけ替える)を、着地した版の写しへ当てる。使い方: apply_mutants.py <exp dir>"""
import sys
from pathlib import Path

exp = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new))


project = "packages/doeff-linter/src/project"
# 変種 1: 表の行の stamp(大きさ・更新時刻)を見ずに、どの行も信じる — 編集した木に古い事実を返す。
replace_once(exp / "mut-stale" / project / "facts_cache.rs", "cached.filter(|row| row.stamp == stamp)", "cached.filter(|_row| true)")
# 変種 2: 置き場の dir を作れない時に止まる(今は黙って置かず、作り直した答えを返す)。
replace_once(
    exp / "mut-fatal" / project / "facts_cache/store.rs",
    "    if std::fs::create_dir_all(dir).is_err() {\n        return 0;\n    }\n    let Some(bytes) = encode_value(value, format)",
    '    std::fs::create_dir_all(dir).expect("置き場の dir を作れない");\n    let Some(bytes) = encode_value(value, format)',
)
# 正常例 S1: 事実の符号化だけを替える(先頭に版の 1 byte を付け、無ければ読めない物として作り直す)— store.rs の 2 つの関数だけ。
store = exp / "s1-encoding" / project / "facts_cache/store.rs"
replace_once(
    store,
    "fn encode_value<T: Serialize>(value: &T, format: Format) -> Option<Vec<u8>> {\n    match format {\n        Format::Json => serde_json::to_vec(value).ok(),\n        Format::Compact => bincode::serialize(value).ok(),\n    }\n}",
    "fn encode_value<T: Serialize>(value: &T, format: Format) -> Option<Vec<u8>> {\n    let mut bytes = vec![0x02];\n    bytes.extend(match format {\n        Format::Json => serde_json::to_vec(value).ok()?,\n        Format::Compact => bincode::serialize(value).ok()?,\n    });\n    Some(bytes)\n}",
)
replace_once(
    store,
    "fn decode_value<T: DeserializeOwned>(bytes: &[u8], format: Format) -> Option<T> {\n    match format {",
    "fn decode_value<T: DeserializeOwned>(bytes: &[u8], format: Format) -> Option<T> {\n    let bytes = bytes.strip_prefix(&[0x02])?;\n    match format {",
)
print("変種を当てた")
