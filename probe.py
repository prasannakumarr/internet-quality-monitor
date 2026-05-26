"""Per-site DNS/TCP/TLS/TTFB latency probes; appended to SQLite on every run."""
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "speedtest.db"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# (label, url)  — ordered by expected latency, fastest first
SITES = [
    ("cloudflare.com", "https://cloudflare.com"),
    ("google.com",     "https://google.com"),
    ("amazon.in",      "https://amazon.in"),
    ("youtube.com",    "https://youtube.com"),
    ("netflix.com",    "https://netflix.com"),
    ("github.com",     "https://github.com"),
    ("stripe.com",     "https://stripe.com"),
]

_W_FMT = "|".join([
    "%{time_namelookup}",
    "%{time_connect}",
    "%{time_appconnect}",
    "%{time_starttransfer}",
    "%{time_total}",
    "%{http_code}",
    "%{exitcode}",
])


def init_db() -> None:
    with sqlite3.connect(DB) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS probes (
                ts          TEXT    NOT NULL,
                site        TEXT    NOT NULL,
                dns_ms      REAL,
                tcp_ms      REAL,
                tls_ms      REAL,
                ttfb_ms     REAL,
                total_ms    REAL,
                http_status INTEGER,
                error       TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_probes_ts ON probes(ts)")


def _curl(url: str, extra: list[str]) -> dict:
    cmd = [
        "curl", "-s", "-L", "-o", "/dev/null",
        "-A", USER_AGENT,
        "--connect-timeout", "5",
        "--max-time", "10",
        "-w", _W_FMT,
    ] + extra + [url]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    parts = out.stdout.strip().split("|")
    if len(parts) < 7:
        return {"error": f"unexpected output: {out.stdout.strip()!r}"}
    try:
        t_dns, t_con, t_tls, t_ttfb, t_tot = (float(p) for p in parts[:5])
        status, exit_code = int(parts[5]), int(parts[6])
        return {
            "dns_ms":      round(t_dns * 1000, 2),
            "tcp_ms":      round((t_con  - t_dns) * 1000, 2),
            "tls_ms":      round((t_tls  - t_con) * 1000, 2),
            "ttfb_ms":     round((t_ttfb - t_tls) * 1000, 2),
            "total_ms":    round(t_tot * 1000, 2),
            "http_status": status,
            "error":       None if exit_code == 0 else f"curl exit {exit_code}",
        }
    except (ValueError, IndexError) as exc:
        return {"error": str(exc)}


def probe_site(name: str, url: str) -> dict:
    result = _curl(url, ["--head"])
    # 405 = server refuses HEAD; retry with first-byte GET
    if result.get("http_status") == 405:
        result = _curl(url, ["-r", "0-0"])
    result["site"] = name
    return result


def run() -> None:
    init_db()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows: list[dict] = []
    lock = threading.Lock()

    def collect(name: str, url: str) -> None:
        r = probe_site(name, url)
        with lock:
            rows.append(r)

    threads = [threading.Thread(target=collect, args=(n, u)) for n, u in SITES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with sqlite3.connect(DB) as c:
        c.executemany(
            "INSERT INTO probes (ts, site, dns_ms, tcp_ms, tls_ms, ttfb_ms, total_ms, http_status, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    ts,
                    r.get("site"),
                    r.get("dns_ms"),
                    r.get("tcp_ms"),
                    r.get("tls_ms"),
                    r.get("ttfb_ms"),
                    r.get("total_ms"),
                    r.get("http_status"),
                    r.get("error"),
                )
                for r in rows
            ],
        )

    for r in sorted(rows, key=lambda x: x.get("total_ms") or 9999):
        if r.get("error") and not r.get("http_status"):
            print(f"  {r['site']:20s}  ERROR: {r['error']}")
        else:
            print(
                f"  {r['site']:20s}"
                f"  dns={r.get('dns_ms', 0):6.1f} ms"
                f"  tcp={r.get('tcp_ms', 0):6.1f} ms"
                f"  tls={r.get('tls_ms', 0):6.1f} ms"
                f"  ttfb={r.get('ttfb_ms', 0):7.1f} ms"
                f"  total={r.get('total_ms', 0):7.1f} ms"
                f"  http={r.get('http_status')}"
            )


if __name__ == "__main__":
    run()
