# HarvestGate — ML Inference & Agronomic Advisory Gateway

HarvestGate is a high-performance, secure, and production-grade MLOps gateway for crop yield prediction and suitability scoring. It is built on FastAPI, ONNX Runtime, and Redis Caching, integrated with OpenRouter LLM for natural language advisory, and backed by a dual-orchestrated Prometheus/Grafana telemetry engine.

---

## System Architecture

The following diagram illustrates the request pipeline, data flow, caching behavior, and observability loop:

```mermaid
graph TD
    User([User / Test Script]) -->|POST /predict| Gateway[FastAPI Gateway]
    
    subgraph Gateway Engine
        Gateway -->|1. Size & Rate Check| GateGuard{Security Filters}
        GateGuard -->|Passed| CacheCheck{Redis Cache Lookup}
        
        CacheCheck -->|Cache HIT < 5ms| ReturnCached[Return Response]
        CacheCheck -->|Cache MISS| RunPreprocess[1. Run Joblib Preprocessor]
        
        RunPreprocess -->|2. Run ONNX Inference| ONNXRun[ONNX Runtime Simulator]
        ONNXRun -->|3. Score Yields| SuitScorer[Bayesian Acreage Scorer]
        
        SuitScorer -->|4. If Explain=True| OpenRouter[OpenRouter LLM Advisor]
        OpenRouter -->|5. Save Result| CacheSave[Redis SET Cache]
        
        CacheSave --> ReturnFresh[Return Live Response]
    end
    
    subgraph Observability & MLOps
        ReturnFresh -->|Async Background| DriftCheck[PSI Data Drift Detector]
        Gateway -->|Prometheus Metrics| ScrapeEndpoint[/metrics]
        ScrapeEndpoint -->|Local Scraping| PromLocal[Prometheus DB]
        ScrapeEndpoint -->|Cloud Scraping| GrafAlloy[Grafana Alloy]
        PromLocal --> GrafLocal[Local Grafana UI]
        GrafAlloy -->|Remote TLS Write| GrafCloud[Grafana Cloud]
    end
```

---

## Project Structure

```text
├── gateway/
│   ├── static/             # Reworked CSS/JS frontend client files
│   ├── cache.py            # Async Redis cache manager with graceful failover
│   ├── main.py             # FastAPI entrypoint, middlewares, and routers
│   ├── router.py           # Circuit breaker & fallback controller
│   └── schemas.py          # Strict Pydantic input/output schemas
├── inference/
│   ├── onnx_runner.py      # ONNX Runtime model execution wrapper
│   ├── openrouter_runner.py# Async OpenRouter LLM client with system constraints
│   └── scorer.py           # Relative yield normalization & Bayesian state priors
├── drift/
│   ├── detector.py         # Dynamic Population Stability Index (PSI) drift calculation
│   └── baseline_stats.json # Pre-computed baseline training statistics
├── monitoring/
│   ├── prometheus/         # Scrape target config files
│   ├── grafana/            # Pre-provisioned dashboards & data sources
│   └── alloy/              # Grafana Alloy cloud-forwarding config files
├── Dockerfile              # Production non-root slim container definition
├── docker-compose.yml      # Local self-contained orchestration stack
└── docker-compose.cloud.yml# Grafana Cloud remote-push forwarding stack
```

---

## Quickstart Guide

### 1. Environment Configurations
Create a `.env` file in the root of the project:
```env
# Server Port & Mode
ENV=production
PORT=8000

# OpenRouter API (Optional, degrades gracefully)
OPENROUTER_API_KEY=your_openrouter_api_key_here
OPENROUTER_MODEL=nvidia/nemotron-nano-9b-v2:free

# Redis Caching
REDIS_PASSWORD=harvestgate_secure_temp_pass_2026
REDIS_URL=redis://:harvestgate_secure_temp_pass_2026@redis:6379

# Grafana Local Dashboard Credentials
GRAFANA_ADMIN_PASSWORD=harvestgate_grafana_2026

# Grafana Cloud Credentials (Required only for Cloud-forwarding Compose)
GRAFANA_CLOUD_PROM_URL=https://prometheus-prod-xxxx.grafana.net/api/prom/push
GRAFANA_CLOUD_USER=123456
GRAFANA_CLOUD_TOKEN=glc_xxxx
```

---

### 2. Orchestration Stack Options

#### Option A: Local Self-Contained Stack (Offline Mode)
Runs the gateway, Redis, Prometheus, and Grafana entirely inside your local Docker runtime.
```bash
# Build and boot the stack
docker compose up --build -d

# Check running services
docker compose ps
```
*   **Web Client UI:** `http://localhost:8000`
*   **Metrics Scraper:** `http://localhost:9090` (Prometheus)
*   **Performance Dashboards:** `http://localhost:3000` (Log in with `admin` / `harvestgate_grafana_2026`)

#### Option B: Cloud Forwarding Stack (Grafana Cloud Mode)
Runs a lightweight stack on the host, scraping metrics and pushing them directly to your Grafana Cloud account using Grafana Alloy.
```bash
# Build and boot the cloud stack
docker compose -f docker-compose.cloud.yml up --build -d
```
*Alloy reads your `GRAFANA_CLOUD_PROM_URL` credentials from `.env` and securely forwards telemetry metrics over TLS.*

---

## API Reference

### 1. Yield Predictor / Crop Recommendations
*   **Endpoint:** `POST /predict`
*   **Description:** Simulates expected yields for all 19 crops (recommendation mode) or calculates a single crop's expected yield and fetches LLM agronomic advisories.
*   **Payload Schema:**
    ```json
    {
      "N": 80.0,
      "P": 40.0,
      "K": 25.0,
      "annual_rainfall": 1100.0,
      "kharif_rainfall": 800.0,
      "rabi_rainfall": 200.0,
      "irrigation_ratio": 0.35,
      "soil_type": "VERTISOLS",
      "state": "Uttar Pradesh",
      "crop": "RICE",
      "explain": true
    }
    ```
*   **Raw Curl Example:**
    ```bash
    curl -X POST "http://localhost:8000/predict" \
         -H "Content-Type: application/json" \
         -d '{"N":80.0,"P":40.0,"K":25.0,"annual_rainfall":1100.0,"kharif_rainfall":800.0,"rabi_rainfall":200.0,"irrigation_ratio":0.35,"soil_type":"VERTISOLS","state":"Uttar Pradesh","crop":"RICE","explain":true}'
    ```

### 2. Service Health Status
*   **Endpoint:** `GET /health`
*   **Description:** Returns availability status for the ONNX model runtime, Redis caching connection pool, and the active LLM circuit breaker state.
*   **Raw Curl Example:**
    ```bash
    curl -X GET "http://localhost:8000/health"
    ```

---

## Security Hardening Policies

1.  **Payload Size Cap:** All incoming request bodies are limited to a maximum of **2KB** by custom middleware. Oversized payloads are dropped instantly with `HTTP 413 Payload Too Large` to prevent memory exhaustion/DoS attacks.
2.  **Rate Limiting Guard:** Configured with SlowAPI limits (`30 requests/minute` per IP for predicting, `60/minute` for default lookups). Excessive requests are blocked with `HTTP 429 Too Many Requests` and prompt the frontend countdown lock overlay.
3.  **Command Rename Hardening:** Redis execution disables destructive global operations (`FLUSHALL`, `FLUSHDB`, `CONFIG`, `DEBUG` commands are renamed to `""` in the container launch script).
4.  **XSS Escaping:** The frontend Javascript client forces escaping of all characters inside LLM markdown responses, dynamically injecting HTML tags safely via raw text blocks to block malicious script injections.


