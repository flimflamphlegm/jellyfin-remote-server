#!/usr/bin/env python3
"""Entry point. Loads the app package from src/jellyfin_remote/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jellyfin_remote.main import main  # noqa: E402

if __name__ == "__main__":
    main()
