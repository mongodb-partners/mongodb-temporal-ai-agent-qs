# Troubleshooting Guide

## Quick Diagnostics

### System Health Check

Run this command to check all services:

```bash
# Quick health check script
uv run python -c "
import requests

print('Checking services...')

# API
try:
    r = requests.get('http://localhost:8000/health')
    print('✅ API: Running' if r.status_code == 200 else '❌ API: Not responding')
except Exception:
    print('❌ API: Not running')

# Temporal UI
try:
    r = requests.get('http://localhost:8080')
    print('✅ Temporal UI: Running' if r.status_code == 200 else '❌ Temporal UI: Not responding')
except Exception:
    print('❌ Temporal: Not running')

# Dashboard
try:
    r = requests.get('http://localhost:8501')
    print('✅ Dashboard: Running' if r.status_code == 200 else '❌ Dashboard: Not responding')
except Exception:
    print('❌ Dashboard: Not running')
"
```

## Common Issues and Solutions

### 1. MongoDB Connection Issues

#### Problem: "ServerSelectionTimeoutError"

**Symptoms:**
```
pymongo.errors.ServerSelectionTimeoutError: cluster.mongodb.net:27017: [Errno 8] nodename nor servname provided, or not known
```

**Solutions:**

1. **Check MongoDB URI format:**
```bash
# Correct format
mongodb+srv://username:password@cluster.mongodb.net/

# Common mistakes
mongodb://username:password@cluster.mongodb.net/  # Missing +srv
mongodb+srv://username:password@cluster/          # Missing .mongodb.net
```

2. **Verify IP whitelist:**
- Go to MongoDB Atlas → Network Access
- Add your IP or use 0.0.0.0/0 for PoV
- Wait 60 seconds for changes to propagate

3. **Test connection:**
```bash
uv run python -c "
from pymongo import MongoClient
import os
client = MongoClient(os.getenv('MONGODB_URI'))
print(client.server_info()['version'])
"
```

4. **Check network connectivity:**
```bash
# Test DNS resolution
nslookup cluster.mongodb.net

# Test network path
traceroute cluster.mongodb.net
```

#### Problem: "Authentication failed"

**Solutions:**

1. **Verify credentials:**
```bash
# Check username/password in .env
grep MONGODB_URI .env

# Test with mongosh
mongosh "mongodb+srv://cluster.mongodb.net/" --username user
```

2. **Check database user permissions:**
- Atlas → Database Access
- Ensure user has "readWrite" on database
- Verify password doesn't contain special characters or URL-encode them

### 2. Temporal Workflow Issues

#### Problem: "Worker not processing workflows"

**Symptoms:**
- Workflows stuck in "Running" state
- No activity execution in logs

**Solutions:**

1. **Check worker is running:**
```bash
# Check process
ps aux | grep run_worker

# Check logs (Docker)
docker compose logs -f temporal-worker

# Restart worker (local)
pkill -f run_worker
uv run python -m temporal.run_worker

# Restart worker (Docker)
docker compose restart temporal-worker
```

2. **Verify task queue name:**
```python
# In .env
TEMPORAL_TASK_QUEUE=transaction-processing-queue

# Must match in worker and workflow
```

3. **Check Temporal server:**
```bash
# Local Temporal
docker ps | grep temporal
docker logs temporal

# Restart if needed
cd docker-compose && docker compose restart temporal
```

#### Problem: "Workflow execution failed"

**Solutions:**

1. **Check Temporal UI for errors:**
- Open http://localhost:8080
- Find workflow execution
- Check "History" tab for failures

2. **Review activity errors:**
```python
# Add debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

3. **Reset workflow state:**
```bash
# Terminate stuck workflow via the Temporal CLI
temporal workflow terminate --workflow-id <id>

# Re-submit by POSTing the same payload again — workflow IDs are
# unique per transaction_id, so a fresh transaction creates a fresh run.
curl -X POST http://localhost:8000/api/transaction \
  -H 'Content-Type: application/json' \
  -d @transaction.json
```

### 3. AWS Bedrock Issues

#### Problem: "AccessDeniedException"

**Symptoms:**
```
botocore.exceptions.ClientError: An error occurred (AccessDeniedException) when calling the InvokeModel operation
```

**Solutions:**

1. **Verify AWS credentials:**
```bash
# Check credentials
aws configure list

# Test Bedrock access
aws bedrock list-foundation-models --region us-east-1
```

2. **Check model access:**
- AWS Console → Bedrock → Model access
- Request access to Claude and Cohere
- Wait for approval (can take 1-2 minutes)

3. **Verify IAM permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    }
  ]
}
```

4. **Switch to the alternative LLM provider:**
```bash
# In .env — use Groq instead of Bedrock for the LLM
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_key
```

#### Problem: "Model timeout"

**Solutions:**

