"""
main.py — FastAPI Gateway Application.

Orchestrates consumer-facing recommendation endpoints and low-level prediction endpoints.
Implements rate limiting, request size limits, CORS policies, security headers,
integrity checks at startup, and global error masking.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv

# Load environment variables from .env before initializing other configurations
# Reloaded model configurations from .env
load_dotenv(override=True)

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from drift.detector import DriftDetector
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from gateway.schemas import (
    CropRecommendation,
    HealthResponse,
    PredictRequest,
    PredictResponse,
    RecommendRequest,
    RecommendResponse,
)
from gateway.security import verify_all_artifacts
from gateway.cache import CacheManager
from inference.climate import ClimateResolver
from inference.onnx_runner import ONNXRunner
from inference.openrouter_runner import OpenRouterRunner
from inference.scorer import SuitabilityScorer
from gateway.router import circuit_breaker
from monitoring.metrics import (
    ACTIVE_REQUESTS,
    CACHE_LOOKUPS,
    GATEWAY_INFO,
    INFERENCE_LATENCY,
    PAYLOAD_REJECTIONS,
    PREDICTION_VALUES,
    RATE_LIMIT_REJECTIONS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)
from prometheus_client import make_asgi_app

# ── Logging Configuration ──
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("harvestgate")

# ── Paths ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

# ── Dependency Containers ──
onnx_runner: Optional[ONNXRunner] = None
suitability_scorer: Optional[SuitabilityScorer] = None
climate_resolver: Optional[ClimateResolver] = None
cache_manager: Optional[CacheManager] = None
openrouter_runner: Optional[OpenRouterRunner] = None
drift_detector: Optional[DriftDetector] = None
startup_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager to handle startup integrity checks and component loads."""
    global onnx_runner, suitability_scorer, climate_resolver, cache_manager, openrouter_runner, drift_detector

    logger.info("Starting up HarvestGate Gateway...")

    # 1. Run security integrity check on all files before loading anything
    logger.info("Executing SHA-256 integrity check on artifacts...")
    if not verify_all_artifacts(MODEL_DIR, DATA_DIR):
        logger.critical(
            "CRITICAL: Integrity verification failed! "
            "Refusing to start application due to potential file tampering."
        )
        # Force terminate process to prevent server start
        os._exit(1)

    # 2. Load model runner, scorer, and weather resolver
    try:
        onnx_runner = ONNXRunner(
            model_path=os.path.join(MODEL_DIR, "harvestml_simulator.onnx"),
            preprocessor_path=os.path.join(MODEL_DIR, "simulator_preprocessor.joblib"),
        )
        suitability_scorer = SuitabilityScorer(
            baselines_path=os.path.join(DATA_DIR, "crop_baselines.json"),
            priors_path=os.path.join(DATA_DIR, "acreage_priors.json"),
        )
        climate_resolver = ClimateResolver(
            defaults_path=os.path.join(DATA_DIR, "state_defaults.json")
        )
        
        # 3. Connect to Redis Cache (Graceful fall-back if offline)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
        cache_manager = CacheManager(redis_url=redis_url, default_ttl=ttl_seconds)
        await cache_manager.connect()

        # 4. Initialize OpenRouter LLM Advisor
        openrouter_runner = OpenRouterRunner()

        # 5. Initialize Data Drift Detector
        drift_detector = DriftDetector(
            baseline_path=os.path.join(BASE_DIR, "drift", "baseline_stats.json"),
            redis_client=cache_manager.client if (cache_manager and cache_manager.client) else None,
            min_samples=100,
            eval_interval=50
        )

        GATEWAY_INFO.info({
            "version": "1.0.0",
            "model_format": "onnx",
            "model_name": "harvestml_simulator",
            "project": "HarvestGate",
        })
        logger.info("All pipeline components loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to load pipeline components: {e}")
        os._exit(1)

    yield

    logger.info("Shutting down HarvestGate Gateway...")
    if cache_manager:
        await cache_manager.disconnect()


