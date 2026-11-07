"""Run configuration: defaults < config.toml < REFPIPE_* env vars < CLI flags."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path

from .reasons import DEPRECATED_POLICIES


@dataclass
class Config:
    db_path: str = "output/pipeline.db"
    output_dir: str = "output"
    deprecated_policy: str = "allow_historical"
    strict_columns: bool = False
    engine: str = "vectorized"

    def validate(self) -> "Config":
        if self.deprecated_policy not in DEPRECATED_POLICIES:
            raise ValueError(f"deprecated_policy must be one of {DEPRECATED_POLICIES}")
        if self.engine not in ("vectorized", "rowwise"):
            raise ValueError("engine must be 'vectorized' or 'rowwise'")
        return self

    def as_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | None = "config.toml", **overrides) -> Config:
    cfg = Config()
    if path and Path(path).is_file():
        with open(path, "rb") as fh:
            for k, v in tomllib.load(fh).items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
    env_map = {
        "REFPIPE_DB_PATH": "db_path",
        "REFPIPE_OUTPUT_DIR": "output_dir",
        "REFPIPE_DEPRECATED_POLICY": "deprecated_policy",
    }
    for env, attr in env_map.items():
        if os.environ.get(env):
            setattr(cfg, attr, os.environ[env])
    for k, v in overrides.items():
        if v is not None and hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg.validate()
