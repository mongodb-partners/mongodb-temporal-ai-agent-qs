# Configuration Guide

## Overview

This document is the reference for all environment variables the
application reads. Configuration is loaded from a `.env` file via
`python-dotenv` (see `utils/config.py`).

The list of recognised variables matches `utils/config.py` and
`.env.example` exactly. Any variable not listed here is not consumed
by the application.

## Quick Configuration

### Minimal required configuration

Create a `.env` file with these settings:

```bash
# MongoDB Atlas (required)
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/

# AWS Bedrock (required for the primary LLM and embedding fallback)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
```

### Copy from template

```bash
cp .env.example .env
# Edit .env with your credentials
```

## Complete Configuration Reference

### MongoDB

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGODB_URI` | MongoDB Atlas connection string (required) | — |
| `MONGODB_DB_NAME` | Database name | `transaction_ai_poc` |

Pool, timeout, and retry behaviour are not configurable via env vars.
They are pinned in `database/connection.py::MONGO_CLIENT_OPTIONS`
following the `mongodb-connection` skill's "High-Traffic / Bursty"
OLTP profile (maxPoolSize 50, minPoolSize 5, serverSelectionTimeoutMS
5000, etc.). Edit that constant directly if you need to retune.

**MongoDB Atlas setup:**

1. Create a free cluster at [mongodb.com/atlas](https://mongodb.com/atlas).
2. Configure network access (whitelist IP, or `0.0.0.0/0` for PoV).
3. Create a database user with read/write permissions.
4. Get the connection string from "Connect" → "Connect your application".

### Temporal

| Variable | Description | Default |
|----------|-------------|---------|
| `TEMPORAL_HOST` | Temporal server address | `temporal:7233` |
| `TEMPORAL_NAMESPACE` | Workflow namespace | `default` |
| `TEMPORAL_TASK_QUEUE` | Task queue name | `transaction-processing-queue` |

Activity timeouts, retry policies, and the auto-approval timeout (24h)
are defined inline in `temporal/workflows.py` and the per-activity
`workflow.execute_activity` calls. They are not configurable via env.

**Docker vs local:**

- Local development: `TEMPORAL_HOST=localhost:7233`
- Docker deployment: `TEMPORAL_HOST=temporal:7233` (the default)

### AWS Bedrock

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_REGION` | AWS region | `us-east-1` |
| `AWS_ACCESS_KEY_ID` | AWS access key (required) | — |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key (required) | — |
| `BEDROCK_MODEL_VERSION` | Claude model id used for transaction analysis | `us.anthropic.claude-opus-4-1-20250805-v1:0` |

Other Bedrock parameters (max tokens, temperature, top_p) are pinned
in `ai/bedrock_client.py` and not exposed as env vars.

**Bedrock setup:**

1. Enable Bedrock in the AWS console.
2. Request access to the Claude and Cohere `embed-english-v3` models
   (approval is typically near-instant).
3. Create an IAM user with `bedrock:InvokeModel` and
   `bedrock:InvokeModelWithResponseStream`, and generate access keys.

### LLM Provider Selection

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | LLM backend: `bedrock` or `groq` | `bedrock` |

`bedrock` is the primary supported LLM (Claude via Bedrock); `groq`
is offered as an alternative for environments without Bedrock access.

### Groq (alternative LLM)

Required when `LLM_PROVIDER=groq`.

| Variable | Description | Default |
|----------|-------------|---------|
| `GROQ_API_KEY` | Groq API key | — |
| `GROQ_MODEL_ID` | Groq model id | `openai/gpt-oss-120b` |

### Embeddings

| Variable | Description | Default |
|----------|-------------|---------|
| `VOYAGE_API_KEY` | Voyage AI API key (primary embeddings) | — |
| `VOYAGE_MODEL` | Voyage embedding model | `voyage-4` |
| `VOYAGE_OUTPUT_DIMENSION` | Embedding dimension | `1024` |

The Cohere fallback model id is hardcoded in `utils/config.py` as
`cohere.embed-english-v3` and uses the same Bedrock credentials as the
LLM. `VOYAGE_OUTPUT_DIMENSION` must remain `1024` to match the Atlas
vector-search index dimension.

If `VOYAGE_API_KEY` is unset or the Voyage call fails, the
`EmbeddingClient` automatically falls back to Cohere via Bedrock.

### Application

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | Environment label (`development`, `production`, etc.) | `development` |
| `LOG_LEVEL` | Python logging level | `INFO` |
| `API_BASE_URL` | URL the Streamlit dashboard uses to reach the API | `http://localhost:8000/api` |

The dashboard reads `API_BASE_URL` to call the FastAPI server. Use
`http://api:8000/api` when running under Docker Compose so the
container can reach the API service by its service name.

