# HarvestGate — ML Inference Gateway + Monitoring Stack
## Complete Project Plan & Implementation Guide

---

## 0. Context & Purpose

This document is a full handoff brief for an AI agent or developer taking over this project. It contains everything needed to understand what's been done, what needs to be built, and why each decision was made.

**Owner:** Biswajit (BCA Graduate, targeting ML Engineer internships)  
**Goal:** Build a production-grade ML inference layer that demonstrates ability to operate ML systems — not just train them. This is the third project in a portfolio narrative: Data Pipelines → ML Modeling → **Production Inference & Monitoring (this project)** → LLM Systems.

**Portfolio narrative this project completes:**  
> "I can build models AND deploy, route, cache, monitor, and detect drift on them in production."

---

## 1. What Has Already Been Done

### 1.1 Source Project: HarvestML / Multilingual AI Crop Intelligence
- Located at: `C:\Users\Biswajitrk\Documents\COdezzz\ML Based Crop Recommendation System\`
- Built on ICRISAT dataset (66,600 rows), XGBoost, 87.26% R²
- Contains three `.joblib` files in the `models/` directory:

| File | Type | Purpose |
|---|---|---|
| `tuned_simulator.joblib` | `xgboost.sklearn.XGBRegressor` | Yield prediction model |
| `simulator_preprocessor.joblib` | `sklearn.compose.ColumnTransformer` | Raw input → model-ready features |
| `test_indices.joblib` | `pandas.Index` | Test row IDs — not needed for gateway |

### 1.2 ONNX Conversion — COMPLETED
- `tuned_simulator.joblib` has been successfully converted to ONNX format
- Tool used: `onnxmltools.convert.convert_xgboost`
- Output file: `harvestml_simulator.onnx`
- Verified loading via `onnxruntime.InferenceSession` ✅
- **ONNX model input:** `float_input`, shape `[None, 7]`, type `tensor(float)`
- This means the ONNX model expects **post-preprocessed** numeric features, not raw input

### 1.3 Critical Architecture Decision: Split Preprocessing
Because the full sklearn Pipeline (ColumnTransformer + XGBRegressor) could not be converted to ONNX as a single unit due to version incompatibilities between `skl2onnx 1.20.0` and XGBoost, the preprocessing and inference are split:

```
Raw Input (JSON)
      ↓
simulator_preprocessor.joblib  ← runs in Python/scikit-learn inside gateway
      ↓
7 float features
      ↓
harvestml_simulator.onnx  ← runs in ONNX Runtime
      ↓