1. **Check AWS region:**
```bash
# Ensure using correct region
AWS_REGION=us-east-1  # or us-west-2
```

2. **Switch LLM provider** if Bedrock is degraded:
```bash
LLM_PROVIDER=groq
```

3. **Tune client parameters** — `max_tokens`, `temperature`, and timeouts
   are pinned in `ai/bedrock_client.py` and `ai/groq_client.py`. Edit
   them there and rebuild Docker images if needed.

### 4. API Server Issues

#### Problem: "Port already in use"

**Symptoms:**
```
ERROR: [Errno 48] Address already in use
```

**Solutions:**

1. **Find and kill process:**
```bash
# Find process using port 8000
lsof -i :8000

# Kill process
kill -9 <PID>

# Or use different port
uvicorn api.main:app --port 8001
```

2. **Check for multiple instances:**
```bash
ps aux | grep uvicorn
pkill -f uvicorn
```

#### Problem: "Module not found"

**Solutions:**

1. **Sync the uv environment:**
```bash
uv sync --extra dev
```

2. **Run commands through uv so they pick up the project venv:**
```bash
uv run python -m scripts.setup_mongodb
uv run pytest
```

3. **Force a clean re-resolve if dependencies look stale:**
```bash
uv lock --upgrade
uv sync --frozen --extra dev
```

4. **Check Python path:**
```bash
uv run python -c "import sys; print(sys.path)"
```

### 5. Dashboard Issues

#### Problem: "Dashboard not updating"

**Solutions:**

1. **Check API connection:**
```python
# In app.py, verify API_BASE_URL
import os
print(os.getenv('API_BASE_URL', 'http://localhost:8000/api'))
```

2. **Clear Streamlit cache:**
```bash
# Stop dashboard
# Delete cache
rm -rf ~/.streamlit/cache

# Restart
uv run streamlit run app.py
```

3. **Check browser console:**
- Open Developer Tools (F12)
- Check Console for errors
- Check Network tab for failed requests

#### Problem: "Session state errors"

**Solutions:**

1. **Refresh browser:**
- Hard refresh: Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows)

2. **Clear cookies:**
- Clear site data for localhost:8501

3. **Restart Streamlit:**
```bash
pkill -f streamlit
uv run streamlit run app.py --server.runOnSave true
```

### 6. Docker Issues

#### Problem: "Cannot connect to Docker daemon"

**Solutions:**

1. **Start Docker Desktop:**
- Open Docker Desktop application
- Wait for "Docker Desktop is running"

2. **Check Docker service:**
```bash
# Mac/Windows
docker version

# Linux
sudo systemctl start docker
sudo usermod -aG docker $USER
```

#### Problem: "Container name conflicts"

**Solutions:**

1. **Remove existing containers:**
```bash
docker compose down
docker rm -f $(docker ps -aq)
docker compose up -d
```

2. **Clean Docker system:**
```bash
docker system prune -a
docker volume prune
```

### 7. Vector Search Issues

#### Problem: "No similar transactions found"

**Solutions:**

1. **Check vector index exists:**
```javascript
// In MongoDB Atlas
db.transactions.getIndexes()

// Should show vector index
{
  "name": "transaction_vector_index",
  "type": "vectorSearch"
}
```

2. **Rebuild the vector index** by re-running setup; `setup_mongodb`
   creates the Atlas vector-search index `transaction_vector_index`
   when missing and is idempotent:
```bash
uv run python -m scripts.setup_mongodb
```

3. **Verify embeddings are stored:**
```python
from database.connection import get_sync_db
db = get_sync_db()
doc = db["transactions"].find_one({"embedding": {"$exists": True}})
print(f"Embedding dimension: {len(doc['embedding'])}")  # Should be 1024
```

4. **Lower the similarity threshold** in `utils/config.py`
   (`SIMILARITY_THRESHOLD = 0.75`) and rebuild Docker images. The
   threshold is not env-configurable.

### 8. Performance Issues

#### Problem: "Slow transaction processing"

**Solutions:**

1. **Check system resources:**
```bash
# CPU and memory
top
htop

# Disk I/O
iostat -x 1

# Network
netstat -i
```

2. **Tune concurrency and pools in source.** Worker concurrency is
   set in `temporal/run_worker.py` (Temporal SDK defaults apply
   unless overridden). MongoDB pool sizes live in
   `database/connection.py::MONGO_CLIENT_OPTIONS`. Edit and rebuild.

3. **Profile slow MongoDB queries:**
```javascript
// MongoDB profiler
db.setProfilingLevel(1, { slowms: 100 })
db.system.profile.find().limit(5).sort({ ts: -1 })
```

## Debugging Tools

### 1. Application Logs

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Tail logs (TransactionLogger writes to ./logs/ when run locally)
tail -f logs/transaction_processor.log

# Errors only
tail -f logs/transaction_errors.log