### Decision Thresholds

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIDENCE_THRESHOLD_APPROVE` | Min AI confidence (%) to auto-approve | `85` |
| `CONFIDENCE_THRESHOLD_ESCALATE` | Min confidence floor below which decisions escalate | `70` |
| `AUTO_APPROVAL_LIMIT` | Amount (USD) above which approved transactions require manager approval | `50000` |

### Hardcoded Settings (not env-driven)

These are defined in `utils/config.py` as class attributes. Edit the
file (and rebuild Docker images) to change them:

| Setting | Value | Purpose |
|---------|-------|---------|
| `HIGH_RISK_COUNTRIES` | `["RU", "IR", "KP", "SY", "AF", "YE"]` | Sanctions / OFAC screening |
| `VECTOR_SEARCH_INDEX` | `transaction_vector_index` | Atlas vector-search index name |
| `VECTOR_DIMENSION` | `1024` | Embedding length |
| `MAX_SIMILAR_CASES` | `10` | Top-k for hybrid search |
| `SIMILARITY_THRESHOLD` | `0.75` | Min combined score for a similar case |

Collection names (`transactions`, `transaction_decisions`,
`customers`, `accounts`, `human_reviews`, `audit_events`,
`notifications`, `system_metrics`, `rules`, `transaction_journal`,
`balance_updates`, `balance_holds`) are also pinned in
`utils/config.py`.

## Environment-Specific Examples

### Development

```bash
APP_ENV=development
LOG_LEVEL=DEBUG
TEMPORAL_HOST=localhost:7233
API_BASE_URL=http://localhost:8000/api
LLM_PROVIDER=bedrock
```

### Docker (project root `.env`)

```bash
APP_ENV=docker
TEMPORAL_HOST=temporal:7233
API_BASE_URL=http://api:8000/api
```

### Production

```bash
APP_ENV=production
LOG_LEVEL=WARNING
TEMPORAL_HOST=temporal.production.internal:7233
TEMPORAL_NAMESPACE=production
LLM_PROVIDER=bedrock

CONFIDENCE_THRESHOLD_APPROVE=90
AUTO_APPROVAL_LIMIT=100000
```

## Validation

There is no dedicated validation script. To check that configuration
is healthy, hit the API health endpoint after startup:

```bash
curl http://localhost:8000/health
```

Sample response:

```json
{
  "status": "healthy",
  "timestamp": "2026-05-11T10:47:45.522207+00:00",
  "mongodb": "connected",
  "temporal": "connected",
  "embedding": {
    "primary_model": "voyage-4",
    "voyage_available": true,
    "cohere_available": true,
    "available_models": ["voyage-4", "cohere.embed-english-v3"]
  }
}
```

`mongodb=disconnected` means `connect_to_mongo()` was never called
or the client was closed; `temporal=disconnected` means the FastAPI
startup event failed to reach `TEMPORAL_HOST`.

## Configuration Best Practices

### Security

- Never commit `.env` files to version control. `.env` is in
  `.gitignore`; `.env.example` is the only template that should ship.
- Use AWS IAM roles in production rather than long-lived access keys.
- Rotate the `GROQ_API_KEY` and `VOYAGE_API_KEY` regularly.

### Performance

- Pool sizes and timeouts live in `database/connection.py`. Adjust
  there based on load testing — they are not env-driven.
- Voyage is faster and finance-tuned; keep `VOYAGE_API_KEY` set so the
  Cohere fallback is only invoked on transient failures.

### Reliability

- Workflow retry policies (`maximum_attempts=5`, exponential backoff)
  are defined in `temporal/workflows.py`. Tune there if needed.
- The `EmbeddingClient` falls back automatically; no extra
  configuration is required.

## Docker Compose

Both compose files load environment from the project's `.env`:

```yaml
# docker-compose.yml (project root)
services:
  api:
    env_file: [.env]
  temporal-worker:
    env_file: [.env]
  streamlit:
    env_file: [.env]
```

To override a single variable for a single service without editing
`.env`, create a `docker-compose.override.yml`:

```yaml
services:
  api:
    environment:
      - LOG_LEVEL=DEBUG
```

## Kubernetes

The repo does not ship Kubernetes manifests. If you produce them
yourself, the canonical mapping is:

- ConfigMap: `APP_ENV`, `LOG_LEVEL`, `TEMPORAL_HOST`,
  `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `API_BASE_URL`,
  `LLM_PROVIDER`, `BEDROCK_MODEL_VERSION`, `GROQ_MODEL_ID`,
  `VOYAGE_MODEL`, `VOYAGE_OUTPUT_DIMENSION`, `MONGODB_DB_NAME`,
  `CONFIDENCE_THRESHOLD_APPROVE`, `CONFIDENCE_THRESHOLD_ESCALATE`,
  `AUTO_APPROVAL_LIMIT`.
- Secret: `MONGODB_URI`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `GROQ_API_KEY`, `VOYAGE_API_KEY`.

## Troubleshooting

| Issue | Likely cause | Fix |
|-------|--------------|-----|
| MongoDB connection timeout | IP not whitelisted, malformed URI | Whitelist your IP in Atlas; verify URI format |
| Bedrock `AccessDeniedException` | Model access not granted | Request access to Claude + Cohere in the Bedrock console |
| `mongodb=disconnected` in health | Async client never initialised | Restart the API; check API logs for `connect_to_mongo` errors |
| `temporal=disconnected` in health | Wrong `TEMPORAL_HOST`, server not running | Check `docker ps` for the temporal container |
| Voyage embeddings missing | `VOYAGE_API_KEY` not set | Add the key, or accept the Cohere fallback |
| Streamlit can't reach API | Wrong `API_BASE_URL` | Use `http://api:8000/api` under Docker, `http://localhost:8000/api` locally |

For deeper troubleshooting see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
