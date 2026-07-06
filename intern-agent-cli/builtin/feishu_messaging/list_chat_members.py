#!/usr/bin/env python3
from pathlib import Path
import sys

CLI_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CLI_ROOT))

from scripts.common.feishu_messaging import main_list_chat_members  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main_list_chat_members())
