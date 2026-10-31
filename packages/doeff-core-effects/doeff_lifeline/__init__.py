"""起こした process が終わったら、その子も終わる — StartProcess の約束の本体の置き場。標準ライブラリだけで書く。

この package は公開の名を持たない(ADR-DOE-API-001 R1・R5 — 名は effect でも handler でもないので内側に置く)。
本体は内側の module _watch に在り、読むのは doeff の中の 2 つだけ: StartProcess の答え手(doeff_core_effects の os_process.hy — Lifeline)と
doeff-cluster の worker の shim(watched_until_eof と止める相手の型)。

置き場は doeff-core-effects の配布の中の、doeff_core_effects と別の top-level の package: doeff_core_effects の package を読むと起動に約 100 ms
乗る(2026-10-07 の実測)ので、job ごとに起こす shim がこの本体だけを読めるように分けた。
"""

if __name__ == "__main__":
    # 前の版の Lifeline は見張りをこの file(`python -I -S -B <この file>`)で起こす。版をまたいで動く process の見張りも起きるように、
    # 本体へ渡す(-I では package を import できないので file として走らせる)。
    import pathlib
    import runpy

    runpy.run_path(str(pathlib.Path(__file__).with_name("_watch.py")), run_name="__main__")