Yield prediction (float)
```

Both files must be present in the gateway's `models/` directory.

### 1.4 Files To Copy Into New Project
From `ML Based Crop Recommendation System\models\`:
- `harvestml_simulator.onnx`
- `simulator_preprocessor.joblib`

---

## 2. New Project Structure

**Project name:** `harvestgate`  
**New repo:** Separate GitHub repository, independent of HarvestML  
**Location:** `C:\Users\Biswajitrk\Documents\COdezzz\harvestgate\`

```
harvestgate/
├── gateway/
│   ├── main.py                  # FastAPI app — entry point
│   ├── router.py                # Routing logic (ONNX vs Groq)
│   ├── cache.py                 # Redis cache interface
│   └── schemas.py               # Pydantic request/response models
├── models/
│   ├── harvestml_simulator.onnx # ONNX yield prediction model
│   └── simulator_preprocessor.joblib  # sklearn ColumnTransformer
├── inference/
│   ├── onnx_runner.py           # ONNX Runtime inference wrapper
│   └── groq_runner.py           # Groq API LLM inference wrapper
├── monitoring/
│   ├── metrics.py               # Prometheus metrics definitions
│   ├── prometheus.yml           # Prometheus scrape config
│   └── grafana/
│       └── dashboard.json       # Pre-built Grafana dashboard
├── drift/
│   └── detector.py              # PSI-based drift detection
├── docker-compose.yml           # Spins up entire stack
├── Dockerfile                   # Gateway container
├── requirements.txt
└── README.md                    # With live Grafana URL
```

---

## 3. Preprocessor Feature Reference

The `simulator_preprocessor.joblib` ColumnTransformer expects these exact columns:

### Numeric Features (StandardScaler)
| Column | Unit | Description |
|---|---|---|
| `N (Kg/ha)` | float | Nitrogen content |
| `P (Kg/ha)` | float | Phosphorus content |
| `K (Kg/ha)` | float | Potassium content |
| `Annual Rainfall (mm)` | float | Total annual rainfall |
| `Kharif Rainfall (mm)` | float | Kharif season rainfall |
| `Rabi Rainfall (mm)` | float | Rabi season rainfall |
| `Irrigation Ratio` | float | Ratio of irrigated area |

### Categorical Features (OneHotEncoder)
| Column | Type | Description |
|---|---|---|
| `Primary Soil Type` | string | Soil classification |
| `State Name` | string | Indian state name |
| `Crop` | string | Crop type |

**Output:** 7 floats (after OHE expansion is handled internally by the preprocessor)

> ⚠️ Agent Note: The ONNX model takes exactly 7 floats as input. The ColumnTransformer handles the OneHotEncoding internally and produces a flattened numeric array. Do not manually one-hot encode before passing to the preprocessor.

---

## 4. System Architecture

```
                        ┌─────────────────────────────────┐
                        │         CLIENT REQUEST           │
                        │  POST /predict                   │
                        │  { N, P, K, rainfall, soil,     │
                        │    state, crop }                 │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │        FASTAPI GATEWAY           │
                        │  - Receives all requests         │
                        │  - Logs request metadata         │
                        │  - Returns unified response      │
                        └──────┬───────────────┬──────────┘
                               │               │
               ┌───────────────▼──┐         ┌──▼──────────────────┐
               │   REDIS CACHE    │         │      ROUTER          │
               │  Check input     │   miss  │  structured input    │
               │  hash first      ├─────────►  → ONNX backend      │
               │  Return if hit   │         │  + explanation?      │
               └───────────────┬──┘         │  → Groq backend      │
                               │ hit        └──┬──────────────────┘
                               │               │
                        ┌──────▼───────┐   ┌───▼──────────────────┐
                        │ Cached       │   │   INFERENCE LAYER     │
                        │ Response     │   │                       │
                        └──────────────┘   │  Backend 1: ONNX      │
                                           │  - preprocessor.joblib│
                                           │  - simulator.onnx     │
                                           │  → yield float        │
                                           │                       │
                                           │  Backend 2: Groq API  │
                                           │  - Llama 3 via Groq   │
                                           │  → natural language   │
                                           │    explanation        │
                                           └───┬──────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────┐
                        │           MONITORING LAYER               │
                        │  - Log prediction + latency             │
                        │  - Update Prometheus metrics            │
                        │  - Run PSI drift check on features      │
                        │  - Fire alert if PSI > threshold        │
                        └──────────────────────┬──────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────┐
                        │         PROMETHEUS + GRAFANA             │
                        │  Live dashboard: latency, throughput,   │
                        │  cache hit rate, error rate, PSI score  │
                        └─────────────────────────────────────────┘
```

---

## 5. Component-by-Component Implementation Plan

---

### 5.1 FastAPI Gateway (`gateway/main.py`)

**What it does:** Entry point for all requests. Orchestrates cache check → routing → inference → monitoring.

**Endpoints to implement:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/predict` | POST | Main prediction endpoint |
| `/predict/explain` | POST | Prediction + LLM explanation |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics scrape endpoint |

**Request body schema (`gateway/schemas.py`):**
```python
class PredictRequest(BaseModel):
    N: float           # Nitrogen Kg/ha
    P: float           # Phosphorus Kg/ha
    K: float           # Potassium Kg/ha
    annual_rainfall: float
    kharif_rainfall: float
    rabi_rainfall: float
    irrigation_ratio: float
    soil_type: str
    state: str
    crop: str
    explain: bool = False   # If True, also call Groq for explanation
```

**Response schema:**
```python
class PredictResponse(BaseModel):
    predicted_yield: float
    unit: str = "Kg/ha"
    explanation: Optional[str] = None
    cached: bool
    latency_ms: float
    model_backend: str   # "onnx" or "groq"
```

---

### 5.2 Router (`gateway/router.py`)

**What it does:** Decides which backend handles the request.

**Routing logic:**
```
if request.explain == False:
    → ONNX backend only (fast, local, no API cost)
    
if request.explain == True:
    → ONNX backend first (get yield prediction)
    → Then Groq backend (generate explanation using prediction)
    → Combine both into single response
```

**Why this routing is defensible in interviews:**
- ONNX for structured prediction = <10ms latency, zero API cost
- Groq for explanation = ~500ms-2s, uses API quota
- Only call Groq when user explicitly asks for explanation
- This is cost-aware routing — a real production concern