# ── Limiter setup ──
limiter = Limiter(key_func=get_remote_address)
ENV = os.getenv("ENV", "production").lower()
app = FastAPI(
    title="HarvestGate — ML Inference Gateway",
    description="High-performance, secure crop recommendation and yield prediction API.",
    lifespan=lifespan,
    docs_url=None if ENV == "production" else "/docs",
    redoc_url=None if ENV == "production" else "/redoc",
    openapi_url=None if ENV == "production" else "/openapi.json",
)

# SlowAPI Rate limit handler registration
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Prometheus /metrics Scrape Endpoint ──
metrics_asgi = make_asgi_app()
app.mount("/metrics", metrics_asgi)

# ── Static Files Directory Mounting ──
app.mount("/static", StaticFiles(directory="gateway/static"), name="static")


def get_client_ip(request: Request) -> str:
    """Extracts client IP, respecting X-Forwarded-For if behind reverse proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

# ── CORS Configuration ──
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True if ALLOWED_ORIGINS != ["*"] else False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


# ── Security & Payload Size Middlewares ──


@app.middleware("http")
async def limit_body_size_middleware(request: Request, call_next):
    """Enforces request payload cap of 2KB to prevent memory exhaustion attacks."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 2048:
        logger.warning(
            f"Oversized request rejected from IP {request.client.host}: {content_length} bytes"
        )
        PAYLOAD_REJECTIONS.inc()
        return JSONResponse(status_code=413, content={"detail": "Payload Too Large"})
    return await call_next(request)


@app.middleware("http")
async def add_security_headers_middleware(request: Request, call_next):
    """Sets standard HTTP security headers for security compliance."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


@app.middleware("http")
async def global_exception_masking_middleware(request: Request, call_next):
    """Intercepts unexpected server errors to prevent leaking internal stack traces."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(f"Unhandled exception in request path {request.url.path}: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    """Outermost middleware — records request count, latency, and active gauge for Prometheus."""
    # Skip the /metrics scrape endpoint to avoid recursive self-instrumentation
    if request.url.path.startswith("/metrics"):
        return await call_next(request)

    endpoint = request.url.path
    method = request.method
    status = "500"  # default if something unexpected happens

    ACTIVE_REQUESTS.labels(endpoint=endpoint).inc()
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = str(response.status_code)
        # Track rate-limit rejections separately for security dashboard
        if response.status_code == 429:
            RATE_LIMIT_REJECTIONS.labels(endpoint=endpoint).inc()
        return response
    except Exception:
        raise
    finally:
        duration = time.perf_counter() - start
        REQUEST_COUNT.labels(endpoint=endpoint, method=method, status=status).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint, method=method).observe(duration)
        ACTIVE_REQUESTS.labels(endpoint=endpoint).dec()


@app.get("/")
async def root():
    """Serves the interactive, premium web client dashboard."""
    return FileResponse("gateway/static/index.html")


# ── Consumer API Endpoints ──


