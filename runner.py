"""Combined speed + bufferbloat test, appended to SQLite."""
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "speedtest.db"
UPLOAD_PAYLOAD = Path(__file__).parent / "data" / "upload_payload.bin"

PING_TARGET = "1.1.1.1"
PING_INTERVAL = 0.2
CHUNK_BYTES = 52428800  # 50 MiB — Cloudflare's __down caps at <100 MiB
DOWNLOAD_URL = f"https://speed.cloudflare.com/__down?bytes={CHUNK_BYTES}"
UPLOAD_URL = "https://speed.cloudflare.com/__up"
UPLOAD_PAYLOAD_MB = 50

IDLE_SEC = 5
LOAD_SEC = 16
RAMP_DISCARD_SEC = 2
SETTLE_SEC = 1
PER_CHUNK_TIMEOUT = 8


def init_db() -> None:
    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                ts            TEXT NOT NULL,
                download_mbps REAL NOT NULL,
                upload_mbps   REAL NOT NULL,
                ping_ms       REAL NOT NULL,
                server        TEXT,
                server_id     INTEGER
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_results_ts ON results(ts)")
        existing = {row[1] for row in c.execute("PRAGMA table_info(results)")}
        for col in ("idle_ping_ms", "download_loaded_ping_ms", "upload_loaded_ping_ms"):
            if col not in existing:
                c.execute(f"ALTER TABLE results ADD COLUMN {col} REAL")


def ensure_upload_payload() -> None:
    target_size = UPLOAD_PAYLOAD_MB * 1024 * 1024
    if UPLOAD_PAYLOAD.exists() and UPLOAD_PAYLOAD.stat().st_size == target_size:
        return
    UPLOAD_PAYLOAD.parent.mkdir(exist_ok=True)
    with open(UPLOAD_PAYLOAD, "wb") as f:
        chunk = b"\0" * (1024 * 1024)
        for _ in range(UPLOAD_PAYLOAD_MB):
            f.write(chunk)


class PingCollector(threading.Thread):
    def __init__(self, target: str, interval: float) -> None:
        super().__init__(daemon=True)
        self.target = target
        self.interval = interval
        self.samples: list[tuple[float, float]] = []
        self._proc: subprocess.Popen | None = None

    def run(self) -> None:
        self._proc = subprocess.Popen(
            ["ping", "-i", str(self.interval), self.target],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if "time=" in line:
                try:
                    rtt = float(line.split("time=")[1].split(" ")[0])
                    self.samples.append((time.time(), rtt))
                except (ValueError, IndexError):
                    pass

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _median_in_window(
    samples: list[tuple[float, float]],
    t_start: float,
    t_end: float,
    discard_first: float = 0.0,
) -> float | None:
    cutoff = t_start + discard_first
    rtts = [rtt for ts, rtt in samples if cutoff <= ts <= t_end]
    return statistics.median(rtts) if rtts else None


def _curl_chunk_bytes(args: list[str], max_time: float) -> int:
    out = subprocess.run(
        args + ["--max-time", f"{max_time:.2f}",
                "-w", "%{size_download} %{size_upload}"],
        capture_output=True, text=True, check=False,
    )
    parts = out.stdout.strip().split()
    if len(parts) < 2:
        return 0
    return int(parts[0]) + int(parts[1])


def _run_loaded(args: list[str]) -> tuple[int, float]:
    """Loop short transfers until LOAD_SEC elapses; return total bytes and elapsed."""
    total = 0
    start = time.time()
    while True:
        remaining = LOAD_SEC - (time.time() - start)
        if remaining < 1.0:
            break
        total += _curl_chunk_bytes(args, min(remaining, PER_CHUNK_TIMEOUT))
    return total, time.time() - start


def _run_download() -> tuple[int, float]:
    return _run_loaded(["curl", "-s", "-o", "/dev/null", DOWNLOAD_URL])


def _run_upload() -> tuple[int, float]:
    return _run_loaded([
        "curl", "-s", "-o", "/dev/null", "-X", "POST",
        "-H", "Content-Type: application/octet-stream",
        "--data-binary", f"@{UPLOAD_PAYLOAD}",
        UPLOAD_URL,
    ])


def run() -> None:
    init_db()
    ensure_upload_payload()

    pinger = PingCollector(PING_TARGET, PING_INTERVAL)
    pinger.start()
    time.sleep(0.5)

    idle_start = time.time()
    time.sleep(IDLE_SEC)
    idle_end = time.time()

    dl_start = time.time()
    dl_bytes, dl_time = _run_download()
    dl_end = time.time()

    time.sleep(SETTLE_SEC)

    ul_start = time.time()
    ul_bytes, ul_time = _run_upload()
    ul_end = time.time()

    pinger.stop()
    pinger.join(timeout=3)

    download_mbps = (dl_bytes * 8) / dl_time / 1_000_000 if dl_time > 0 else 0.0
    upload_mbps = (ul_bytes * 8) / ul_time / 1_000_000 if ul_time > 0 else 0.0

    idle_ping = _median_in_window(pinger.samples, idle_start, idle_end)
    dl_loaded_ping = _median_in_window(pinger.samples, dl_start, dl_end, RAMP_DISCARD_SEC)
    ul_loaded_ping = _median_in_window(pinger.samples, ul_start, ul_end, RAMP_DISCARD_SEC)

    if idle_ping is None:
        raise RuntimeError("no idle ping samples collected — is the network down?")

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = (
        ts,
        download_mbps,
        upload_mbps,
        idle_ping,
        "Cloudflare edge",
        None,
        idle_ping,
        dl_loaded_ping,
        ul_loaded_ping,
    )
    with sqlite3.connect(DB) as c:
        c.execute(
            "INSERT INTO results "
            "(ts, download_mbps, upload_mbps, ping_ms, server, server_id, "
            " idle_ping_ms, download_loaded_ping_ms, upload_loaded_ping_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    parts = [
        ts,
        f"down={download_mbps:.1f} Mbps",
        f"up={upload_mbps:.1f} Mbps",
        f"idle_ping={idle_ping:.1f} ms",
    ]
    if dl_loaded_ping is not None:
        parts.append(f"bb_dl={dl_loaded_ping - idle_ping:+.1f} ms")
    if ul_loaded_ping is not None:
        parts.append(f"bb_ul={ul_loaded_ping - idle_ping:+.1f} ms")
    print("  ".join(parts))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"speedtest failed: {e}", file=sys.stderr)
        sys.exit(1)
