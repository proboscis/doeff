"""impls/fast_jev.hy の公開面の型(Python の読み手 = 検)。deff は普通の関数。"""

FAST_JEV_PLUGIN_ID: str
FAST_JEV_MARKETPLACE_NAME: str
FAST_JEV_MARKETPLACE_URL: str
FAST_JEV_KEY_FILE_ENV: str
FAST_JEV_CACHE_TTL_MINUTES: int

def fast_jev_compaction_enabled(settings_text: str | None) -> bool: ...
def fast_jev_home_settings(settings_text: str | None, key_file: str) -> str: ...
def fast_jev_install_command(config_dir: str) -> str: ...
