# Phase 5 — Population Stability Index (PSI) Drift Detection Design & Roadmap

This document outlines the step-by-step implementation plan for monitoring incoming prediction feature distributions against the training data baseline using Population Stability Index (PSI) to detect data drift, alerting in real-time, and visualizing via Prometheus and Grafana.

---

## 📅 Implementation Steps

### Step 1: Export Baseline Statistics (Phase 5.1)

Save the training dataset distributions to serve as the expected baseline.

*   **Location:** Already executed and saved in [drift/baseline_stats.json](file:///c:/Users/Biswajitrk/Documents/COdezzz/HarvestGate%20-%20ML%20Inference%20Gateway/drift/baseline_stats.json).
*   **Structure:** Stores the `min`, `max`, `bin_edges` (10 equal-width bins), and the `expected` proportions for all 7 numerical features:
    *   `N (Kg/ha)`
    *   `P (Kg/ha)`
    *   `K (Kg/ha)`
    *   `Annual Rainfall (mm)`
    *   `Kharif Rainfall (mm)`
    *   `Rabi Rainfall (mm)`
    *   `Irrigation Ratio`

---

### Step 2: Implement Custom Drift Detector (Phase 5.2)

Create the mathematical and queuing engine to process historical requests.

*   **Location:** Create `drift/detector.py` containing the `DriftDetector` class.
*   **State Management (Redis):** 
    *   Store serialized incoming prediction requests as a rolling history list in Redis (`drift:history`).
    *   Keep the window size capped at **1,000 items** using Redis `LTRIM`.
*   **Per-IP Contribution Cap:** 
    *   To prevent a single user or bot from spamming request payloads and artificially triggering a drift alert, group samples in the window by client IP.
    *   Enforce a **10% cap** where only the 10 most recent requests from any single IP are counted.
*   **Minimum Sample Size:** Ensure the capped list has **at least 100 samples** before calculating PSI to prevent statistical noise.
*   **Epsilon Handling:** Add $\epsilon = 10^{-6}$ to all bin calculations to prevent divisions by zero or taking the logarithm of zero.

---

### Step 3: Integrate into Gateway & Telemetry (Phase 5.3)

Wire the collector and evaluator into the main prediction pipeline.

*   **Location:** Update endpoints in [gateway/main.py](file:///c:/Users/Biswajitrk/Documents/COdezzz/HarvestGate%20-%20ML%20Inference%20Gateway/gateway/main.py).
*   **Non-Blocking Background Tasks:** 
    *   On every prediction/recommendation request (only on cache misses), push the numerical feature values and the client's IP to Redis.
    *   To ensure zero latency impact on client API responses, run the list updates and PSI checks inside FastAPI's `BackgroundTasks`.
*   **Throttled Evaluation:** Increment a Redis counter (`drift:counter`). Execute the actual PSI calculations only on **every 50th request**.
*   **Telemetry Tracking:**
    *   Expose the calculated PSI values via the `gateway_psi_score{feature="<name>"}` Prometheus Gauge.
    *   Increment the `gateway_drift_alerts_total{feature="<name>", severity="moderate|significant"}` Counter when PSI crosses the `0.1` or `0.25` thresholds.
    *   Apply a **5-minute cooldown** per feature to prevent duplicate alerts flooding logs.

---

### Step 4: Verification Testing

Create a script `verify_drift_detection.py` to validate the setup:

1.  **Cache Cleaning:** Reset any existing drift keys in Redis.
2.  **Base Case:** Inject 100 requests with typical values from multiple IPs and verify `gateway_psi_score` is low (< 0.1) and no alerts are fired.
3.  **Drift Case:** Inject 100 requests with anomalous values (e.g. extremely high Nitrogen or low rainfall) and assert that `gateway_psi_score` rises and a drift alert is logged.
4.  **IP Cap Case:** Send 50 anomalous requests from a single IP address and confirm that the drift remains untriggered because excess requests from that single IP are capped at 10.

---

## 🔒 Phase 5 Security Hardening Matrix

| Security Rule | Risk Addressed | Implementation Detail |
|---|---|---|
| **Per-IP Cap (10%)** | Denial of Service / Alert Spoofing | Caps contributions at 10 requests per IP in the rolling evaluation window. |
| **Non-Blocking Execution** | Thread starvation / Request latency inflation | Updates and PSI math are handled asynchronously in FastAPI background threads. |
| **Alert Cooldown Throttling** | Log flooding / Resource exhaustion | Enforces a strict 5-minute cooldown before firing duplicate warnings/errors for a feature. |
| **Sanitized Epsilon Bins** | Division by Zero / Internal Server Crashes | Adds $10^{-6}$ epsilon to proportions, protecting mathematical functions from infinity/errors. |