---

### 5.3 Redis Cache (`gateway/cache.py`)

**What it does:** Prevents redundant inference on repeated inputs.

**Cache key strategy:**
```python
import hashlib, json

def make_cache_key(request: PredictRequest) -> str:
    # Serialize request to deterministic string, then hash it
    payload = json.dumps(request.dict(), sort_keys=True)
    return f"predict:{hashlib.md5(payload.encode()).hexdigest()}"
```

**Cache flow:**
1. Hash incoming request
2. Check Redis: `GET cache_key`
3. If hit → return cached response, increment hit counter
4. If miss → run inference, `SET cache_key response EX 3600` (1hr TTL)

**Metrics to expose:**
- `cache_hits_total` (counter)
- `cache_misses_total` (counter)
- Cache hit rate = hits / (hits + misses)

**TTL Decision (interview talking point):**  
1 hour TTL balances freshness vs compute savings. Agricultural predictions don't change minute-to-minute. If model is updated, flush cache via `FLUSHDB` on deployment.

---

### 5.4 ONNX Inference Runner (`inference/onnx_runner.py`)

**What it does:** Loads preprocessor + ONNX model, runs inference on raw input.

```python
import joblib
import numpy as np
import onnxruntime as rt
import pandas as pd

class ONNXRunner:
    def __init__(self, model_path: str, preprocessor_path: str):
        self.session = rt.InferenceSession(model_path)
        self.preprocessor = joblib.load(preprocessor_path)
        self.input_name = self.session.get_inputs()[0].name  # "float_input"
    
    def predict(self, request: PredictRequest) -> float:
        # Build DataFrame matching preprocessor's expected column names
        df = pd.DataFrame([{
            "N (Kg/ha)": request.N,
            "P (Kg/ha)": request.P,
            "K (Kg/ha)": request.K,
            "Annual Rainfall (mm)": request.annual_rainfall,
            "Kharif Rainfall (mm)": request.kharif_rainfall,
            "Rabi Rainfall (mm)": request.rabi_rainfall,
            "Irrigation Ratio": request.irrigation_ratio,
            "Primary Soil Type": request.soil_type,
            "State Name": request.state,
            "Crop": request.crop
        }])
        
        # Preprocess → 7 floats
        features = self.preprocessor.transform(df).astype(np.float32)
        
        # ONNX inference
        result = self.session.run(None, {self.input_name: features})
        return float(result[0][0])
```

> ⚠️ Agent Note: Column names in the DataFrame must exactly match the strings the ColumnTransformer was trained on. Case-sensitive. See Section 3 for exact column names.

---

### 5.5 Groq Inference Runner (`inference/groq_runner.py`)

**What it does:** Takes ONNX prediction + input features, generates a human-readable agricultural explanation.

**Model:** `llama3-8b-8192` via Groq API (free tier available)

**Prompt template:**
```python
EXPLANATION_PROMPT = """
You are an agricultural advisor AI. Based on the following soil and climate conditions, 
a crop yield prediction model has estimated the yield.

Input conditions:
- Crop: {crop}
- State: {state}
- Soil Type: {soil_type}
- Nitrogen: {N} Kg/ha, Phosphorus: {P} Kg/ha, Potassium: {K} Kg/ha
- Annual Rainfall: {annual_rainfall}mm
- Irrigation Ratio: {irrigation_ratio}

Predicted Yield: {predicted_yield:.2f} Kg/ha

Provide a 2-3 sentence agronomic explanation of this prediction. 
Mention one specific actionable recommendation for the farmer.
Keep it practical and concise.
"""
```

---

### 5.6 Prometheus Metrics (`monitoring/metrics.py`)

**What it does:** Defines all metrics the gateway exposes at `/metrics`.

**Metrics to implement:**

```python
from prometheus_client import Counter, Histogram, Gauge

# Request volume
REQUEST_COUNT = Counter(
    'gateway_requests_total',
    'Total prediction requests',
    ['endpoint', 'backend', 'status']
)

# Latency
REQUEST_LATENCY = Histogram(
    'gateway_request_duration_seconds',
    'Request latency in seconds',
    ['endpoint', 'backend'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Cache
CACHE_HITS = Counter('gateway_cache_hits_total', 'Cache hits')
CACHE_MISSES = Counter('gateway_cache_misses_total', 'Cache misses')

# Drift
PSI_SCORE = Gauge('gateway_psi_score', 'Current PSI drift score', ['feature'])
DRIFT_ALERTS = Counter('gateway_drift_alerts_total', 'Drift threshold breaches')

# Prediction distribution
PREDICTION_VALUES = Histogram(
    'gateway_prediction_values',
    'Distribution of predicted yield values',
    buckets=[500, 1000, 2000, 3000, 4000, 5000, 7500, 10000]
)
```

