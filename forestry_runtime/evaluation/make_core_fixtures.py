from __future__ import annotations

import argparse
import shutil
from pathlib import Path


FILES = {
    "mixed/config.ini": "[processing]\nmode = research\nscale = 2.5\n",
    "mixed/notes.txt": "通用 Agent 验收目录，不是无人机影像目录。\n",
    "mixed/README": "extensionless text file\n",
    "mixed/nested/data_a.csv": "plot,value\nA,10\nB,20\n",
    "mixed/nested/data_b.csv": "plot,value\nC,30\nD,40\n",
    "mixed/broken.py": (
        "from pathlib import Path\n\n"
        "values = [2, 4, 6]\n"
        "total = sum(value)\n"
        "Path('result.txt').write_text(str(total), encoding='utf-8')\n"
        "print(total)\n"
    ),
}


def create(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    for relative, content in FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / "mixed" / "sample.bin").write_bytes(b"\x00\x01\x02\xff")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evaluation/work"))
    args = parser.parse_args()
    create(args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
