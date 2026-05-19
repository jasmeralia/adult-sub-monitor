from pathlib import Path
from typing import Any

import yaml
from yaml import SafeLoader, load

from adult_sub_monitor.models import AppConfig


def load_config(path: Path) -> AppConfig:
    try:
        with path.open(encoding="utf-8") as config_file:
            config_data: Any = load(config_file, Loader=SafeLoader)
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML config file {path}: {exc}") from exc

    return AppConfig.model_validate(config_data)