---

### 5.7 Drift Detector (`drift/detector.py`)

**What it does:** Compares incoming feature distributions against training baseline using PSI (Population Stability Index).

**Why PSI over KL Divergence (interview answer):**  
PSI handles zero-frequency bins gracefully by adding a small epsilon — KL divergence goes to infinity when a bin has zero probability in either distribution. PSI is also threshold-interpretable: PSI < 0.1 = stable, 0.1–0.25 = moderate drift, > 0.25 = significant drift.

**Implementation approach:**

```python
import numpy as np

class DriftDetector:
    def __init__(self, baseline_stats: dict):
        # baseline_stats loaded from training data distribution
        # Contains mean, std, min, max, bin_edges for each numeric feature
        self.baseline = baseline_stats
        self.PSI_THRESHOLD = 0.25
    
    def compute_psi(self, baseline_dist, current_dist, bins=10) -> float:
        # PSI = sum((actual% - expected%) * ln(actual% / expected%))
        epsilon = 1e-6
        psi = 0
        for i in range(len(baseline_dist)):
            expected = baseline_dist[i] + epsilon
            actual = current_dist[i] + epsilon
            psi += (actual - expected) * np.log(actual / expected)
        return psi
    
    def check(self, incoming_features: dict) -> dict:
        results = {}
        for feature in ["N", "P", "K", "annual_rainfall"]:
            psi = self.compute_psi(
                self.baseline[feature],
                self._bin_value(incoming_features[feature], feature)
            )
            results[feature] = psi
            if psi > self.PSI_THRESHOLD:
                self._fire_alert(feature, psi)
        return results
    
    def _fire_alert(self, feature: str, psi: float):
        # Log alert + update Prometheus gauge
        print(f"DRIFT ALERT: {feature} PSI={psi:.4f} exceeds threshold {self.PSI_THRESHOLD}")
        # Prometheus: DRIFT_ALERTS.inc()
        # In production: trigger retraining job here
```

**Baseline stats:** Computed once from the HarvestML training data and saved as a JSON file in `drift/baseline_stats.json`.

---

### 5.8 Docker Compose (`docker-compose.yml`)

**What it does:** Spins up the entire stack with one command: `docker-compose up`

**Services:**

```yaml
version: '3.8'
services:
  gateway:
    build: .
    ports:
      - "8000:8000"
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
  
  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
```

**Single command to run everything:**
```bash
docker-compose up --build
```

---

### 5.9 Grafana Dashboard

**What it shows (panels to build):**

| Panel | Type | Metric |
|---|---|---|
| Requests/sec | Time series | `rate(gateway_requests_total[1m])` |
| p50/p95/p99 Latency | Time series | `histogram_quantile(0.95, gateway_request_duration_seconds)` |
| Cache Hit Rate | Gauge | `cache_hits / (cache_hits + cache_misses)` |
| Error Rate | Time series | `rate(gateway_requests_total{status="error"}[1m])` |
| PSI Score by Feature | Bar chart | `gateway_psi_score` |
| Prediction Distribution | Histogram | `gateway_prediction_values` |
| ONNX vs Groq split | Pie chart | `gateway_requests_total` by backend |

---

## 6. Implementation Order

Build in this exact sequence — each step is testable before moving to the next:

