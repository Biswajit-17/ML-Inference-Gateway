"""
verify_drift_detection.py - Automated verification suite for Phase 5 Data Drift Detection.

Optimized to use a single persistent HTTP connection (Keep-Alive) to prevent
Windows socket exhaustion (WinError 10055) during rapid-fire test requests.
"""

import sys
import http.client
import json
import time
import subprocess
import os
import redis
import random

# We run on port 8080 to avoid conflicts with port 8000
TEST_PORT = 8080

def sample_feature_value(baseline_data, feature_name):
    """Samples a value matching the distribution defined in baseline_stats.json."""
    feature = baseline_data[feature_name]
    edges = feature["bin_edges"]
    expected = feature["expected"]
    
    # Choose a bin according to expected probabilities
    bin_idx = random.choices(range(len(expected)), weights=expected, k=1)[0]
    
    # Generate a uniform value within that bin
    low = edges[bin_idx]
    high = edges[bin_idx + 1]
    return random.uniform(low, high)

def clear_redis_drift_keys(redis_url: str):
    """Resets Redis drift tracking and cache keys to ensure fresh execution."""
    print(f"Connecting to Redis at {redis_url} to clear drift and cache data...")
    try:
        r = redis.Redis.from_url(redis_url)
        # Clear drift data, prediction cache, and recommendations cache
        keys = []
        for pattern in ["drift:*", "predict:*", "recommend:*"]:
            keys.extend(r.keys(pattern))
        if keys:
            print(f"   Deleting {len(keys)} entries matching drift/predict/recommend patterns...")
            r.delete(*keys)
            print("   Redis keys cleared.")
        else:
            print("   No matching Redis keys found.")
    except Exception as e:
        print(f"   ⚠️ WARNING: Failed to clear Redis keys: {e}")

def query_gateway_metrics() -> dict:
    """Queries /metrics endpoint using a dedicated transient connection."""
    try:
        conn = http.client.HTTPConnection("localhost", TEST_PORT, timeout=5)
        conn.request("GET", "/metrics/")
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        
        lines = data.splitlines()
        metrics = {}
        for line in lines:
            if line.startswith("gateway_psi_score"):
                parts = line.split(" ")
                val = float(parts[-1])
                label_part = parts[0].split("{")[1].split("}")[0]
                feature = label_part.split("=")[1].replace('"', '')
                metrics[f"psi_{feature}"] = val
            elif line.startswith("gateway_drift_alerts_total"):
                parts = line.split(" ")
                val = int(float(parts[-1]))
                label_part = parts[0].split("{")[1].split("}")[0]
                labels = {}
                for p in label_part.split(","):
                    k, v = p.split("=")
                    labels[k.strip()] = v.strip().replace('"', '')
                metrics[f"alert_{labels['feature']}_{labels['severity']}"] = val
        return metrics
    except Exception as e:
        print(f"   Failed to query metrics: {e}")
        return {}

def send_with_keepalive(conn, method, path, body, headers):
    """Sends a request via HTTP Keep-Alive connection, with retries on connection reset."""
    for attempt in range(3):
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return conn, resp
        except (http.client.HTTPException, ConnectionError) as e:
            print(f"   [Keep-Alive Retry] Connection error ({e}), re-establishing connection...")
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPConnection("localhost", TEST_PORT)
    # One final try outside the loop to let any exception propagate if it keeps failing
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    return conn, resp