@app.post("/recommend", response_model=RecommendResponse)
@limiter.limit("30/minute")
async def recommend(request: Request, payload: RecommendRequest, background_tasks: BackgroundTasks):
    """
    Consumer-Facing Recommendation Endpoint.

    Auto-resolves soil parameters and fetches live rainfall averages
    before running the 19-crop simulator to return Top 5 suggestions.
    """
    start_time = time.perf_counter()

    if not climate_resolver or not onnx_runner or not suitability_scorer:
        raise HTTPException(status_code=503, detail="Service currently unavailable")

    # 1. Attempt Cache Lookup
    cache_key = None
    if cache_manager and cache_manager.is_connected:
        cache_key = cache_manager.make_cache_key(
            "recommend",
            {
                "state": payload.state,
                "district": payload.district,
                "soil_type": payload.soil_type,
                "explain": payload.explain,
            },
        )
        cached_res = await cache_manager.get(cache_key)
        if cached_res:
            CACHE_LOOKUPS.labels(endpoint="/recommend", result="hit").inc()
            latency_ms = (time.perf_counter() - start_time) * 1000
            cached_res["cached"] = True
            cached_res["latency_ms"] = round(latency_ms, 2)
            return cached_res
        CACHE_LOOKUPS.labels(endpoint="/recommend", result="miss").inc()

    # 2. Resolve NPK defaults & rainfall (fetches weather or falls back)
    climate_data, source = await climate_resolver.resolve(payload.state, payload.district)

    # Record request features for data drift checks in background
    if drift_detector:
        background_tasks.add_task(
            drift_detector.record_request,
            get_client_ip(request),
            {
                "N": climate_data["n_avg"],
                "P": climate_data["p_avg"],
                "K": climate_data["k_avg"],
                "annual_rainfall": climate_data["annual_rainfall_avg"],
                "kharif_rainfall": climate_data["kharif_rainfall_avg"],
                "rabi_rainfall": climate_data["rabi_rainfall_avg"],
                "irrigation_ratio": climate_data["irrigation_ratio_avg"],
            }
        )

    # 3. Build full environment profile for model runner
    env_profile = {
        "N (Kg/ha)": climate_data["n_avg"],
        "P (Kg/ha)": climate_data["p_avg"],
        "K (Kg/ha)": climate_data["k_avg"],
        "Annual Rainfall (mm)": climate_data["annual_rainfall_avg"],
        "Kharif Rainfall (mm)": climate_data["kharif_rainfall_avg"],
        "Rabi Rainfall (mm)": climate_data["rabi_rainfall_avg"],
        "Irrigation Ratio": climate_data["irrigation_ratio_avg"],
        "Primary Soil Type": payload.soil_type,
        "State Name": payload.state,
    }

    # 4. Simulate all 19 crops simultaneously
    with INFERENCE_LATENCY.labels(backend="onnx").time():
        predictions = onnx_runner.predict_all_crops(env_profile)

    # Record predicted yield values for distribution monitoring
    for _crop, yield_val in predictions:
        PREDICTION_VALUES.observe(yield_val)

    # 5. Score yields using capped relative scoring + Bayesian prior
    recommendations = suitability_scorer.score_all(predictions, payload.state)

    # 6. Call LLM for explanation if requested
    explanation = None
    if payload.explain and openrouter_runner:
        if circuit_breaker.can_execute():
            try:
                with INFERENCE_LATENCY.labels(backend="openrouter").time():
                    explanation = await openrouter_runner.explain_recommendation(
                        state=payload.state,
                        district=payload.district,
                        soil_type=payload.soil_type,
                        recommendations=recommendations
                    )
                if "temporarily unavailable" in explanation:
                    circuit_breaker.record_failure()
                else:
                    circuit_breaker.record_success()
            except Exception as e:
                logger.error(f"OpenRouter advisory call failed: {e}")
                circuit_breaker.record_failure()
                explanation = "Agronomic advisory is temporarily unavailable due to external API latency."
        else:
            explanation = "Agronomic advisory is temporarily unavailable due to active circuit breaker (LLM offline)."

    latency_ms = (time.perf_counter() - start_time) * 1000

    response_data = RecommendResponse(
        status="success",
        state=payload.state,
        district=payload.district,
        recommendations=recommendations,
        climate_source=source,
        explanation=explanation,
        cached=False,
        latency_ms=round(latency_ms, 2),
    )

    # 7. Cache the Response - Always cache successful predictions regardless of explanation status
    if cache_manager and cache_manager.is_connected and cache_key:
        await cache_manager.set(cache_key, response_data.model_dump())

    return response_data


# ── Integration API Endpoints ──


