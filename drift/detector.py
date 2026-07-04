"""
drift/detector.py — Real-time Population Stability Index (PSI) Data Drift Detector.

Tracks incoming prediction features in a rolling window saved in Redis.
Enforces a 10% per-IP sample contribution cap to prevent denial-of-service/spoofing,
adds epsilon protection against division-by-zero, and updates Prometheus gauges.
"""

import json
import logging
import math
import os
import time
from typing import Dict, List, Optional
import numpy as np
import redis

from monitoring.metrics import DRIFT_ALERTS, PSI_SCORE

logger = logging.getLogger("harvestgate.drift")

class DriftDetector:
    """
    Computes PSI on a rolling window of request feature values stored in Redis
    against baseline statistics loaded from drift/baseline_stats.json.
    """

    def __init__(
        self,
        baseline_path: str = "drift/baseline_stats.json",
        redis_client: Optional[redis.Redis] = None,
        window_size: int = 1000,
        min_samples: int = 100,
        eval_interval: int = 50,
        psi_threshold: float = 0.25,
        cooldown_seconds: float = 300.0,  # 5 minutes alert cooldown
    ):
        self.baseline_path = baseline_path
        self.redis = redis_client
        self.window_size = window_size
        self.min_samples = min_samples
        self.eval_interval = eval_interval
        self.psi_threshold = psi_threshold
        self.cooldown_seconds = cooldown_seconds
        
        # Load baseline statistics
        self.baseline = {}
        if os.path.exists(baseline_path):
            try:
                with open(baseline_path, "r") as f:
                    self.baseline = json.load(f)
                logger.info(f"Successfully loaded baseline statistics from {baseline_path}")
            except Exception as e:
                logger.error(f"Failed to parse baseline statistics: {e}")
        else:
            logger.error(f"Baseline stats file not found at: {baseline_path}")

        # Map internal feature names to Pydantic/Resolvers payload names
        self.feature_mapping = {
            "N (Kg/ha)": "N",
            "P (Kg/ha)": "P",
            "K (Kg/ha)": "K",
            "Annual Rainfall (mm)": "annual_rainfall",
            "Kharif Rainfall (mm)": "kharif_rainfall",
            "Rabi Rainfall (mm)": "rabi_rainfall",
            "Irrigation Ratio": "irrigation_ratio",
        }

    async def record_request(self, client_ip: str, features: Dict[str, float]):
        """
        Pushes a new prediction request's features to the Redis history list.
        Runs asynchronously in a background thread to prevent latency hits.
        """
        if not self.redis:
            return

        payload = {
            "ip": client_ip,
            "timestamp": time.time(),
            "features": features
        }

        try:
            # PUSH to Redis List
            await self.redis.lpush("drift:history", json.dumps(payload))
            # TRIM list to cap at max window size
            await self.redis.ltrim("drift:history", 0, self.window_size - 1)
            
            # Increment request counter to decide if we run eval
            count = await self.redis.incr("drift:counter")
            if count % self.eval_interval == 0:
                logger.info(f"Evaluation interval reached ({count} requests). Running data drift checks.")
                await self.evaluate_drift()
        except Exception as e:
            logger.error(f"Failed to record request for drift tracking: {e}")

    async def evaluate_drift(self) -> Dict[str, float]:
        """Loads samples from Redis, applies per-IP capping, and calculates PSI scores."""
        if not self.redis or not self.baseline:
            return {}

        try:
            # 1. Fetch all rolling samples
            raw_samples = await self.redis.lrange("drift:history", 0, -1)
            if not raw_samples:
                return {}

            samples = []
            for r in raw_samples:
                try:
                    samples.append(json.loads(r))
                except Exception:
                    continue

            # 2. Apply per-IP contribution cap (10% of window size or max 10 requests per IP)
            ip_groups = {}
            for s in samples:
                ip = s.get("ip", "unknown")
                if ip not in ip_groups:
                    ip_groups[ip] = []
                ip_groups[ip].append(s)

            capped_samples = []
            for ip, ip_samples in ip_groups.items():
                # Sort by timestamp descending and take the 10 most recent requests
                ip_samples.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                capped_samples.extend(ip_samples[:10])

            total_samples = len(capped_samples)
            if total_samples < self.min_samples:
                logger.info(
                    f"Capped samples count ({total_samples}) is below minimum threshold ({self.min_samples}). Skipping PSI calculations."
                )
                return {}

            logger.info(f"Computing PSI over {total_samples} capped samples (originally {len(samples)} total).")
            psi_results = {}

            # 3. Compute PSI for each feature
            for baseline_name, payload_name in self.feature_mapping.items():
                if baseline_name not in self.baseline:
                    continue

                feature_baseline = self.baseline[baseline_name]
                bin_edges = feature_baseline["bin_edges"]
                expected_proportions = feature_baseline["expected"]

                # Extract feature values from capped samples
                values = [
                    s["features"].get(payload_name)
                    for s in capped_samples
                    if payload_name in s.get("features", {})
                ]

                # If missing too many data points, skip
                if len(values) < self.min_samples:
                    continue

                # Compute actual proportions based on baseline bin edges
                # First and last bins span -inf to inf respectively to handle values outside baseline range
                edges = list(bin_edges)
                edges[0] = -float("inf")
                edges[-1] = float("inf")

                counts, _ = np.histogram(values, bins=edges)
                actual_proportions = (counts / len(values)).tolist()

                # Calculate Population Stability Index (PSI)
                psi = 0.0
                epsilon = 1e-6
                for act, exp in zip(actual_proportions, expected_proportions):
                    a_prob = act + epsilon
                    e_prob = exp + epsilon
                    psi += (a_prob - e_prob) * math.log(a_prob / e_prob)

                psi_results[payload_name] = psi

                # 4. Update Prometheus Gauge
                PSI_SCORE.labels(feature=payload_name).set(psi)

                # 5. Alert & Cooldown Check
                if psi >= self.psi_threshold:
                    await self._trigger_alert(payload_name, psi, severity="significant")
                elif psi >= 0.1:
                    await self._trigger_alert(payload_name, psi, severity="moderate")

            return psi_results

        except Exception as e:
            logger.error(f"Error evaluating feature drift: {e}", exc_info=True)
            return {}

    async def _trigger_alert(self, feature: str, psi: float, severity: str):
        """Logs drift alert and increments Prometheus alert counter with a cooldown period."""
        cooldown_key = f"drift:cooldown:{feature}:{severity}"
        try:
            # Check if alert cooldown is active in Redis
            is_cooldown = await self.redis.get(cooldown_key)
            if is_cooldown:
                return

            # Set cooldown token with TTL
            await self.redis.setex(cooldown_key, int(self.cooldown_seconds), "active")

            # Fire the alert
            log_msg = f"⚠️ DATA DRIFT DETECTED: Feature '{feature}' has {severity} drift (PSI = {psi:.4f})."
            if severity == "significant":
                logger.error(log_msg)
            else:
                logger.warning(log_msg)

            # Increment Prometheus counter
            DRIFT_ALERTS.labels(feature=feature, severity=severity).inc()

        except Exception as e:
            logger.error(f"Failed to process drift alert for {feature}: {e}")
