# 型検査の展開の保存が照らす POST_PROCESSING の閉包の digest を出す(後処理の変更が保存を無効にするかを確かめるため)
import hashlib, os, sys
from doeff_hy_bytecode_guard.macro_use import value_digest
from doeff_hy_bytecode_guard.records import ValueReference

def sha256_of(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None

d = value_digest(ValueReference("doeff_hy.static_check", "POST_PROCESSING"), sha256_of)
print("digest", None if d is None else d.digest)
