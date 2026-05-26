# Internet Quality Monitor

A self-hosted dashboard that measures your internet speed and latency every 3 hours, stores the history in SQLite, and renders an interactive web dashboard using Flask and Plotly.

## Features

- **Speed + bufferbloat tests** every 3 hours via Cloudflare's speed endpoint
  - Download / upload Mbps
  - Idle ping, loaded ping (bufferbloat grade)
- **Per-site latency probes** every 5 minutes across 7 major sites
  - DNS lookup / TCP connect / TLS handshake / TTFB breakdown
- **Interactive dashboard** with charts for:
  - Speed and ping trends (7-day and 30-day)
  - Speed distribution (histogram + boxplot)
  - ISP quality score by time of day
  - Weekly quality heatmap (day × 3-hour window)
  - Site latency breakdown (stacked bar)
- **Systemd user units** — no root required, runs in the background via timers
- **Mobile-responsive** — works on phone and tablet
- **Dark mode** — follows system preference

## Requirements

- Linux with systemd (tested on Arch Linux)
- Python 3.11+
- `ping` and `curl` available on PATH
- Internet connection

## Quick Start

```bash
# 1. Clone the repo — the systemd unit files assume this exact path
#    If you want a different path, edit the three files in systemd/ first
git clone https://github.com/yourusername/speed.git ~/Desktop/speed
cd ~/Desktop/speed

# 2. Create a virtual environment and install dependencies
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Smoke-test the speed runner (takes ~40 seconds)
.venv/bin/python runner.py

# 4. Smoke-test the site prober (takes ~5 seconds)
.venv/bin/python probe.py

# 5. Install systemd user units
mkdir -p ~/.config/systemd/user
ln -sf ~/Desktop/speed/systemd/speedtest.service           ~/.config/systemd/user/
ln -sf ~/Desktop/speed/systemd/speedtest.timer             ~/.config/systemd/user/
ln -sf ~/Desktop/speed/systemd/speedtest-probe.service     ~/.config/systemd/user/
ln -sf ~/Desktop/speed/systemd/speedtest-probe.timer       ~/.config/systemd/user/
ln -sf ~/Desktop/speed/systemd/speedtest-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload

# 6. Start everything
systemctl --user enable --now speedtest.timer speedtest-probe.timer speedtest-dashboard.service

# 7. Enable linger so units run when you're not logged in
sudo loginctl enable-linger $USER

# 8. Open the dashboard
#    Replace with your machine's IP if accessing from another device
xdg-open http://localhost:5000
```

## Configuration

### Timezone

Open `app.py` and change the `TZ` constant near the top to your local timezone:

```python
# Change this to your local timezone, e.g. 'America/New_York', 'Europe/London'
TZ = "Asia/Kolkata"
```

A full list of valid timezone strings is available at
https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

### Install path

The systemd unit files assume the project lives at `~/Desktop/speed`.
If you clone it elsewhere, edit all three `*.service` files and update the
`WorkingDirectory` and `ExecStart` lines before installing the units.

### Schedules

| Timer | Default | Edit |
|---|---|---|
| Speed test | Every 3 hours, clock-aligned (00:00, 03:00 … 21:00) | `systemd/speedtest.timer` |
| Site probes | Every 5 minutes | `systemd/speedtest-probe.timer` |

### Sites probed

`probe.py` tests 7 sites by default. Edit the `SITES` list near the top of the
file to add, remove, or swap sites:

```python
SITES = [
    ("cloudflare.com", "https://cloudflare.com"),
    ("google.com",     "https://google.com"),
    ...
]
```

## How Data is Stored

All data lives in a single SQLite file at `data/speedtest.db` (created
automatically on the first run). There are two tables:

### `results` — speed + bufferbloat measurements

Written by `runner.py` once per speed test run (~every 3 hours).

