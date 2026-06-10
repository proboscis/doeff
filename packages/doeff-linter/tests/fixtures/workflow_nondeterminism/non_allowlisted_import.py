# doeff: workflow
import yaml


def parse_config(raw: str) -> object:
    return yaml.safe_load(raw)
