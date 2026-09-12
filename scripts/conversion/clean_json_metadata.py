"""清理既有 JSON 中被誤解析的判決結果欄位。"""

import argparse
import json
import re
from pathlib import Path

from legal_tool.config import JSON_OUTPUT_DIR


def clean_result(value):
    if not isinstance(value, str):
        return value
    return re.sub(r"\.pdf\s*的副本$", "", value, flags=re.IGNORECASE).strip()


def clean_json_directory(directory):
    directory = Path(directory)
    changed = 0
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            continue
        old_value = metadata.get("判決結果")
        new_value = clean_result(old_value)
        if new_value == old_value:
            continue
        metadata["判決結果"] = new_value
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed += 1
    print(f"已清理 {changed} 份 JSON。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=str(JSON_OUTPUT_DIR))
    args = parser.parse_args()
    clean_json_directory(args.directory)
