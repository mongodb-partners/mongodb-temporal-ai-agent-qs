#!/bin/bash

set -e

echo "🚀 Setting up Transaction AI PoV Application"
echo "=========================================="

# Require uv (https://docs.astral.sh/uv/).
if ! command -v uv >/dev/null 2>&1; then
    echo "❌ uv is not installed. Install it from https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Sync dependencies (creates .venv from uv.lock; --extra dev includes pytest).
echo "📚 Installing dependencies via uv ..."
uv sync --frozen --extra dev

# Check for .env file
if [ ! -f ".env" ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env with your MongoDB URI and AWS credentials"
    echo "   Then re-run this script to complete setup."
    exit 1
fi

# Source .env file
export $(cat .env | grep -v '^#' | xargs)

# Check required environment variables
if [ -z "$MONGODB_URI" ]; then
    echo "❌ MONGODB_URI not set in .env file"
    echo "   Please add your MongoDB Atlas connection string"
    exit 1
fi

if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    echo "⚠️  AWS credentials not set. AI features will use mock mode."
fi

# Start Docker services
echo "🐳 Starting Docker services (Temporal)..."
docker network inspect temporal-network >/dev/null 2>&1 || docker network create temporal-network

if [ ! -d docker-compose ]; then
    git clone https://github.com/temporalio/docker-compose.git
fi
cd docker-compose
grep -q "external: true" docker-compose.yml || echo -e "    external: true" >> docker-compose.yml
docker-compose up -d
cd ..

# Wait for Temporal to be ready
echo "⏳ Waiting for Temporal to start..."
sleep 10

# Setup MongoDB
echo "🗄️ Setting up MongoDB collections and indexes..."
uv run python -m scripts.setup_mongodb || {
    echo "⚠️  MongoDB setup failed. Check your connection string."
    echo "   You can retry with: uv run python -m scripts.setup_mongodb"
}

# Function to open URL in browser
open_browser() {
    local url=$1
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        open "$url" &>/dev/null
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        xdg-open "$url" &>/dev/null || sensible-browser "$url" &>/dev/null || echo "Please open $url in your browser"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        # Windows
        start "$url" &>/dev/null
    else
        echo "Please open $url in your browser"
    fi
}

# Start application services
echo ""
echo "🚀 Starting application services..."
echo "================================"

# Start Temporal Worker in background
echo "Starting Temporal Worker..."
uv run python -m temporal.run_worker &
WORKER_PID=$!

# Start API server in background
echo "Starting API server..."
uv run uvicorn api.main:app --reload --port 8000 &
API_PID=$!

# Start Streamlit in background
echo "Starting Streamlit dashboard..."
uv run streamlit run app.py --server.port 8501 --server.address 0.0.0.0 &
STREAMLIT_PID=$!

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down services..."
    kill $WORKER_PID $API_PID $STREAMLIT_PID 2>/dev/null
    exit 0
}

trap cleanup INT TERM

# Define URLs
DASHBOARD_URL="http://localhost:8501"
API_URL="http://localhost:8000"
TEMPORAL_URL="http://localhost:8080"

# Monitor and auto-launch when ready
echo ""
echo "🔍 Waiting for services to be ready..."
echo "================================"

# Wait for all services to be ready
MAX_ATTEMPTS=30
ATTEMPT=0
ALL_READY=false

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    API_READY=false
    STREAMLIT_READY=false
    TEMPORAL_READY=false

    # Check each service
    if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        API_READY=true
    fi

    if curl -s http://localhost:8501 >/dev/null 2>&1; then
        STREAMLIT_READY=true
    fi

    if curl -s http://localhost:8080 >/dev/null 2>&1; then
        TEMPORAL_READY=true
    fi

    # Check if all services are ready
    if [ "$API_READY" = true ] && [ "$STREAMLIT_READY" = true ] && [ "$TEMPORAL_READY" = true ]; then
        ALL_READY=true
        echo ""
        echo "✅ All services are ready!"
        echo ""
        echo "🚀 Launching applications in browser..."

        # Launch Dashboard first (main UI)
        echo "   📊 Opening Dashboard: $DASHBOARD_URL"
        open_browser "$DASHBOARD_URL"
        sleep 2

        # Launch Temporal UI
        echo "   ⚙️  Opening Temporal UI: $TEMPORAL_URL"
        open_browser "$TEMPORAL_URL"
        sleep 1

        # Launch API Docs
        echo "   📚 Opening API Docs: $API_URL/docs"
        open_browser "$API_URL/docs"

        break
    else
        # Show status
        echo -n "⏳ Waiting for services... ["
        [ "$API_READY" = true ] && echo -n "API ✓" || echo -n "API ✗"
        echo -n " | "
        [ "$STREAMLIT_READY" = true ] && echo -n "Dashboard ✓" || echo -n "Dashboard ✗"
        echo -n " | "
        [ "$TEMPORAL_READY" = true ] && echo -n "Temporal ✓" || echo -n "Temporal ✗"
        echo "] (Attempt $((ATTEMPT+1))/$MAX_ATTEMPTS)"

        ATTEMPT=$((ATTEMPT + 1))
        sleep 5
    fi
done

if [ "$ALL_READY" = false ]; then
    echo ""
    echo "⚠️  Some services did not start within the expected time."
    echo "    Check the output above for errors."
    echo ""
    echo "    Manual URLs:"
    echo "    • Dashboard: $DASHBOARD_URL"
    echo "    • API Docs: $API_URL/docs"
    echo "    • Temporal UI: $TEMPORAL_URL"
fi

echo ""
echo "🎉 Application is running!"
echo ""

# Keep script running
wait