@app.post("/predict", response_model=PredictResponse)
@limiter.limit("60/minute")
async def predict(request: Request, payload: PredictRequest, background_tasks: BackgroundTasks):
    """
    Integration-Facing Predict Endpoint.

    Takes explicit climate and soil values.
    Runs prediction for either a single crop, or is simulated for all 19.
    """
    start_time = time.perf_counter()

    if not onnx_runner or not suitability_scorer:
        raise HTTPException(status_code=503, detail="Service currently unavailable")

    # 1. Attempt Cache Lookup
    cache_key = None
    if cache_manager and cache_manager.is_connected:
        cache_key = cache_manager.make_cache_key("predict", payload.model_dump())
        cached_res = await cache_manager.get(cache_key)
        if cached_res:
            CACHE_LOOKUPS.labels(endpoint="/predict", result="hit").inc()
            latency_ms = (time.perf_counter() - start_time) * 1000
            cached_res["cached"] = True
            cached_res["latency_ms"] = round(latency_ms, 2)
            return cached_res
        CACHE_LOOKUPS.labels(endpoint="/predict", result="miss").inc()

    env_profile = {
        "N (Kg/ha)": payload.N,
        "P (Kg/ha)": payload.P,
        "K (Kg/ha)": payload.K,
        "Annual Rainfall (mm)": payload.annual_rainfall,
        "Kharif Rainfall (mm)": payload.kharif_rainfall,
        "Rabi Rainfall (mm)": payload.rabi_rainfall,
        "Irrigation Ratio": payload.irrigation_ratio,
        "Primary Soil Type": payload.soil_type,
        "State Name": payload.state,
    }

    # Record request features for data drift checks in background
    if drift_detector:
        background_tasks.add_task(
            drift_detector.record_request,
            get_client_ip(request),
            {
                "N": payload.N,
                "P": payload.P,
                "K": payload.K,
                "annual_rainfall": payload.annual_rainfall,
                "kharif_rainfall": payload.kharif_rainfall,
                "rabi_rainfall": payload.rabi_rainfall,
                "irrigation_ratio": payload.irrigation_ratio,
            }
        )

    # Single-crop mode vs multi-crop mode
    explanation = None
    if payload.crop:
        # Run prediction for single crop
        with INFERENCE_LATENCY.labels(backend="onnx").time():
            predicted_yield = onnx_runner.predict_single(env_profile, payload.crop)
        PREDICTION_VALUES.observe(predicted_yield)
        
        # Explain single crop
        if payload.explain and openrouter_runner:
            if circuit_breaker.can_execute():
                try:
                    with INFERENCE_LATENCY.labels(backend="openrouter").time():
                        explanation = await openrouter_runner.explain_prediction(
                            env_profile=env_profile,
                            crop=payload.crop,
                            predicted_yield=predicted_yield
                        )
                    if "temporarily unavailable" in explanation:
                        circuit_breaker.record_failure()
                    else:
                        circuit_breaker.record_success()
                except Exception as e:
                    logger.error(f"OpenRouter explanation call failed: {e}")
                    circuit_breaker.record_failure()
                    explanation = "Agronomic explanation is temporarily unavailable due to external API latency."
            else:
                explanation = "Agronomic explanation is temporarily unavailable due to active circuit breaker (LLM offline)."
                
        latency_ms = (time.perf_counter() - start_time) * 1000
        response_data = PredictResponse(
            predicted_yield=round(predicted_yield, 2),
            recommendations=None,
            unit="Kg/ha",
            explanation=explanation,
            cached=False,
            latency_ms=round(latency_ms, 2),
            model_backend="onnx",
        )
    else:
        # Multi-crop mode
        with INFERENCE_LATENCY.labels(backend="onnx").time():
            predictions = onnx_runner.predict_all_crops(env_profile)
        for _crop, yield_val in predictions:
            PREDICTION_VALUES.observe(yield_val)
        recommendations = suitability_scorer.score_all(predictions, payload.state)
        
        # Explain recommendations
        if payload.explain and openrouter_runner:
            if circuit_breaker.can_execute():
                try:
                    with INFERENCE_LATENCY.labels(backend="openrouter").time():
                        explanation = await openrouter_runner.explain_recommendation(
                            state=payload.state,
                            district=None,
                            soil_type=payload.soil_type,
                            recommendations=recommendations
                        )
                    if "temporarily unavailable" in explanation:
                        circuit_breaker.record_failure()
                    else:
                        circuit_breaker.record_success()
                except Exception as e:
                    logger.error(f"OpenRouter advisory call failed: {e}")
                    circuit_breaker.record_failure()
                    explanation = "Agronomic explanation is temporarily unavailable due to external API latency."
            else:
                explanation = "Agronomic explanation is temporarily unavailable due to active circuit breaker (LLM offline)."
                
        latency_ms = (time.perf_counter() - start_time) * 1000
        response_data = PredictResponse(
            predicted_yield=None,
            recommendations=recommendations,
            unit="Kg/ha",
            explanation=explanation,
            cached=False,
            latency_ms=round(latency_ms, 2),
            model_backend="onnx",
        )

    # 2. Cache the Response - Always cache successful predictions regardless of explanation status
    if cache_manager and cache_manager.is_connected and cache_key:
        await cache_manager.set(cache_key, response_data.model_dump())

    return response_data


