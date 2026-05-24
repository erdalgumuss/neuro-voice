# Runbook — Observability stack (Prometheus + Grafana)

**Owner:** Backend lead · **Stack:** Prometheus + Grafana
**Companion:** [latency-bench.md](latency-bench.md) for capturing per-hardware baselines.

NQAI Voice ships a Prometheus exporter on both the gateway (`/metrics` on the FastAPI port) and the worker (`start_http_server` on `NQAI_WORKER_METRICS_PORT`, default 9100). This doc explains how to wire those into a working dashboard + alerting setup.

## Assets in this repo

| File | What it is |
|---|---|
| [`deploy/grafana/dashboards/nqai-voice.json`](../../deploy/grafana/dashboards/nqai-voice.json) | Importable Grafana dashboard (schema v39). RED + waterfall panels + queue / worker health. |
| [`deploy/prometheus/alerts.yml`](../../deploy/prometheus/alerts.yml) | Alert rules. 7 rules across availability / saturation / latency. |
| [`deploy/prometheus/prometheus.yml.example`](../../deploy/prometheus/prometheus.yml.example) | Scrape config template (gateway + worker). |

## Importing the dashboard

1. Provision Prometheus as a Grafana datasource named `Prometheus` (UID matches the `${datasource}` template variable in the JSON).
2. Grafana UI → Dashboards → New → Import → upload `nqai-voice.json` (or paste contents).
3. Tenant + voice template variables auto-populate from `label_values(nqai_tts_requests_total, …)` once the gateway has served at least one request and Prometheus has scraped it.

## Wiring Prometheus

```bash
# In your Prometheus deployment dir:
cp neeko-voice/deploy/prometheus/prometheus.yml.example prometheus.yml
cp neeko-voice/deploy/prometheus/alerts.yml alerts.yml

# Adjust scrape targets — replace gateway:8000 / worker:9100 with
# your actual service DNS or pod selectors.

prometheus --config.file=prometheus.yml --web.enable-lifecycle
```

For Kubernetes, replace `static_configs` with `kubernetes_sd_configs` selecting the gateway and worker pods.

## Panel-by-panel guide

The dashboard groups panels by reading order:

**Top stat row** — at-a-glance cluster health.
- *Healthy workers* (red < 1, yellow = 1, green ≥ 2)
- *Cluster capacity* — sum of declared worker capacity
- *In-flight jobs* — current concurrent jobs across the cluster
- *Queue depth* — `nqai_queue_depth{stream="jobs"}` (XLEN)
- *Success rate (5m)* — `success / total` over 5m
- *DLQ count (1h)* — increase in DLQ over the last hour

**Request panels** — RED top-line.
- *TTS request rate by status* — stack of success / error / backpressure
- *Errors by type* — poison / transient / unknown / dlq

**Latency panels** — the waterfall.
- *First audio (worker-side)* — p50/p95/p99 of `nqai_tts_first_audio_seconds`. This is "engine finished publishing first byte to Redis."
- *Gateway first byte (client-facing TTFB)* — p50/p95/p99 of `nqai_tts_gateway_first_byte_seconds`. This is what your customer's HTTP client actually sees as TTFB on `/v1/tts/stream`.
- *Inference duration (model only)* — `nqai_tts_inference_seconds`. Pure model time.
- *Total wall time (worker-side)* — `nqai_tts_total_seconds`. Pipeline start → archive + DB commit done.

**Stage panels** — drill into the parts.
- *Queue wait p95 vs worker pickup p95* — if these are big, the queue is the bottleneck.
- *Reference resolve p95 (R2 / cache)* — if this is big, your R2 cache is missing.

The gap between **First audio** and **Gateway first byte** is the gateway + transport overhead. If this gap is sustained > 100 ms (p95), HTTP framing / ASGI buffering is dominating client TTFB — see the `NqaiVoiceGatewayTransportOverhead` alert.

## Alert tuning

The shipped thresholds in `alerts.yml` are starter values. After your first real-traffic week:

1. Run `scripts/latency_bench.py` to get a per-hardware baseline.
2. Look at one week of actual `histogram_quantile` values for the latency rules.
3. Set the alert threshold at `~1.5 × observed p95`.
4. Set the `for:` window long enough to filter normal traffic spikes — 10 m is conservative; raise to 30 m if a deploy noticeably moves things.

Don't loosen `NqaiVoiceDlqGrowing` — every DLQ entry is an unhandled failure mode worth investigating. If you suppress this, you're allowing silent data loss.

## What the dashboard does NOT show (yet)

- Per-voice cold-load latency. Faz C v1 follow-up will add `nqai_worker_cold_load_seconds{voice_id}` once we wire sticky routing.
- pg connection pool saturation. Faz C v1 item 4 will add pgBouncer + an exporter.
- Tracing exemplars. Wired separately in Faz C+ when OTel is on.

## Quick sanity-check on a running deployment

```bash
# Gateway exporter
curl -s http://localhost:8000/metrics | grep -E 'nqai_tts_(requests|first_audio)_'

# Worker exporter (default port 9100)
curl -s http://localhost:9100/metrics | grep -E 'nqai_worker_(count|capacity)_total'
```

If both return data, the stack is wired. If the worker returns nothing, check `NQAI_WORKER_METRICS_PORT` and the firewall between Prometheus and the worker pod.
