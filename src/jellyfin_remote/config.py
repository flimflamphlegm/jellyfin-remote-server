"""Settings come from config.json in the project root, then env vars override."""
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.json"

ENV_OVERRIDES = {
    "jellyfin_url": "JELLYFIN_URL",
    "api_key": "JELLYFIN_API_KEY",
    "port": "JELLYFIN_REMOTE_PORT",
    "device_filter": "JELLYFIN_DEVICE_FILTER",
    "user_filter": "JELLYFIN_USER_FILTER",
}


@dataclass
class Config:
    jellyfin_url: str = "http://localhost:8096"
    api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8097
    # Optional: only follow sessions whose device name or client contains this text.
    device_filter: str = ""
    # Optional: only follow sessions for this Jellyfin user name (exact, case-insensitive).
    user_filter: str = ""
    # Players that never report position or pause to Jellyfin; the page hides the timer for these.
    no_progress_clients: list = field(default_factory=lambda: ["cliamp"])


def load_config(path=CONFIG_PATH):
    data = {}
    if Path(path).exists():
        data = json.loads(Path(path).read_text())
    for key, env_name in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value:
            data[key] = value

    known = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg.port = int(cfg.port)
    cfg.jellyfin_url = cfg.jellyfin_url.rstrip("/")
    return cfg