```
Phase 1 — Core Inference (Days 1-3)
  ✅ ONNX conversion (DONE)
  [ ] Project folder setup
  [ ] requirements.txt
  [ ] ONNXRunner class + unit test
  [ ] FastAPI /predict endpoint (no cache, no monitoring yet)
  [ ] Test with curl/Postman

Phase 2 — Caching (Days 4-5)
  [ ] Redis setup in Docker Compose
  [ ] Cache class implementation
  [ ] Integrate cache into /predict endpoint
  [ ] Verify cache hits returning correctly

Phase 3 — Monitoring (Days 6-8)
  [ ] Prometheus metrics definitions
  [ ] Instrument gateway with metrics
  [ ] prometheus.yml scrape config
  [ ] Grafana dashboard setup
  [ ] Verify metrics appear in Grafana

Phase 4 — Groq Integration (Days 9-10)
  [ ] Groq API key setup
  [ ] GroqRunner class
  [ ] /predict?explain=true endpoint
  [ ] Router logic

Phase 5 — Drift Detection (Days 11-13)
  [ ] Compute baseline stats from HarvestML training data
  [ ] PSI implementation
  [ ] Integrate drift check into prediction pipeline
  [ ] PSI alerts appearing in Grafana

Phase 6 — Interactive Frontend & Security Hardening (Days 14-15)
  [ ] Swagger & ReDoc production auto-hiding
  [ ] Single-page web UI layout (Frosted Glassmorphism UI)
  [ ] Client-side validation, XSS prevention, and 429 rate limit overlays
  [ ] Serve frontend from gateway root `/`

Phase 7 — Dockerization & Deployment (Days 16-17)
  [ ] Gateway Dockerfile & .dockerignore
  [ ] Local Self-Contained Compose (docker-compose.yml)
  [ ] Cloud Forwarding Compose (docker-compose.cloud.yml + Grafana Alloy)
  [ ] README & documentation
  [ ] Deploy to Railway/Render
```

---

## 7. Tech Stack Summary

| Layer | Tool | Version | Purpose |
|---|---|---|---|
| Gateway | FastAPI | latest | REST API, async request handling |
| Validation | Pydantic | v2 | Request/response schemas |
| ML Inference | ONNX Runtime | latest | Run ONNX model |
| Preprocessing | scikit-learn | match HarvestML venv | Run ColumnTransformer |
| LLM | Groq API (Llama 3) | llama3-8b-8192 | Natural language explanations |
| Cache | Redis | 7-alpine | Key-value prediction cache |
| Metrics | Prometheus client (Python) | latest | Metrics collection |
| Visualization | Grafana | latest | Live monitoring dashboard |
| Drift Detection | Custom Python + PSI | — | Feature distribution monitoring |
| Containerization | Docker + Docker Compose | latest | Reproducible deployment |
| Deployment | Railway or Render | — | Free tier, live URL |

---

## 8. Environment Variables

```env
GROQ_API_KEY=your_groq_api_key_here
REDIS_URL=redis://redis:6379
ONNX_MODEL_PATH=models/harvestml_simulator.onnx
PREPROCESSOR_PATH=models/simulator_preprocessor.joblib
BASELINE_STATS_PATH=drift/baseline_stats.json
PSI_THRESHOLD=0.25
CACHE_TTL_SECONDS=3600
```

---

## 9. Requirements.txt (Starting Point)

```
fastapi
uvicorn[standard]
pydantic
onnxruntime
scikit-learn
joblib
numpy
pandas
redis
prometheus-client
groq
python-dotenv
xgboost
```

---

## 10. Key Interview Talking Points This Project Enables

| Question | Your Answer |
|---|---|
| Why ONNX over pickle? | Portability, no framework dependency in production, faster inference via runtime optimization |
| Why PSI over KL divergence for drift? | PSI handles zero-frequency bins via epsilon, threshold-interpretable (0.1/0.25 boundaries), industry standard for model monitoring |
| How does your cache handle model updates? | FLUSHDB on deployment, TTL-based expiry, cache key includes model version hash |
| Why p95 latency not average? | Average hides tail latency — a p95 of 2s means 5% of users wait 2+ seconds, which is invisible in averages |
| How does your router decide which backend? | Cost-aware routing: ONNX for structured prediction (free, <10ms), Groq only when explanation explicitly requested (API cost, ~1s) |
| What's the difference between data drift and concept drift? | Data drift = input distribution shifts. Concept drift = relationship between input and output changes. PSI detects data drift. Concept drift needs ground truth labels to detect. |

---

## 11. What This Project Proves

This project is explicitly missing from most fresher portfolios. It demonstrates:

1. You understand that ML engineering is not just model training
2. You can build the infrastructure layer that sits between a model and users
3. You know why latency, caching, and drift matter in production
4. You can operate a monitoring stack, not just set one up
5. You've thought about cost (Groq routing), reliability (health checks), and observability (Prometheus/Grafana)

The live Grafana dashboard URL in the README is non-negotiable. It makes the project real.

---

*Document generated for project handoff. Last updated during ONNX conversion phase.*
