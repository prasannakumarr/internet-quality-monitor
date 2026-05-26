"""Flask dashboard for speedtest history."""
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from flask import Flask, redirect, render_template, url_for

DB = Path(__file__).parent / "data" / "speedtest.db"
# Change this to your local timezone, e.g. 'America/New_York', 'Europe/London'
TZ = "Asia/Kolkata"
app = Flask(__name__)


def load_df() -> pd.DataFrame:
    if not DB.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB) as c:
        df = pd.read_sql_query(
            "SELECT ts, download_mbps, upload_mbps, ping_ms, server FROM results ORDER BY ts",
            c,
            parse_dates=["ts"],
        )
    if not df.empty:
        df["ts"] = df["ts"].dt.tz_convert(TZ).dt.tz_localize(None)
    return df


def speed_chart(window: pd.DataFrame, title: str, include_js: bool) -> str:
    long = window.melt(
        id_vars=["ts"],
        value_vars=["download_mbps", "upload_mbps"],
        var_name="metric",
        value_name="Mbps",
    )
    long["metric"] = long["metric"].map({"download_mbps": "Download", "upload_mbps": "Upload"})
    fig = px.line(long, x="ts", y="Mbps", color="metric", markers=True,
                  labels={"ts": "Time"}, title=title)
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40), legend_title_text="")
    return fig.to_html(full_html=False, include_plotlyjs=("cdn" if include_js else False),
                       config={"responsive": True, "displayModeBar": False})


def ping_chart(window: pd.DataFrame, title: str) -> str:
    fig = px.line(window, x="ts", y="ping_ms", markers=True,
                  labels={"ts": "Time", "ping_ms": "Ping (ms)"}, title=title)
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40))
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})


def speed_histogram(window: pd.DataFrame, column: str, title: str) -> str:
    fig = px.histogram(window, x=column, nbins=30, title=title,
                       labels={column: "Mbps"})
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40), yaxis_title="Count")
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})


def speed_boxplot(window: pd.DataFrame, title: str) -> str:
    long = window.melt(
        value_vars=["download_mbps", "upload_mbps"],
        var_name="metric", value_name="Mbps",
    )
    long["metric"] = long["metric"].map({"download_mbps": "Download", "upload_mbps": "Upload"})
    fig = px.box(long, x="metric", y="Mbps", color="metric", points="all", title=title)
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40), legend_title_text="",
                      xaxis_title="")
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})


def ping_histogram(window: pd.DataFrame, title: str) -> str:
    fig = px.histogram(window, x="ping_ms", nbins=30, title=title,
                       labels={"ping_ms": "Ping (ms)"})
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40), yaxis_title="Count")
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})


def ping_boxplot(window: pd.DataFrame, title: str) -> str:
    fig = px.box(window, y="ping_ms", points="all", title=title,
                 labels={"ping_ms": "Ping (ms)"})
    fig.update_layout(margin=dict(l=40, r=20, t=50, b=40))
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})