def test_drift_detection():
    print("=" * 60)
    print("RUNNING DATA DRIFT DETECTION VALIDATION SUITE")
    print("=" * 60)

    # 1. Read Redis URL from .env
    redis_url = "redis://127.0.0.1:6379"
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f.read().splitlines():
                if line.startswith("REDIS_URL="):
                    redis_url = line.split("=", 1)[1].strip()

    clear_redis_drift_keys(redis_url)

    # Load baseline statistics for sampling
    try:
        with open("drift/baseline_stats.json", "r") as f:
            baseline_data = json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load baseline stats for test sampling: {e}")
        sys.exit(1)

    baseline_to_payload = {
        "N (Kg/ha)": "N",
        "P (Kg/ha)": "P",
        "K (Kg/ha)": "K",
        "Annual Rainfall (mm)": "annual_rainfall",
        "Kharif Rainfall (mm)": "kharif_rainfall",
        "Rabi Rainfall (mm)": "rabi_rainfall",
        "Irrigation Ratio": "irrigation_ratio",
    }

    # 2. Start gateway server on TEST_PORT
    print(f"Starting gateway server on port {TEST_PORT}...")
    proc = subprocess.Popen(
        [r".\venv\Scripts\python.exe", "-m", "uvicorn", "gateway.main:app", "--port", str(TEST_PORT)]
    )
    time.sleep(8)  # Wait for startup and check

    all_passed = True
    unique_ips = [f"192.168.1.{i}" for i in range(1, 13)]

    try:
        # Establish a single persistent HTTP connection (Keep-Alive)
        print("Establishing persistent TCP connection to gateway...")
        conn = http.client.HTTPConnection("localhost", TEST_PORT)

        # Step 1: Send baseline (normal) requests
        print("Step 1: Sending 150 normal/baseline requests to establish stability...")
        for i in range(150):
            ip = unique_ips[i % len(unique_ips)]
            req_payload = {
                "soil_type": "VERTISOLS",
                "state": "Uttar Pradesh",
                "crop": "RICE",
                "explain": False
            }
            for baseline_name, payload_name in baseline_to_payload.items():
                req_payload[payload_name] = sample_feature_value(baseline_data, baseline_name)

            headers = {
                "Content-Type": "application/json",
                "X-Forwarded-For": ip,
                "Connection": "keep-alive"
            }
            
            try:
                conn, resp = send_with_keepalive(conn, "POST", "/predict", body=json.dumps(req_payload), headers=headers)
                resp.read()  # Must read to clear buffer for next Keep-Alive request
            except Exception as e:
                print(f"   Request {i+1} failed: {e}")
                all_passed = False
                break

        time.sleep(2)  # Wait for background evaluations
        
        # Query metrics and check baseline PSI
        metrics = query_gateway_metrics()
        print("Current metrics after baseline requests:")
        print(json.dumps(metrics, indent=2))

        for feature in ["N", "P", "K", "annual_rainfall"]:
            psi_key = f"psi_{feature}"
            if psi_key in metrics:
                psi_val = metrics[psi_key]
                print(f"   PSI of {feature}: {psi_val:.4f}")
                if psi_val < 0.15:
                    print(f"   [PASS] {feature} PSI is within normal range.")
                else:
                    print(f"   [FAIL] {feature} PSI is unexpectedly high: {psi_val}")
                    all_passed = False
            else:
                print(f"   [FAIL] Metric {psi_key} not exposed in Prometheus!")
                all_passed = False

        # Step 2: Send anomalous/drifted requests
        print("\nStep 2: Sending 150 anomalous requests to trigger drift...")
        for i in range(150):
            ip = unique_ips[i % len(unique_ips)]
            req_payload = {
                "N": 450.0 + (i % 10),
                "P": 230.0 + (i % 5),
                "K": 190.0 + (i % 5),
                "annual_rainfall": 4800.0 + (i % 100),
                "kharif_rainfall": 3900.0 + (i % 50),
                "rabi_rainfall": 800.0 + (i % 30),
                "irrigation_ratio": 0.95 + (i % 10) * 0.005,
                "soil_type": "VERTISOLS",
                "state": "Uttar Pradesh",
                "crop": "RICE",
                "explain": False
            }

            headers = {
                "Content-Type": "application/json",
                "X-Forwarded-For": ip,
                "Connection": "keep-alive"
            }
            
            try:
                conn, resp = send_with_keepalive(conn, "POST", "/predict", body=json.dumps(req_payload), headers=headers)
                resp.read()
            except Exception as e:
                print(f"   Request {i+1} failed: {e}")
                all_passed = False
                break

        time.sleep(2)  # Wait for background evaluations
        conn.close()

        # Query metrics and check drifted PSI
        metrics_drifted = query_gateway_metrics()
        print("Current metrics after anomalous requests:")
        print(json.dumps(metrics_drifted, indent=2))

        for feature in ["N", "annual_rainfall"]:
            psi_key = f"psi_{feature}"
            if psi_key in metrics_drifted:
                psi_val = metrics_drifted[psi_key]
                print(f"   Drifted PSI of {feature}: {psi_val:.4f}")
                if psi_val >= 0.25:
                    print(f"   [PASS] {feature} PSI successfully flagged significant drift.")
                else:
                    print(f"   [FAIL] {feature} PSI failed to detect drift: {psi_val}")
                    all_passed = False
            else:
                print(f"   [FAIL] Metric {psi_key} missing after drift!")
                all_passed = False

            alert_key = f"alert_{feature}_significant"
            if alert_key in metrics_drifted:
                alert_count = metrics_drifted[alert_key]
                print(f"   Drift Alert Count for {feature}: {alert_count}")
                if alert_count >= 1:
                    print(f"   [PASS] Drift alert fired for {feature}.")
                else:
                    print(f"   [FAIL] Drift alert count is 0 for {feature}!")
                    all_passed = False
            else:
                print(f"   [FAIL] Drift alert counter '{alert_key}' not incremented!")
                all_passed = False

    finally:
        print("\nStopping gateway server...")
        proc.terminate()
        proc.wait()

    print("=" * 60)
    if all_passed:
        print("SUCCESS: ALL DRIFT DETECTION TESTS PASSED!")
        sys.exit(0)
    else:
        print("FAIL: DRIFT DETECTION TESTS FAILED!")
        sys.exit(1)

if __name__ == "__main__":
    test_drift_detection()
