"""Ekspor spesifikasi OpenAPI lengkap (termasuk API Publik B2B FR-14) ke file.

Pemakaian:
  python scripts/export_openapi.py                 # -> docs/api/openapi.yaml
  python scripts/export_openapi.py --out api.json  # format ikut ekstensi

Dipakai untuk dokumentasi developer (docs/API_B2B.md merujuk file ini) dan
tooling klien (generator SDK/klien). Regenerate setiap kali endpoint berubah
(tidak ada CI gate — jaga manual, atau panggil dari workflow bila perlu).
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# `app` bukan package yang ter-importable langsung dari scripts/ — tambahkan
# root api/ ke sys.path dulu (sama seperti pola script lain di folder ini).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.yaml"),
        help="File keluaran (ekstensi .yaml/.yml atau .json)",
    )
    args = parser.parse_args()

    spec = app.openapi()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".yaml", ".yml"):
        out.write_text(
            yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"OpenAPI diekspor: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
