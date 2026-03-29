# Rapport de projet — Supervision production Prometheus + Grafana

**Matière** : Ops/DevOps — Supervision  
**Sujet** : Mise en place d'une supervision exploitable en production  

---

## 1. Contexte et objectifs

L'objectif du projet est de construire une stack de supervision opérationnelle capable de répondre en temps réel aux questions clés d'une équipe Ops :

- Le service est-il UP ?
- Quel est le taux d'erreur ?
- Quelle est la latence (p95) ?
- Y a-t-il de la saturation CPU/RAM ?
- Quel endpoint est responsable d'un incident ?

Pour cela, nous avons déployé une stack complète **Prometheus + Grafana + Alertmanager** autour d'une API web instrumentée, le tout reproductible via un seul `docker compose up`.

---

## 2. Architecture de la solution

```
FastAPI (demo-api)  →  /metrics  ←┐
                                   │
node_exporter       →  /metrics  ← Prometheus → Grafana (dashboards)
                                   │
                                   └→ Alertmanager (routing alertes)

traffic-generator → appels HTTP continus → demo-api
```

### Composants déployés

| Service | Image | Rôle |
|---|---|---|
| `demo-api` | FastAPI custom | API applicative exposant des métriques Prometheus |
| `node-exporter` | prom/node-exporter | Métriques système (CPU, RAM, FS) |
| `prometheus` | prom/prometheus | Collecte, évaluation des règles |
| `alertmanager` | prom/alertmanager | Routage et déduplication des alertes |
| `grafana` | grafana/grafana | Visualisation et dashboards |
| `traffic-generator` | curlimages/curl | Génération de trafic HTTP simulé |

Tous les services sont reliés via un réseau Docker dédié `monitoring`. Les données Prometheus et Grafana sont persistées dans des volumes nommés.

---

## 3. Cible applicative — API FastAPI instrumentée

Nous avons développé une petite API FastAPI exposant plusieurs endpoints (`/api/users`, `/api/orders`, `/api/products`, `/api/slow`) avec des comportements volontairement variés : latences aléatoires, taux d'erreur simulés (5% de 500 sur `/orders`, 10% de 404 sur `/products`).

L'instrumentation repose sur **trois types de métriques Prometheus** :

- `http_requests_total` (Counter) — comptage des requêtes par méthode, endpoint et code HTTP
- `http_request_duration_seconds` (Histogram) — distribution de la latence avec buckets fins
- `http_requests_in_progress` (Gauge) — requêtes en cours de traitement

Ces métriques sont exposées sur `/metrics` et scrapées par Prometheus toutes les 10 secondes.

---

## 4. Modèle de supervision — SLI / SLO

### SLI 1 — Disponibilité

**Définition** : proportion de requêtes HTTP ayant reçu une réponse non-5xx.

```promql
1 - (
  sum(rate(http_requests_total{status_code=~"5.."}[5m]))
  / sum(rate(http_requests_total[5m]))
)
```

**SLO** : ≥ 99,5% de succès sur 5 minutes glissantes.  
**Justification** : un taux d'erreur de 0,5% maximum (1 requête sur 200) est un seuil réaliste pour une API interne, laissant une marge face aux erreurs ponctuelles sans masquer un vrai problème.

### SLI 2 — Latence p95

**Définition** : 95e percentile du temps de réponse de l'API.

```promql
histogram_quantile(
  0.95,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
)
```

**SLO** : p95 ≤ 500 ms sur 5 minutes glissantes.  
**Justification** : 500 ms est une limite UX reconnue pour les APIs REST synchrones. Le p95 (plutôt que la moyenne) cible l'expérience des utilisateurs dans le pire décile, ce qui est plus représentatif de la qualité réelle du service.

---

## 5. Requêtes PromQL

Six requêtes couvrant l'ensemble du périmètre demandé :

| # | Objectif | Requête |
|---|---|---|
| 1 | **UP** | `up{job="demo-api"}` |
| 2 | **Trafic (req/s)** | `sum(rate(http_requests_total[2m])) by (endpoint)` |
| 3 | **Taux d'erreur 5xx** | `sum(rate(http_requests_total{status_code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` |
| 4 | **Latence p95** | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))` |
| 5 | **CPU système** | `100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)` |
| 6 | **Top 5 endpoints** | `topk(5, sum(rate(http_requests_total[5m])) by (endpoint))` |

Toutes les requêtes utilisent `rate()` sur des compteurs (jamais `increase()` seul), avec des fenêtres adaptées (2–5 min) pour équilibrer réactivité et bruit.

---

## 6. Dashboards Grafana

### Dashboard N1 — Vue principale

Dashboard opérationnel avec **10 panneaux** organisés en 3 lignes :

- **Ligne 1 (stats)** : Service UP, taux de succès (SLO), latence p95 (SLO), trafic global — lecture en 5 secondes
- **Ligne 2 (séries temporelles)** : Trafic par endpoint, taux d'erreur 4xx/5xx, latence p50/p95/p99, CPU système
- **Ligne 3** : Mémoire disponible, Top 5 endpoints (bargauge avec drilldown)

Une **variable `endpoint`** permet de filtrer tous les panneaux sur un endpoint précis. Le panneau "Top 5 endpoints" contient un **lien de drilldown** vers le dashboard N2.

### Dashboard N2 — Diagnostic

Dashboard d'investigation avec filtre par endpoint, affichant : erreurs par code HTTP, distribution de latence (p50/p95/p99), requêtes en cours, répartition des codes en camembert, saturation CPU par mode.

---

## 7. Alertes

Quatre alertes configurées dans Prometheus, routées via Alertmanager :

### Alerte 1 — `HighErrorRate` (critical)
- **Condition** : taux d'erreur 5xx > 0,5% pendant 2 minutes
- **Contexte** : alerte symptôme métier, directement liée au SLO de disponibilité
- **Action** : identifier l'endpoint, consulter les logs, vérifier la saturation

### Alerte 2 — `HighP95Latency` (warning)
- **Condition** : p95 > 500ms sur un endpoint pendant 3 minutes
- **Contexte** : alerte symptôme métier, liée au SLO de latence
- **Action** : vérifier la saturation CPU/RAM, détecter un pic de trafic

### Alerte 3 — `LowMemoryAvailable` (warning)
- **Condition** : mémoire disponible < 10% du total pendant 5 minutes
- **Contexte** : alerte saturation système, risque d'OOM killer
- **Action** : `docker stats`, identifier le container consommateur

### Alerte 4 — `PrometheusTargetDown` (critical)
- **Condition** : `up == 0` pendant 1 minute
- **Contexte** : alerte qualité de collecte — les métriques ne sont plus remontées
- **Action** : vérifier l'état du container, connectivité réseau, logs

Chaque alerte contient des `labels` (service, severity, env) et des `annotations` avec un message clair, les étapes de résolution et un lien vers le dashboard concerné. Une règle d'inhibition évite les faux positifs sur les alertes métier quand une target est déjà signalée DOWN.

---

## 8. Reproductibilité

L'ensemble du projet est livré dans un repo Git. Le lancement complet se fait en une commande :

```bash
docker compose up -d --build
```

Les dashboards Grafana sont chargés automatiquement via le système de **provisioning** (pas de manipulation manuelle). Le générateur de trafic démarre simultanément, ce qui garantit des données visibles immédiatement dans Grafana.
