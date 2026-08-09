#!/usr/bin/env python3
"""Pantau umur & biaya instance Vast.ai; alert bila lupa di-destroy.

Skrip mandiri (stdlib saja, tanpa dependency) untuk memenuhi NFR-08
(alert biaya cloud): menampilkan semua instance dengan perkiraan biaya
terkumpul, memberi peringatan bila umur/biaya melewati ambang, dan
(opsional) auto-destroy supaya instance GPU yang selesai dipakai tidak
terlupakan (billing Vast per detik — instance nyala tanpa kerja tetap
ditagih).

Sumber data (prioritas):
  1. CLI ``vastai`` (default)   -> ``vastai show instances --raw``
  2. API langsung ``--api-key`` -> ``GET {api-url}/api/v1/instances`` (Bearer)
     (v0 sudah dihapus Vast — HTTP 410, 2026)

API key dibaca dari (prioritas): flag ``--api-key`` > env ``VAST_API_KEY``
> file ``.env`` di root repo (auto-load, tanpa dependency). Jangan pernah
menaruh key di dalam repo ter-commit — gunakan ``.env`` (sudah di-gitignore)
atau ``vastai set api-key`` (disimpan di ``~/.vastai/``).

Cara pakai:
    python infra/vast/vast_cost_monitor.py                # cek sekali
    python infra/vast/vast_cost_monitor.py --watch        # pantau terus
    python infra/vast/vast_cost_monitor.py --json         # output skrip lain
    python infra/vast/vast_cost_monitor.py --auto-destroy --label-contains smoke
    python infra/vast/vast_cost_monitor.py --auto-destroy --label-contains smoke --yes

Exit code (untuk cron / Task Scheduler):
    0 = aman (tidak ada yang melewati ambang)
    1 = error runtime (CLI/API tidak bisa dipanggil)
    2 = ada instance melewati ambang (alert terkirim)
    3 = ada instance di-destroy, atau destroy diminta (dry-run)

Catatan: usage error argparse juga exit 2 — di cron, perhatikan stderr untuk
membedakan dari breach sungguhan.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_API = "https://console.vast.ai"
# Status yang masih menagih biaya GPU penuh (dph_total per jam).
ACTIVE_STATUS = {"running", "loading", "rebooting"}
DEFAULT_STATE_FILE = str(Path.home() / ".vast_cost_monitor_state.json")
DEFAULT_LOG_FILE = "vast-monitor.log"


# ---------------------------------------------------------------------------
# Model & analisis
# ---------------------------------------------------------------------------
@dataclass
class Row:
    instance_id: int
    gpu: str
    status: str
    label: str
    uptime_h: float | None
    cost: float | None
    breaches: list[str]
    active: bool

    def to_json(self) -> dict:
        return {
            "id": self.instance_id,
            "gpu": self.gpu,
            "status": self.status,
            "label": self.label,
            "uptime_h": round(self.uptime_h, 2) if self.uptime_h is not None else None,
            "est_cost_usd": round(self.cost, 2) if self.cost is not None else None,
            "breaches": self.breaches,
            "billing_active": self.active,
        }


def _to_epoch(value) -> float | None:
    """start_date bisa int/float, atau string (beberapa serializer JSON)."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return float(value) if isinstance(value, (int, float)) else None


def analyze(instances: list[dict], max_hours: float, max_cost: float) -> list[Row]:
    """Hitung umur, perkiraan biaya, dan pelanggaran ambang tiap instance."""
    now = time.time()
    rows: list[Row] = []
    for inst in instances:
        iid = inst.get("id")
        status = str(inst.get("actual_status") or inst.get("cur_state") or "unknown")
        active = status in ACTIVE_STATUS
        start = _to_epoch(inst.get("start_date"))
        uptime_h = max(0.0, (now - start) / 3600.0) if start else None
        # Biaya hanya relevan untuk instance aktif menagih; status
        # stopped/frozen hanya menagih storage, jangan tampilkan menyesatkan.
        dph = inst.get("dph_total")
        cost = None
        if active and uptime_h is not None and isinstance(dph, (int, float)):
            cost = dph * uptime_h
        breaches: list[str] = []
        if active and uptime_h is not None and max_hours and uptime_h > max_hours:
            breaches.append("hours")
        if active and cost is not None and max_cost and cost > max_cost:
            breaches.append("cost")
        gpus = inst.get("num_gpus") or 1
        gpu = f"{inst.get('gpu_name') or '?'} x{gpus}"
        rows.append(
            Row(iid, gpu, status, str(inst.get("label") or ""), uptime_h, cost, breaches, active)
        )
    return rows


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------
def _parse_instances_json(text: str) -> list[dict] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        items = data.get("instances")
        if isinstance(items, list):
            return [d for d in items if isinstance(d, dict)]
    return None