@app.route("/")
def index():
    df = load_df()
    if df.empty:
        return render_template("index.html", count=0)

    now = df["ts"].max()
    last_7d = df[df["ts"] >= now - pd.Timedelta(days=7)]
    last_30d = df[df["ts"] >= now - pd.Timedelta(days=30)]

    charts = {
        "speed_7d": speed_chart(last_7d, "Speed - last 7 days", include_js=True),
        "ping_7d": ping_chart(last_7d, "Ping - last 7 days"),
        "speed_30d": speed_chart(last_30d, "Speed - last 30 days", include_js=False),
        "ping_30d": ping_chart(last_30d, "Ping - last 30 days"),
        "speed_30d_hist_down": speed_histogram(last_30d, "download_mbps", "Speed distribution - last 30 days (download)"),
        "speed_30d_hist_up": speed_histogram(last_30d, "upload_mbps", "Speed distribution - last 30 days (upload)"),
        "speed_30d_box": speed_boxplot(last_30d, "Speed spread - last 30 days"),
        "ping_30d_hist": ping_histogram(last_30d, "Ping distribution - last 30 days"),
        "ping_30d_box": ping_boxplot(last_30d, "Ping spread - last 30 days"),
        "isp_hourly":  hourly_quality_chart(last_30d, include_js=False),
        "isp_heatmap": weekly_heatmap(last_30d, include_js=False),
    }

    descriptions = {
        "speed_7d": "Shows how fast your internet downloads and uploads files over the past week. Steady high lines = good performance; drops mean your ISP had issues.",
        "ping_7d": "Measures how quickly your device talks to servers (in milliseconds). <strong>Good: 0–60ms | Medium: 60–100ms | Bad: 100ms+</strong>—lower is better; spikes mean delays in video calls or gaming.",
        "speed_30d": "Your download and upload trends over a full month. Use this to spot patterns—does speed drop on weekends or evenings? It helps you file complaints with evidence.",
        "ping_30d": "Your long-term responsiveness over 30 days. <strong>Aim for under 60ms consistently.</strong> Stable low ping = smooth video conferencing and gaming; high/jumpy ping = frustrating delays.",
        "speed_30d_hist_down": "Shows how often you get different download speeds over 30 days—is your internet consistently fast, or does it bounce around? A tight cluster = reliable; spread out = unreliable.",
        "speed_30d_hist_up": "Shows upload speed consistency (sending files, video calls, cloud uploads). If it's all over the map, your ISP isn't delivering what you're paying for consistently.",
        "speed_30d_box": "Compares the range of your download vs. upload speeds—one might be stable while the other is wildly inconsistent. Helps you understand where problems lie.",
        "ping_30d_hist": "Shows how often your connection is snappy vs. sluggish. <strong>Most pings should cluster in the 20–60ms range.</strong> If scattered or mostly 100ms+, your ISP has serious problems.",
        "ping_30d_box": "Displays high/low/median ping to spot if your ISP's response time is reliable. <strong>Median should stay under 60ms; if jumping above 100ms, file a complaint.</strong> Helps identify if problems happen at specific times.",
        "isp_hourly": "Shows which hours your internet is best/worst (9 AM vs 9 PM). Use this to schedule important work when quality is highest, or file complaints about peak-hour drops.",
        "isp_heatmap": "A visual map showing the best/worst times each day of the week. Red = poor quality, green = excellent. Find patterns: 'Saturdays at 8 PM always stink.'",
        "isp_probes": "<strong>TL;DR:</strong> Fast DNS + TCP/TLS = Your ISP is great. Slow TTFB = Website's problem, not ours. Breaks down why websites are slow—is it DNS lookup, connecting, secure handshake, or server response? Helps prove if the problem is your ISP or the website itself.",
    }

    pdf = load_probes_df()
    if not pdf.empty:
        charts["isp_probes"] = probe_stacked_bar(pdf, include_js=False)

    latest = df.iloc[-1]
    down_30d = last_30d["download_mbps"]
    summary = {
        "avg_down": round(last_30d["download_mbps"].mean(), 1),
        "avg_up": round(last_30d["upload_mbps"].mean(), 1),
        "avg_ping": round(last_30d["ping_ms"].mean(), 1),
        "min_down": round(last_30d["download_mbps"].min(), 1),
        "max_down": round(last_30d["download_mbps"].max(), 1),
        "std_down_30d": round(down_30d.std(), 1),
        "p90_down_30d": round(down_30d.quantile(0.90), 1),
        "p95_down_30d": round(down_30d.quantile(0.95), 1),
    }
    isp_days = round((last_30d["ts"].max() - last_30d["ts"].min()).total_seconds() / 86400, 1)
    return render_template(
        "index.html",
        count=len(df),
        latest=latest,
        summary=summary,
        charts=charts,
        descriptions=descriptions,
        headline=isp_headline(last_30d),
        isp_days=isp_days,
        has_probes=not pdf.empty,
    )


def load_probes_df() -> pd.DataFrame:
    if not DB.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB) as c:
        try:
            df = pd.read_sql_query(
                "SELECT ts, site, dns_ms, tcp_ms, tls_ms, ttfb_ms "
                "FROM probes WHERE error IS NULL AND http_status < 400 ORDER BY ts",
                c, parse_dates=["ts"],
            )
        except Exception:
            return pd.DataFrame()
    if not df.empty:
        df["ts"] = df["ts"].dt.tz_convert(TZ).dt.tz_localize(None)
    return df


