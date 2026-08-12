#!/usr/bin/env python3
"""Pantau antrean job & metrik operasional JernihAI; alert keputusan autoscale (NFR-08).

Skrip mandiri (stdlib saja, tanpa dependency) membaca endpoint metrik API
(`GET {api}/api/v1/health/metrics`) dan memberi sinyal operasional:

  * panjang antrean Celery (job menunggu worker) — menumpuk terus di atas
    ambang = tambah worker (autoscale up); nyaris nol = kapasitas berlebih;
  * throughput & failure rate 24 jam — failure rate tinggi = cek model/GPU;
  * rata-rata durasi proses — verifikasi KPI NFR-01 (end-to-end < 60 detik).

Sumber data: endpoint metrik API saja — skrip TIDAK butuh akses langsung ke
Redis/DB. Base URL dari flag ``--api-url`` > env ``JERNIHAI_API_URL`` >
``http://localhost:8000``.

Cara pakai:
    python infra/monitor/queue_monitor.py                      # cek sekali
    python infra/monitor/queue_monitor.py --watch --interval 60
    python infra/monitor/queue_monitor.py --json               # output skrip lain
    python infra/monitor/queue_monitor.py --max-queue 5 --ntfy-topic jernihai-ops
    python infra/monitor/queue_monitor.py --max-failure-rate 0.2 --webhook-url ...

Exit code (cron / Task Scheduler):
    0 = sehat (tidak ada metrik melewati ambang)
    1 = error runtime (API tidak bisa dihubungi / respons tidak dikenal)
    2 = perlu perhatian: antrean / failure rate melewati ambang

Catatan: usage error argparse juga exit 2 — di cron, perhatikan stderr untuk
membedakan dari breach sungguhan (pola sama dengan vast_cost_monitor.py).
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
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_API = "http://localhost:8000"
DEFAULT_STATE_FILE = str(Path.home() / ".jernihai_queue_monitor_state.json")


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------
def fetch_metrics(base: str, timeout: float = 15.0) -> dict:
    """GET {base}/api/v1/health/metrics -> dict (raise RuntimeError bila gagal)."""
    url = f"{base.rstrip('/')}/api/v1/health/metrics"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise RuntimeError(f"API tidak terjangkau ({url}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respons API bukan JSON ({url}): {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Respons API tidak dikenal (bukan objek JSON)")
    return data


# ---------------------------------------------------------------------------
# Analisis
# ---------------------------------------------------------------------------
def _to_f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def analyze(data: dict, max_queue: int, max_failure_rate: float) -> list[str]:
    """Return daftar pelanggaran ambang (kosong = sehat)."""
    breaches: list[str] = []

    queue = data.get("queue") or {}
    length = queue.get("length")
    q_status = queue.get("status")
    # Redis tidak terjangkau = sinyal penting, tetap dilaporkan meski ambang
    # antrean "off" (--max-queue 0) — bukan breach antrean, tapi mengubah
    # exit code (2) supaya cron/alert tahu Redis/broker bermasalah.
    if q_status == "error":
        breaches.append("queue:unreachable")
    if max_queue > 0 and q_status == "ok" and length is not None and length > max_queue:
        breaches.append(f"queue:{length}")

    tp = data.get("throughput") or {}
    rate = tp.get("failure_rate_24h")
    if max_failure_rate > 0 and rate is not None and rate > max_failure_rate:
        breaches.append(f"failure-rate:{rate:.1%}")

    return breaches


# ---------------------------------------------------------------------------
# Tampilan
# ---------------------------------------------------------------------------
def _out(args: argparse.Namespace):
    """stdout hanya untuk output utama; alert/status ke stderr saat --json/--quiet."""
    return sys.stderr if args.json or args.quiet else sys.stdout


def _fmt_seconds(s: float | None) -> str:
    return "n/a" if s is None else f"{s:.0f}s"


def render(data: dict, breaches: list[str], args: argparse.Namespace) -> None:
    queue = data.get("queue") or {}
    jobs = data.get("jobs") or {}
    tp = data.get("throughput") or {}
    latency = data.get("latency") or {}
    cfg = data.get("config") or {}

    q_status = queue.get("status", "?")
    q_text = (
        f"{queue.get('length', '-')} (status={q_status})"
        if q_status == "ok"
        else f"status={q_status}"
    )
    rate = tp.get("failure_rate_24h")
    rate_text = f"{rate:.1%}" if rate is not None else "n/a"
    print(
        f"Antrean: {q_text}  |  Job: queued={jobs.get('queued', 0)} "
        f"processing={jobs.get('processing', 0)} completed={jobs.get('completed', 0)} "
        f"failed={jobs.get('failed', 0)}"
    )
    print(
        f"Throughput: {tp.get('completed_1h', 0)}/jam, "
        f"{tp.get('completed_24h', 0)}/24jam, gagal {tp.get('failed_24h', 0)} "
        f"(failure-rate 24jam {rate_text})  |  Latensi avg "
        f"{_fmt_seconds(latency.get('avg_processing_seconds_24h'))} "
        f"({latency.get('samples', 0)} sampel)"
    )
    print(
        f"Config: env={cfg.get('environment')}, storage={cfg.get('storage_backend')}, "
        f"ratelimit={cfg.get('rate_limit_backend')}, enhance={cfg.get('enhance_backend')}"
    )
    if breaches:
        print(f"ALERT: {', '.join(breaches)}", file=_out(args))


# ---------------------------------------------------------------------------
# Alert (notifikasi) — pola sama dengan vast_cost_monitor.py
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


def _notify(args: argparse.Namespace, title: str, msg: str) -> bool:
    """Kirim ke semua channel aktif; True bila minimal satu channel berhasil."""
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
    return sent


def _cooldown_key(breaches: list[str]) -> str:
    """Key cooldown per KATEGORI, bukan nilai breach.

    Tanpa ini, antrean yang berfluktuasi (mis. 20 -> 25) mengubah key dan
    alert bisa terkirim ulang di dalam jendela cooldown (spam).
    """
    return ",".join(sorted({b.split(":", 1)[0] for b in breaches}))


def send_alerts(data: dict, breaches: list[str], args: argparse.Namespace) -> None:
    """Kirim alert breach dengan cooldown (state file per kategori)."""
    if not breaches:
        return
    state = _state_load(args.state_file)
    now = time.time()
    key = _cooldown_key(breaches)
    if now - state.get(key, 0.0) < args.cooldown:
        return

    queue = data.get("queue") or {}
    tp = data.get("throughput") or {}
    lines: list[str] = []
    if "queue:unreachable" in breaches:
        lines.append("Antrean Redis tidak dapat dibaca — cek kesehatan Redis/broker.")
    length = queue.get("length")
    if length is not None and length > 0:
        lines.append(
            f"Antrean {length} job menunggu (ambang {args.max_queue}) — "
            "pertimbangkan menambah worker (autoscale up)."
        )
    rate = tp.get("failure_rate_24h")
    if rate is not None and args.max_failure_rate > 0 and rate > args.max_failure_rate:
        lines.append(
            f"Failure rate 24 jam {rate:.1%} melewati ambang "
            f"{args.max_failure_rate:.0%} — cek log worker/model GPU."
        )
    title = "⚠ JernihAI: metrik melewati ambang"
    msg = "\n".join(lines) or ", ".join(breaches)
    print(msg, file=_out(args))

    if _notify(args, title, msg):
        state[key] = now
        _state_save(args.state_file, state)


# ---------------------------------------------------------------------------
# Alur utama
# ---------------------------------------------------------------------------
def run_check(args: argparse.Namespace) -> int:
    data = fetch_metrics(args.api_url, args.timeout)
    breaches = analyze(data, args.max_queue, args.max_failure_rate)

    if args.json:
        print(json.dumps({"breaches": breaches, "metrics": data}, indent=2))
    elif not args.quiet:
        print(
            f"JernihAI — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} "
            f"(ambang: antrean >{args.max_queue or 'off'}, "
            f"failure-rate >{args.max_failure_rate or 'off'})"
        )
        render(data, breaches, args)

    if breaches:
        send_alerts(data, breaches, args)
        return 2
    return 0


def watch(args: argparse.Namespace) -> int:
    print(
        f"Memantau tiap {args.interval}s — ambang: antrean >{args.max_queue or 'off'}, "
        f"failure-rate >{args.max_failure_rate or 'off'}. Ctrl+C untuk berhenti."
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("JERNIHAI_API_URL", DEFAULT_API),
        help=f"Base URL API (default: {DEFAULT_API}; env JERNIHAI_API_URL)",
    )
    p.add_argument(
        "--max-queue",
        type=int,
        default=None,
        help="Alert bila antrean > N job (default 10; 0 = mati). Env JERNIHAI_MONITOR_MAX_QUEUE",
    )
    p.add_argument(
        "--max-failure-rate",
        type=float,
        default=None,
        help="Alert bila failure rate 24 jam > N (default 0.2; 0 = mati). "
        "Env JERNIHAI_MONITOR_MAX_FAILURE_RATE",
    )
    p.add_argument("--timeout", type=float, default=15.0, help="Timeout HTTP, detik")
    p.add_argument("--watch", action="store_true", help="Pantau terus (loop)")
    p.add_argument("--interval", type=int, default=300, help="Interval watch, detik (default 300)")
    p.add_argument("--json", action="store_true", help="Output JSON ke stdout")
    p.add_argument("--quiet", action="store_true", help="Tanpa tabel; hanya alert/error")
    p.add_argument(
        "--ntfy-topic",
        default=os.environ.get("JERNIHAI_MONITOR_NTFY_TOPIC"),
        help="Alert ke ntfy.sh/<topic>",
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get("JERNIHAI_MONITOR_WEBHOOK_URL"),
        help="Alert POST JSON {text} ke webhook",
    )
    p.add_argument("--notify-desktop", action="store_true", help="Popup desktop OS-native")
    p.add_argument(
        "--cooldown",
        type=int,
        default=3600,
        help="Jeda antar-alert per kategori, detik (default 3600)",
    )
    p.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="File state cooldown alert")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.max_queue is None:
        args.max_queue = int(_env_float("JERNIHAI_MONITOR_MAX_QUEUE", 10.0))
    if args.max_failure_rate is None:
        args.max_failure_rate = _env_float("JERNIHAI_MONITOR_MAX_FAILURE_RATE", 0.2)
    if args.max_queue < 0 or args.max_failure_rate < 0:
        print(
            "Peringatan: ambang negatif diperlakukan sebagai mati (0).",
            file=sys.stderr,
        )
        args.max_queue = max(0, args.max_queue)
        args.max_failure_rate = max(0.0, args.max_failure_rate)
    try:
        return watch(args) if args.watch else run_check(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