@app.post("/predict/explain", response_model=PredictResponse)
@limiter.limit("10/minute")
async def predict_explain(request: Request, payload: PredictRequest, background_tasks: BackgroundTasks):
    """Convenience alias endpoint that forces explain=True."""
    payload.explain = True
    return await predict(request, payload, background_tasks)


# ── Front-End Backward Compatibility Routes ──


@app.post("/api/simulate")
@limiter.limit("30/minute")
async def legacy_simulate(request: Request, payload: RecommendRequest):
    """
    Backward-compatibility route mapping React frontend request to `/recommend` logic.

    Returns payload formatted exactly as React app expects.
    """
    res = await recommend(request, payload)

    # Format output dictionary exactly matching old React app expectations
    top_recommendations = [
        {
            "crop": r.crop,
            "expected_yield_kg_per_ha": r.expected_yield_kg_per_ha,
            "max_potential_yield": r.max_potential_yield,
            "suitability_percentage": r.suitability_percentage,
        }
        for r in res.recommendations
    ]

    return {
        "status": "success",
        "state_simulated": payload.state,
        "recommendations": top_recommendations,
        "ai_advisory": "AI Advisory is coming soon in Phase 4.",
        "metadata": {
            "generation_time_ms": int(res.latency_ms),
            "active_model": "XGBoost ONNX",
            "model_certainty_pct": top_recommendations[0]["suitability_percentage"],
            "rainfall_variability": "Moderate",  # Expose static placeholder or wire up later
            "confidence_level": "High"
            if top_recommendations[0]["suitability_percentage"] > 75
            else "Moderate",
        },
    }


@app.get("/api/districts/{state_name}")
@limiter.limit("60/minute")
async def get_districts(request: Request, state_name: str):
    """Returns list of unique districts for a state from lookup tables."""
    if not climate_resolver:
        raise HTTPException(status_code=503, detail="Service unavailable")

    # Map name mapping if needed
    lookup_state = state_name
    if lookup_state.upper() == "ODISHA":
        lookup_state = "Orissa"

    # Search defaults
    state_data = climate_resolver.defaults.get(lookup_state)
    if not state_data:
        # Loop to scan case insensitive
        for s_name, s_val in climate_resolver.defaults.items():
            if s_name.upper() == lookup_state.upper():
                state_data = s_val
                break

    if not state_data:
        return []

    districts = list(state_data.get("districts", {}).keys())
    return districts


@app.get("/api/defaults/{state_name}")
@limiter.limit("60/minute")
async def get_defaults(request: Request, state_name: str, district: Optional[str] = None):
    """Returns NPK and rainfall default values from lookup tables."""
    if not climate_resolver:
        raise HTTPException(status_code=503, detail="Service unavailable")

    # Map name mapping if needed
    lookup_state = state_name
    if lookup_state.upper() == "ODISHA":
        lookup_state = "Orissa"

    # Search defaults
    state_data = climate_resolver.defaults.get(lookup_state)
    if not state_data:
        # Loop to scan case insensitive
        for s_name, s_val in climate_resolver.defaults.items():
            if s_name.upper() == lookup_state.upper():
                state_data = s_val
                break

    if not state_data:
        return {}

    if district:
        district_data = state_data.get("districts", {}).get(district)
        if not district_data:
            # Try case insensitive
            for d_name, d_val in state_data.get("districts", {}).items():
                if d_name.upper() == district.upper():
                    district_data = d_val
                    break
        return district_data if district_data else {}

    return state_data


@app.get("/health", response_model=HealthResponse)
@limiter.limit("120/minute")
async def health(request: Request):
    """Health check endpoint confirming load status of pipeline dependencies."""
    redis_status = False
    if cache_manager:
        redis_status = await cache_manager.ping()

    return HealthResponse(
        status="healthy",
        onnx_loaded=(onnx_runner is not None),
        redis_connected=redis_status,
        llm_available=(openrouter_runner is not None),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

