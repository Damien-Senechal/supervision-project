import time
import random
from fastapi import FastAPI, Request, Response
from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
)

app = FastAPI(title="Demo API")

# ── Métriques Prometheus ──────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "Nombre total de requêtes HTTP",
    ["method", "endpoint", "status_code"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Durée des requêtes HTTP en secondes",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Requêtes HTTP en cours de traitement",
    ["method", "endpoint"]
)

app_info = Gauge(
    "app_info",
    "Informations sur l'application",
    ["version", "env"]
)
app_info.labels(version="1.0.0", env="production").set(1)


# ── Middleware d'instrumentation ──────────────────────────────────────────────

@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    endpoint = request.url.path
    method = request.method

    # Ignore /metrics pour ne pas polluer les stats
    if endpoint == "/metrics":
        return await call_next(request)

    http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()
    start = time.time()

    try:
        response = await call_next(request)
        status_code = str(response.status_code)
    except Exception:
        status_code = "500"
        raise
    finally:
        duration = time.time() - start
        http_requests_total.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
        http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)
        http_requests_in_progress.labels(method=method, endpoint=endpoint).dec()

    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"message": "API de démonstration - supervision Prometheus/Grafana"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/users")
async def get_users():
    # Simule une latence variable (10–200ms)
    time.sleep(random.uniform(0.01, 0.2))
    return {"users": [{"id": i, "name": f"User {i}"} for i in range(1, 6)]}


@app.get("/api/orders")
async def get_orders():
    # Simule une latence plus élevée parfois (p95 ≈ 400ms)
    time.sleep(random.uniform(0.05, 0.5))
    # Simule 5% d'erreurs 500
    if random.random() < 0.05:
        return Response(content='{"error": "internal error"}', status_code=500, media_type="application/json")
    return {"orders": [{"id": i, "total": round(random.uniform(10, 500), 2)} for i in range(1, 4)]}


@app.get("/api/products")
async def get_products():
    time.sleep(random.uniform(0.005, 0.1))
    # Simule 10% d'erreurs 404
    if random.random() < 0.1:
        return Response(content='{"error": "not found"}', status_code=404, media_type="application/json")
    return {"products": [{"id": i, "name": f"Product {i}", "price": round(random.uniform(5, 200), 2)} for i in range(1, 8)]}


@app.get("/api/slow")
async def slow_endpoint():
    # Endpoint intentionnellement lent pour déclencher des alertes
    time.sleep(random.uniform(0.8, 2.0))
    return {"message": "réponse lente simulée"}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
