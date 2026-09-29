"""yuki-aidd-kit のデザインシステム出荷物を qa_sentinel/web/static/ds/ に写す。
使い方: python scripts/sync_design_system.py <yuki-aidd-kit のパス>
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

FILES = {"tokens.css": "02_共通/ひな形/tokens.css", "components.css": "02_共通/ひな形/ui/components.css", "layout.css": "02_共通/ひな形/ui/layout.css",
         "contrast-pairs.md": "02_共通/ひな形/ui/contrast-pairs.md", "feedback.js": "02_共通/ひな形/components/feedback.js", "icons.js": "02_共通/ひな形/components/icons.js"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__); return 2
    kit = Path(sys.argv[1]); dst = Path(__file__).resolve().parent.parent / "qa_sentinel" / "web" / "static" / "ds"
    for name, rel in FILES.items():
        src = kit / rel
        if not src.exists():
            print(f"skip: {src}"); continue
        shutil.copy2(src, dst / name); print(f"copied: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
