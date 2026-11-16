"""Rebuild the self-contained replay page from the template and dataset."""

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="JSON data exported by flight_model.py")
    args = parser.parse_args()
    index = HERE / "replay.html"
    template = (HERE / "template.html").read_text(encoding="utf-8")
    if args.data:
        data = json.loads(args.data.read_text(encoding="utf-8"))
    else:
        # Preserve the committed replay when only layout or interaction code changes.
        match = re.search(r"const DATA = (.*?);\s*</script>", index.read_text(encoding="utf-8"), re.S)
        if not match:
            parser.error("No embedded dataset in replay.html; pass --data site_data.json")
        data = json.loads(match.group(1))
    if not isinstance(data.get("rows"), list) or not data["rows"]:
        parser.error("Dataset has no flight rows")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    index.write_text(template.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print(f"Built {index} with {len(data['rows']):,} flights")


if __name__ == "__main__":
    main()