# Audit trail (one JSON per line)
tail -f logs/transaction_audit.log

# Under Docker
docker compose logs -f api
docker compose logs -f temporal-worker
docker compose logs -f streamlit
```

### 2. Temporal CLI

```bash
# List workflows
temporal workflow list

# Describe workflow
temporal workflow describe --workflow-id <id>

# Show workflow history
temporal workflow show --workflow-id <id>

# Query workflow state
temporal workflow query --workflow-id <id> --name get_status
```

### 3. MongoDB Shell

```javascript
// Connect to MongoDB
mongosh $MONGODB_URI

// Check collections
show collections

// Count documents
db.transactions.countDocuments()

// Find recent errors
db.audit_events.find({level: "ERROR"}).sort({timestamp: -1}).limit(10)

// Check indexes
db.transactions.getIndexes()
```

### 4. Python Debug Console

```python
# Interactive debugging
python -i debug_console.py

# Or in code
import pdb; pdb.set_trace()

# Or with IPython
from IPython import embed; embed()
```

## Error Messages Reference

### Critical Errors

| Error | Meaning | Action |
|-------|---------|--------|
| `ConnectionError` | Service unreachable | Check service is running |
| `AuthenticationError` | Invalid credentials | Verify username/password |
| `TimeoutError` | Operation timed out | Increase timeout, check network |
| `PermissionError` | Insufficient access | Check IAM/database permissions |
| `ValidationError` | Invalid input data | Check request format |

### Warning Messages

| Warning | Meaning | Action |
|---------|---------|--------|
| `Retry attempt X` | Transient failure | Monitor, may self-resolve |
| `Connection pool full` | High load | Increase pool size |
| `Slow query` | Performance issue | Add index, optimize query |
| `Cache miss` | Cache expired | Normal, will repopulate |

## Recovery Procedures

### 1. Full System Restart

```bash
# Stop application services
docker compose down

# Stop Temporal infrastructure
cd docker-compose && docker compose down && cd ..

# Clean up logs and dangling containers
rm -rf logs/*.log
docker system prune -f

# Start fresh
cd docker-compose && docker compose up -d && cd ..
docker compose up -d

# Re-seed MongoDB if needed
uv run python -m scripts.setup_mongodb
```

### 2. Database Recovery

```bash
# Backup current data
mongodump --uri="$MONGODB_URI" --out=backup/

# Clean database (preserves rules)
uv run python -c "
from database.connection import get_sync_db
db = get_sync_db()
for collection in db.list_collection_names():
    if collection != 'rules':
        db[collection].delete_many({})
"

# Restore or reinitialize
mongorestore --uri="$MONGODB_URI" backup/
# OR re-seed via the idempotent setup script
uv run python -m scripts.setup_mongodb
```

### 3. Workflow Recovery

```bash
# List stuck workflows
temporal workflow list --query='ExecutionStatus="Running"'

# Terminate all stuck workflows
temporal workflow list --query='ExecutionStatus="Running"' \
  | grep WORKFLOW_ID \
  | awk '{print $2}' \
  | xargs -I {} temporal workflow terminate --workflow-id {}

# Restart worker (local)
uv run python -m temporal.run_worker

# Restart worker (Docker)
docker compose restart temporal-worker
```

## Getting Help

### Diagnostic Information to Collect

When reporting issues, include:

1. **Environment details:**
```bash
python --version
uv pip list | grep -E "temporal|pymongo|fastapi|streamlit|voyageai|groq|boto3"
docker version
```

2. **Configuration (sanitized):**
```bash
cat .env | sed 's/=.*/=***/'
```

3. **Error logs:**
```bash
tail -n 100 logs/app.log
docker logs temporal
```

4. **System resources:**
```bash
df -h
free -m
ulimit -a
```

### Support Channels

- **GitHub Issues:** Create issue with logs


## Preventive Measures

### Daily Checks

1. Monitor disk space: `df -h`
2. Check service health: `curl http://localhost:8000/health`
3. Review error logs: `grep ERROR logs/*.log`

### Weekly Maintenance

1. Restart services: `docker compose restart`
2. Clean old logs: `find logs/ -mtime +7 -delete`
3. Re-run the idempotent setup to repair indexes:
   `uv run python -m scripts.setup_mongodb`
4. Review metrics on the dashboard / `GET /api/metrics`

### Before Demo/Evaluation

1. Run full test suite: `uv run pytest --cov`
2. Run advanced scenarios: `uv run python -m scripts.advanced_scenarios`
3. Re-seed test data: `uv run python -m scripts.setup_mongodb`
4. Verify all services: `docker compose ps` + `curl /health`
5. Test critical paths manually via the dashboard

---

For issues not covered in this guide, check the [GitHub repository](https://github.com/mongodb-partners/maap-temporal-ai-agent-qs) or contact the development team.