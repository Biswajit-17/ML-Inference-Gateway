# Phase 7 — Dockerization & Deployment Design & Roadmap

This document outlines the step-by-step implementation plan for containerizing the HarvestGate ML Inference Gateway, establishing a dual-orchestration strategy for both self-contained local execution and cloud-forwarded metrics monitoring, and deploying to production.

---

## 📅 Implementation Steps

### Step 1: Create Gateway Dockerfile & .dockerignore (Phase 7.1)

Create a production-ready, minimal Docker container to run the FastAPI gateway application.

*   **Location:** Create `Dockerfile` and `.dockerignore` in the repository root.
*   **Base Image:** Use `python:3.11-slim` to minimize the image footprint (~120MB raw size) and reduce vulnerability surface area.
*   **Build Optimization:** Install Python dependencies using `--no-cache-dir` to prevent pip cache inflation.
*   **Healthcheck:** Implement a native `HEALTHCHECK` using `curl` to query the `/health` endpoint so container orchestrators can monitor gateway health.
*   **Context Optimization:** Exclude local virtual environments (`venv/`), git history (`.git/`), cache files (`__pycache__/`, `*.pyc`), local secrets (`.env`), and testing scripts from being baked into the image.

---

### Step 2: Configure Local Self-Contained Orchestration (Phase 7.2a)

Update the default compose configuration in [docker-compose.yml](file:///c:/Users/Biswajitrk/Documents/COdezzz/HarvestGate%20-%20ML%20Inference%20Gateway/docker-compose.yml) to allow any developer to run the entire stack locally with zero configuration.

*   **Default Stack:** Contains the `gateway`, `redis`, `prometheus`, and `grafana` containers.
*   **Offline Capability:** Requires zero cloud accounts or internet connection. Users run:
    ```bash
    docker compose up --build
    ```
    and access the local performance dashboards instantly on `http://localhost:3000`.
*   **Scraping target:** Update [prometheus.yml](file:///c:/Users/Biswajitrk/Documents/COdezzz/HarvestGate%20-%20ML%20Inference%20Gateway/monitoring/prometheus/prometheus.yml) to target `"gateway:8000"` within the bridge network.

---

### Step 3: Configure Cloud Forwarding Orchestration (Phase 7.2b)

Create a dedicated cloud-forwarding compose configuration to push telemetry metrics to Grafana Cloud, bypassing local database storage requirements.

*   **Cloud Stack:** Create `docker-compose.cloud.yml` containing only `gateway`, `redis`, and `alloy` (Grafana Alloy).
*   **Metrics Forwarder:** Grafana Alloy scrapes the gateway locally and remote-writes metrics over secure TLS to your centralized Grafana Cloud account.
*   **Access Control:** Only the owner (you) can execute this mode since it requires your private Grafana Cloud credentials (`GRAFANA_CLOUD_PROM_URL`, `USER`, `TOKEN`) loaded from your uncommitted `.env` file. You run:
    ```bash
    docker compose -f docker-compose.cloud.yml up --build
    ```

---

### Step 4: Write Comprehensive README & Documentation (Phase 7.3)

Create `README.md` at the project root to serve as a complete portfolio handbook.

*   **System Architecture:** Explain the data flow between the FastAPI Gateway, Redis Cache, local ONNX Runtime, OpenRouter LLM, and the dual-mode telemetry stack.
*   **Quickstart Guide:** Detail instructions on running the self-contained local stack vs. deploying/running the cloud-forwarding setup.
*   **API Reference:** Provide clear curl examples for `/predict`, `/predict/explain`, and `/health` endpoints.

---

### Step 5: Deploy to Render / Railway (Phase 7.4)

Configure cloud deployment settings for a live online demonstration.

*   **Option A: Railway Deployment**
    *   Railway detects compose files automatically. Connect your GitHub repository to Railway to build and deploy each container.
    *   Expose a public domain on the `gateway` service port `8000` and inject the shared variables (such as `REDIS_PASSWORD` and `GROQ_API_KEY`) via Railway's shared environment variables panel.
*   **Option B: Render Deployment**
    *   Create a new **Web Service** on Render pointing to your repository, choosing standard Docker build runtime options.
    *   Provision a managed **Redis Cache** instance directly via Render's dashboard.
    *   Link the services by binding the Redis connection URL string as the `REDIS_URL` environment variable inside the gateway web service settings.

---

## 🔒 Phase 7 Security Hardening Matrix

| Security Rule | Risk Addressed | Implementation Detail |
|---|---|---|
| **Minimal Base Image** | Host OS vulnerability exploitation | Using `python:3.11-slim` strips out compilers, build tooling, and GUI packages. |
| **Non-Root Execution** | Container breakout to host root access | Create a dedicated system user/group inside the Dockerfile to run the Uvicorn worker. |
| **Secure .dockerignore** | Credentials leakage and cache pollution | Excludes `.env`, private SSH keys, and `venv` directories from being baked into the Docker image. |
| **Private Docker Network** | Inter-service traffic interception | Redis, Prometheus, and Grafana communicate on a closed bridge network, exposing only ports 8000, 9090, and 3000 to the host. |
| **Docker Healthcheck** | Silent service failures | `HEALTHCHECK` queries `/health` every 30s to automatically restart unhealthy container instances. |
