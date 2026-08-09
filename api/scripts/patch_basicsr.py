"""Patch kompatibilitas basicsr 1.4.2 vs torchvision >=0.17.

torchvision menghapus modul `torchvision.transforms.functional_tensor`;
`rgb_to_grayscale` kini ada di `torchvision.transforms.functional`.
basicsr 1.4.2 masih mengimpor dari modul lama -> ImportError saat runtime.

Dijalankan di Dockerfile.worker SETELAH pip install (image GPU). Di image
CPU / mesin tanpa basicsr, script selesai dengan SKIP (exit 0) — aman
dijalankan di mana saja.

Exit code: 0 = sukses/SKIP, 1 = error tak terduga.
"""

import glob
import sys
from pathlib import Path

_OLD = "from torchvision.transforms.functional_tensor import rgb_to_grayscale"
_NEW = "from torchvision.transforms.functional import rgb_to_grayscale"


def find_degradations() -> Path | None:
    """Cari basicsr/data/degradations.py tanpa meng-import basicsr.

    `import basicsr` memicu degradations.py yang belum ter-patch dan justru
    gagal — karena itu lokasi dicari lewat site-packages.
    """
    roots: list[str] = []
    try:
        import site

        roots.extend(site.getsitepackages())
    except Exception:  # noqa: BLE001 - venv bisa tanpa getsitepackages
        pass
    try:
        import sysconfig

        purelib = sysconfig.get_paths().get("purelib")
        if purelib:
            roots.append(purelib)
    except Exception:  # noqa: BLE001
        pass
    roots.append(str(Path(sys.prefix) / "lib"))

    for root in roots:
        if not root:
            continue
        hits = glob.glob(f"{root}/basicsr/data/degradations.py")
        if hits:
            return Path(hits[0])
    return None


def patch_file(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    if _OLD not in src:
        return f"SKIP: pattern sudah tidak ada di {path}"
    path.write_text(src.replace(_OLD, _NEW), encoding="utf-8")
    return f"PATCHED: {path}"


def main() -> int:
    path = find_degradations()
    if path is None:
        print("SKIP: basicsr tidak terpasang (image CPU / non-worker)", file=sys.stderr)
        return 0
    print(patch_file(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