| Column | Type | Description |
|---|---|---|
| `ts` | TEXT | ISO-8601 UTC timestamp, e.g. `2026-05-26T12:00:00+00:00` |
| `download_mbps` | REAL | Download speed in Megabits per second |
| `upload_mbps` | REAL | Upload speed in Megabits per second |
| `ping_ms` | REAL | Idle ping (same as `idle_ping_ms`) |
| `server` | TEXT | Always `"Cloudflare edge"` |
| `server_id` | INTEGER | Always `NULL` (reserved) |
| `idle_ping_ms` | REAL | Median RTT to 1.1.1.1 before any load |
| `download_loaded_ping_ms` | REAL | Median RTT while downloading (bufferbloat) |
| `upload_loaded_ping_ms` | REAL | Median RTT while uploading (bufferbloat) |

**Bufferbloat grade** = `loaded_ping_ms - idle_ping_ms`. The lower the better:
A+ < 5 ms · A < 30 ms · B < 60 ms · C < 200 ms · D 200 ms+

Example row:
```
ts                        | download_mbps | upload_mbps | idle_ping_ms | download_loaded_ping_ms
2026-05-26T12:00:11+00:00 | 391.4         | 152.7        | 2.7          | 8.1
```

Inspect with:
```bash
sqlite3 data/speedtest.db \
  'SELECT ts, ROUND(download_mbps,1) AS down, ROUND(upload_mbps,1) AS up,
          ROUND(idle_ping_ms,1) AS idle,
          ROUND(download_loaded_ping_ms - idle_ping_ms, 1) AS bb_dl
   FROM results ORDER BY ts DESC LIMIT 10'
```

### `probes` — per-site latency breakdown

Written by `probe.py` every 5 minutes. One row per site per run (7 sites = 7 rows per run).

| Column | Type | Description |
|---|---|---|
| `ts` | TEXT | ISO-8601 UTC timestamp (same for all 7 rows in one run) |
| `site` | TEXT | Site label, e.g. `"cloudflare.com"` |
| `dns_ms` | REAL | DNS lookup time in milliseconds |
| `tcp_ms` | REAL | TCP connect time (after DNS) |
| `tls_ms` | REAL | TLS handshake time (after TCP connect) |
| `ttfb_ms` | REAL | Time to first byte (after TLS) |
| `total_ms` | REAL | Total end-to-end time |
| `http_status` | INTEGER | HTTP response code (200, 202, …) |
| `error` | TEXT | `NULL` on success; error string on failure |

The latency breakdown shows where time is actually spent:
- **DNS + TCP/TLS slow** → ISP routing or peering issue
- **TTFB slow** → website server problem, not your ISP

Inspect with:
```bash
sqlite3 data/speedtest.db \
  'SELECT ts, site, ROUND(dns_ms,1), ROUND(tcp_ms,1),
          ROUND(tls_ms,1), ROUND(ttfb_ms,1)
   FROM probes ORDER BY ts DESC LIMIT 14'
```

## Architecture

```
runner.py   (oneshot, every 3h)    → writes 1 row  → data/speedtest.db
probe.py    (oneshot, every 5min)  → writes 7 rows → data/speedtest.db
app.py      (long-running)         → reads on demand from data/speedtest.db
                                   → serves http://localhost:5000
```

The three scripts share nothing except the SQLite file — they do not call each
other and do not need to run at the same time.

## Cheat Sheet

```bash
# Trigger a speed test right now
systemctl --user start speedtest.service

# Trigger a site probe right now
systemctl --user start speedtest-probe.service

# Restart dashboard after editing app.py or templates/
systemctl --user restart speedtest-dashboard.service

# Tail logs
journalctl --user -u speedtest.service -f
journalctl --user -u speedtest-probe.service -f
journalctl --user -u speedtest-dashboard.service -f

# Check when timers fire next
systemctl --user list-timers speedtest.timer speedtest-probe.timer

# Stop everything
systemctl --user disable --now speedtest.timer speedtest-probe.timer speedtest-dashboard.service
```

## Exposing Publicly (optional)

The dashboard binds to `0.0.0.0:5000` and is already reachable on your LAN.
To expose it to the internet without opening a router port, use
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):

```yaml
# In your cloudflared config.yml, add above the catch-all:
ingress:
  - hostname: speed.yourdomain.com
    service: http://localhost:5000
  - service: http_status:404
```

```bash
cloudflared tunnel route dns <tunnel-name> speed.yourdomain.com
sudo systemctl restart cloudflared
```

## License

MIT