def isp_headline(df: pd.DataFrame) -> dict | None:
    if len(df) < 10:
        return None
    peak_down = df["download_mbps"].quantile(0.9)
    best_ping = df["ping_ms"].quantile(0.1)
    good = (df["download_mbps"] >= peak_down * 0.85) & (df["ping_ms"] <= best_ping * 2.5)
    good_pct = round(good.mean() * 100)
    tmp = df.copy()
    tmp["bucket"] = (tmp["ts"].dt.hour // 3) * 3
    buckets = tmp.groupby("bucket").agg(
        down=("download_mbps", "median"),
        ping=("ping_ms", "median"),
    )
    buckets["quality"] = (
        buckets["down"].clip(upper=peak_down) / peak_down * 0.6
        + best_ping / buckets["ping"].clip(lower=best_ping) * 0.4
    )
    worst_b = int(buckets["quality"].idxmin())
    return {
        "good_pct":   good_pct,
        "worst_hour": f"{worst_b:02d}:00–{(worst_b + 3) % 24:02d}:00",
        "worst_down": round(buckets.loc[worst_b, "down"], 1),
        "peak_down":  round(peak_down, 1),
        "worst_ping": round(buckets.loc[worst_b, "ping"], 1),
        "best_ping":  round(best_ping, 1),
        "days":       round((df["ts"].max() - df["ts"].min()).total_seconds() / 86400, 1),
    }


def hourly_quality_chart(df: pd.DataFrame, include_js: bool) -> str:
    tmp = df.copy()
    tmp["bucket"] = (tmp["ts"].dt.hour // 3) * 3
    peak_down = tmp["download_mbps"].quantile(0.9)
    best_ping = tmp["ping_ms"].quantile(0.1)
    buckets = tmp.groupby("bucket").agg(
        down=("download_mbps", "median"),
        ping=("ping_ms", "median"),
    ).reindex(range(0, 24, 3))
    buckets["quality"] = (
        buckets["down"].clip(upper=peak_down) / peak_down * 0.6
        + best_ping / buckets["ping"].clip(lower=best_ping) * 0.4
    ).fillna(0) * 100
    colors = [
        "#2ca02c" if q >= 85 else ("#ff7f0e" if q >= 70 else "#d62728")
        for q in buckets["quality"]
    ]
    labels = [f"{b:02d}:00–{(b+3):02d}:00" for b in range(0, 24, 3)]
    fig = go.Figure(go.Bar(
        x=labels,
        y=buckets["quality"].round(1),
        marker_color=colors,
        text=[f"{q:.0f}%" for q in buckets["quality"]],
        textposition="outside",
        hovertemplate="%{x} IST<br>Quality: %{y:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        title="ISP quality score by 3-hour window (IST)",
        xaxis=dict(title="Time window (IST)"),
        yaxis=dict(title="Quality score (0–100)", range=[0, 120]),
        margin=dict(l=50, r=20, t=60, b=50),
        showlegend=False,
    )
    return fig.to_html(full_html=False, include_plotlyjs=("cdn" if include_js else False),
                       config={"responsive": True, "displayModeBar": False})


def weekly_heatmap(df: pd.DataFrame, include_js: bool) -> str:
    tmp = df.copy()
    tmp["bucket"] = (tmp["ts"].dt.hour // 3) * 3
    tmp["bucket_label"] = tmp["bucket"].apply(lambda b: f"{b:02d}–{(b+3):02d}")
    tmp["dow"] = tmp["ts"].dt.day_name()
    peak_down = tmp["download_mbps"].quantile(0.9)
    best_ping = tmp["ping_ms"].quantile(0.1)

    def cell_score(sub):
        if len(sub) < 3:
            return float("nan")
        s = min(sub["download_mbps"].median() / peak_down, 1.0)
        l = min(best_ping / sub["ping_ms"].median(), 1.0)
        return round((0.6 * s + 0.4 * l) * 100, 1)

    scores = tmp.groupby(["dow", "bucket_label"]).apply(cell_score).reset_index(name="score")
    days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = scores.pivot(index="dow", columns="bucket_label", values="score").reindex(days_order)
    fig = px.imshow(
        pivot,
        color_continuous_scale=[[0, "#d62728"], [0.5, "#ffff00"], [1.0, "#2ca02c"]],
        zmin=0, zmax=100,
        labels={"x": "Time window (IST)", "y": "", "color": "Quality"},
        title="Quality heatmap — by day & 3-hour window (IST) · grey = fewer than 3 samples",
        aspect="auto",
    )
    fig.update_layout(margin=dict(l=90, r=20, t=60, b=50))
    return fig.to_html(full_html=False, include_plotlyjs=("cdn" if include_js else False),
                       config={"responsive": True, "displayModeBar": False})


def probe_stacked_bar(pdf: pd.DataFrame, include_js: bool) -> str:
    cutoff = pdf["ts"].max() - pd.Timedelta(days=7)
    recent = pdf[pdf["ts"] >= cutoff]
    avg = recent.groupby("site")[["dns_ms", "tcp_ms", "tls_ms", "ttfb_ms"]].median().reset_index()
    avg["total"] = avg[["dns_ms", "tcp_ms", "tls_ms", "ttfb_ms"]].sum(axis=1)
    avg = avg.sort_values("total")
    fig = go.Figure()
    for col, label, color in [
        ("dns_ms",  "DNS lookup",             "#636EFA"),
        ("tcp_ms",  "TCP connect",            "#EF553B"),
        ("tls_ms",  "TLS handshake",          "#00CC96"),
        ("ttfb_ms", "Server response (TTFB)", "#AB63FA"),
    ]:
        fig.add_trace(go.Bar(name=label, x=avg["site"], y=avg[col], marker_color=color))
    fig.update_layout(
        barmode="stack",
        title="Where time goes loading each site — median, last 7 days",
        xaxis_title="",
        yaxis_title="ms",
        margin=dict(l=50, r=20, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig.to_html(full_html=False, include_plotlyjs=("cdn" if include_js else False),
                       config={"responsive": True, "displayModeBar": False})


@app.route("/isp")
def isp():
    return redirect(url_for("index") + "#isp")


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000, threads=4)