def fetch_cli(cli: str) -> list[dict]:
    """Panggil `vastai show instances` — coba flag --raw lalu --json."""
    last_err: Exception | None = None
    for flag in ("--raw", "--json"):
        try:
            out = subprocess.run(
                [cli, "show", "instances", flag],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = exc
            continue
        if out.returncode != 0:
            last_err = RuntimeError(out.stderr.strip() or f"exit code {out.returncode}")
            continue
        data = _parse_instances_json(out.stdout)
        if data is not None:
            return data
        last_err = RuntimeError("output bukan JSON yang dikenali")
    raise RuntimeError(f"Gagal memanggil '{cli} show instances': {last_err}")


def fetch_api(api_key: str, base: str) -> list[dict]:
    """Jalur alternatif tanpa CLI: API publik v1 (Bearer header saja).

    Endpoint v0 (api/v0/instances) sudah dihapus Vast (HTTP 410 Gone, 2026).
    Kunci hanya lewat header Authorization (tidak di request line), TIDAK
    pernah dicetak di pesan error — URL ditampilkan ter-redact. `limit`
    besar agar instance di halaman berikutnya tidak terlewat (v1 paginated).
    """
    url = f"{base}/api/v1/instances?" + urllib.parse.urlencode({"limit": 1000})
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _parse_instances_json(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise RuntimeError(f"API gagal ({url.split('?')[0]}?limit=..): {exc}") from exc
    if data is None:
        raise RuntimeError("API mengembalikan JSON yang tidak dikenal")
    return data


def load_instances(args: argparse.Namespace) -> list[dict]:
    if args.api_key:
        return fetch_api(args.api_key, args.api_url)
    cli = shutil.which(args.cli)
    if not cli:
        raise RuntimeError(
            "Perintah 'vastai' tidak ditemukan. Install: 'pip install vastai' lalu "
            "'vastai set api-key <KEY>' — atau pakai '--api-key <KEY>' (tanpa CLI)."
        )
    return fetch_cli(cli)


# ---------------------------------------------------------------------------
# Tampilan
# ---------------------------------------------------------------------------
def _out(args: argparse.Namespace):
    """stdout hanya untuk output utama; alert/status ke stderr saat --json/--quiet."""
    return sys.stderr if args.json or args.quiet else sys.stdout


def _fmt_uptime(h: float | None) -> str:
    return "n/a" if h is None else f"{h:.1f} h"


def _fmt_cost(c: float | None) -> str:
    return "n/a" if c is None else f"${c:.2f}"


def render_table(rows: list[Row]) -> None:
    label_w = max([len("Label")] + [min(len(r.label), 18) for r in rows])
    gpu_w = max([len("GPU")] + [len(r.gpu) for r in rows])
    id_w = max([len("ID")] + [len(str(r.instance_id)) for r in rows])
    header = (
        f"{'ID':>{id_w}}  {'GPU':<{gpu_w}}  {'Status':<9}  {'Label':<{label_w}}  "
        f"{'Uptime':>8}  {'Est biaya':>9}  Alert"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        alert = ",".join(r.breaches) if r.breaches else "-"
        label = r.label[:18] if len(r.label) > 18 else r.label
        print(
            f"{r.instance_id:>{id_w}}  {r.gpu:<{gpu_w}}  {r.status:<9}  {label:<{label_w}}  "
            f"{_fmt_uptime(r.uptime_h):>8}  {_fmt_cost(r.cost):>9}  {alert}"
        )


# ---------------------------------------------------------------------------
# Alert (notifikasi)
# ---------------------------------------------------------------------------
def _state_load(path: str) -> dict[str, float]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_save(path: str, state: dict[str, float]) -> None:
    with contextlib.suppress(OSError):
        Path(path).write_text(json.dumps(state), encoding="utf-8")


def desktop_notify(title: str, msg: str) -> bool:
    """Popup OS-native, best-effort (Windows/Linux/macOS)."""
    system = platform.system()
    try:
        if system == "Windows":
            text = msg.replace("'", "''")
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(New-Object -ComObject WScript.Shell).Popup('{text}', 30, '{title}', 0x40)",
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            return out.returncode == 0
        if system == "Darwin":
            out = subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                capture_output=True,
                timeout=15,
                check=False,
            )
            return out.returncode == 0
        if system == "Linux" and shutil.which("notify-send"):
            out = subprocess.run(
                ["notify-send", title, msg], capture_output=True, timeout=15, check=False
            )
            return out.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    return False


def _http_post(url: str, data: bytes, headers: dict[str, str]) -> bool:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def send_alerts(rows: list[Row], args: argparse.Namespace) -> None:
    """Kirim alert untuk instance yang melewati ambang (dengan cooldown)."""
    fresh = [r for r in rows if r.breaches and r.active]
    if not fresh:
        return
    state = _state_load(args.state_file)
    now = time.time()
    due = [r for r in fresh if now - state.get(str(r.instance_id), 0.0) >= args.cooldown]
    if not due:
        return
    title = "⚠ Vast.ai: instance lewat ambang biaya"
    lines = [f"{len(due)} instance berjalan melewati ambang (jam/biaya):"]
    for r in due:
        lines.append(
            f"  #{r.instance_id} {r.gpu} [{r.label or '-'}] {_fmt_uptime(r.uptime_h)} "
            f"~{_fmt_cost(r.cost)} -> {', '.join(r.breaches)}"
        )
    msg = "\n".join(lines)
    print(msg, file=_out(args))

    sent = False
    if args.ntfy_topic:
        sent = _http_post(
            f"https://ntfy.sh/{args.ntfy_topic}",
            msg.encode("utf-8"),
            {"Title": title, "Priority": "high", "Tags": "rotating_light"},
        ) or sent
    if args.webhook_url:
        sent = _http_post(
            args.webhook_url,
            json.dumps({"text": f"{title}\n{msg}"}).encode("utf-8"),
            {"Content-Type": "application/json"},
        ) or sent
    if args.notify_desktop:
        sent = desktop_notify(title, msg) or sent
    if sent:
        for r in due:
            state[str(r.instance_id)] = now
        _state_save(args.state_file, state)


# ---------------------------------------------------------------------------
# Auto-destroy
# ---------------------------------------------------------------------------
def _log(path: str, msg: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def maybe_destroy(rows: list[Row], args: argparse.Namespace) -> bool:
    """Destroy instance yang lewat ambang. Tanpa --yes hanya dry-run.

    Keamanan: hanya status aktif; label mengandung 'prod' di-skip kecuali
    --allow-prod; --label-contains mempersempit sasaran. Mengembalikan True
    bila ada destroy yang dieksekusi ATAU diminta (dry-run).
    """
    candidates = [r for r in rows if r.breaches and r.active]
    if args.label_contains:
        needle = args.label_contains.lower()
        candidates = [r for r in candidates if needle in r.label.lower()]
    if not candidates:
        return False

    stream = _out(args)
    print("\n== AUTO-DESTROY ==", file=stream)
    actioned = False
    for r in candidates:
        if "prod" in r.label.lower() and not args.allow_prod:
            print(
                f"  SKIP #{r.instance_id}: label mengandung 'prod' (pakai --allow-prod)",
                file=stream,
            )
            continue
        actioned = True
        if args.yes:
            try:
                out = subprocess.run(
                    [args.cli, "destroy", "instance", str(r.instance_id)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"  GAGAL destroy #{r.instance_id}: {exc}", file=stream)
                _log(args.log_file, f"FAIL destroy {r.instance_id} {r.gpu}: {exc}")
                continue
            if out.returncode == 0:
                print(
                    f"  DESTROYED #{r.instance_id} ({r.gpu}, label={r.label!r}) "
                    "— billing berhenti",
                    file=stream,
                )
                _log(args.log_file, f"DESTROYED {r.instance_id} {r.gpu} label={r.label!r}")
            else:
                err = out.stderr.strip() or out.stdout.strip()
                print(f"  GAGAL destroy #{r.instance_id}: {err}", file=stream)
                _log(args.log_file, f"FAIL destroy {r.instance_id} {r.gpu}: {err}")
        else:
            print(
                f"  [dry-run] akan destroy #{r.instance_id} ({r.gpu}, label={r.label!r}) — "
                "ulangi dengan --yes untuk mengeksekusi",
                file=stream,
            )
            _log(args.log_file, f"DRY-RUN destroy {r.instance_id} {r.gpu} label={r.label!r}")
    return actioned


# ---------------------------------------------------------------------------
# Alur utama
# ---------------------------------------------------------------------------
def run_check(args: argparse.Namespace) -> int:
    instances = load_instances(args)
    rows = analyze(instances, args.max_hours, args.max_cost)
    if args.json:
        print(json.dumps([r.to_json() for r in rows], indent=2))
    elif not args.quiet:
        print(
            f"Vast.ai — {datetime.now(UTC).strftime('%H:%M:%S UTC')} "
            f"(ambang: umur >{args.max_hours or 'off'} jam, biaya >${args.max_cost or 'off'})"
        )
        render_table(rows)

    breached = [r for r in rows if r.breaches and r.active]
    if not breached:
        return 0
    send_alerts(rows, args)
    if not args.auto_destroy:
        return 2
    actioned = maybe_destroy(rows, args)
    return 3 if actioned else 2


def watch(args: argparse.Namespace) -> int:
    print(
        f"Memantau tiap {args.interval} s — ambang: umur >{args.max_hours or 'off'} jam, "
        f"biaya >${args.max_cost or 'off'}. Ctrl+C untuk berhenti."
    )
    while True:
        try:
            run_check(args)
        except RuntimeError as exc:
            ts = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"[{ts}] ERROR: {exc}", file=sys.stderr)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _load_dotenv() -> None:
    """Muat VAST_* dari file .env di ROOT repo (tanpa override env yang ada).

    Loader minimal tanpa python-dotenv — cukup untuk variabel key/alert.
    Hanya root repo (bukan CWD) agar tidak membaca .env sembarangan saat
    skrip dijalankan dari folder lain.
    """
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key.startswith("VAST_") and key not in os.environ:
            os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--cli", default="vastai", help="Nama perintah vastai (default: vastai)")
    p.add_argument(
        "--api-key",
        default=os.environ.get("VAST_API_KEY"),
        help="API key Vast.ai (tanpa CLI)",
    )
    p.add_argument("--api-url", default=DEFAULT_API, help=f"Base URL API (default: {DEFAULT_API})")
    p.add_argument(
        "--max-hours",
        type=float,
        default=None,
        help="Alert bila umur > N jam (default 2.0; 0 = mati). Env VAST_MONITOR_MAX_HOURS",
    )
    p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Alert bila biaya > $N (default 5.0; 0 = mati). Env VAST_MONITOR_MAX_COST",
    )
    p.add_argument("--watch", action="store_true", help="Pantau terus (loop)")
    p.add_argument("--interval", type=int, default=300, help="Interval watch, detik (default 300)")
    p.add_argument("--json", action="store_true", help="Output JSON ke stdout")
    p.add_argument("--quiet", action="store_true", help="Tanpa tabel; hanya alert/error")
    p.add_argument("--auto-destroy", action="store_true", help="Destroy yang lewat ambang")
    p.add_argument("--yes", action="store_true", help="Eksekusi destroy (tanpa ini hanya dry-run)")
    p.add_argument(
        "--label-contains",
        default=None,
        help="Auto-destroy hanya label yang memuat teks ini",
    )
    p.add_argument("--allow-prod", action="store_true", help="Izinkan destroy label 'prod'")
    p.add_argument(
        "--ntfy-topic",
        default=os.environ.get("VAST_MONITOR_NTFY_TOPIC"),
        help="Alert ke ntfy.sh/<topic>",
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get("VAST_MONITOR_WEBHOOK_URL"),
        help="Alert POST JSON {text} ke webhook",
    )
    p.add_argument("--notify-desktop", action="store_true", help="Popup desktop OS-native")
    p.add_argument(
        "--cooldown",
        type=int,
        default=3600,
        help="Jeda antar-alert per instance, detik (default 3600)",
    )
    p.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="File state cooldown alert")
    p.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILE,
        help="Log auto-destroy (default: vast-monitor.log)",
    )
    return p


def main() -> int:
    _load_dotenv()  # sebelum build_parser — default --api-key membaca env
    args = build_parser().parse_args()
    if args.max_hours is None:
        args.max_hours = _env_float("VAST_MONITOR_MAX_HOURS", 2.0)
    if args.max_cost is None:
        args.max_cost = _env_float("VAST_MONITOR_MAX_COST", 5.0)
    # Ambang negatif selalu-breach — normalisasi jadi mati (0).
    if args.max_hours < 0 or args.max_cost < 0:
        print(
            "Peringatan: --max-hours/--max-cost negatif diperlakukan sebagai mati (0).",
            file=sys.stderr,
        )
        args.max_hours = max(0.0, args.max_hours)
        args.max_cost = max(0.0, args.max_cost)
    if args.yes and not args.auto_destroy:
        print("Peringatan: --yes tanpa --auto-destroy tidak berpengaruh.", file=sys.stderr)
    if args.api_key and args.auto_destroy:
        print(
            "Peringatan: auto-destroy tetap memakai CLI 'vastai' "
            "(--api-key hanya untuk membaca).",
            file=sys.stderr,
        )
    try:
        return watch(args) if args.watch else run_check(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
