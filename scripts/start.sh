#!/bin/sh
set -e

# Detect Render cloud environment and enforce production defaults
if [ "$RENDER" = "true" ] || [ -n "$RENDER_SERVICE_ID" ]; then
    echo "==> Render deployment detected: enforcing production defaults..."
    export ENVIRONMENT="${ENVIRONMENT:-production}"
    export BOT_MODE="${BOT_MODE:-webhook}"
fi

echo "================================================================================"
echo " Starting EMYC Telegram Competitive Exam Platform"
echo "================================================================================"

DB_URL="${DATABASE_URL:-}"

# Check if DATABASE_URL is unset or pointing to localhost in a production/Render environment
if [ -z "$DB_URL" ] || echo "$DB_URL" | grep -q "localhost:5432" || echo "$DB_URL" | grep -q "127.0.0.1:5432"; then
    echo "================================================================================"
    echo "❌ CONFIGURATION ERROR: DATABASE_URL is missing or set to localhost!"
    echo ""
    echo "Current DATABASE_URL: '${DB_URL:-<UNSET>}'"
    echo ""
    echo "Render Docker containers cannot connect to 'localhost:5432' because PostgreSQL"
    echo "is not running inside this container."
    echo ""
    echo "HOW TO FIX THIS IN RENDER:"
    echo "  1. Open your Render Dashboard: https://dashboard.render.com"
    echo "  2. Click on your 'emyc' Web Service"
    echo "  3. Go to the 'Environment' tab in the left sidebar"
    echo "  4. Add the following Environment Variable:"
    echo "       Key:   DATABASE_URL"
    echo "       Value: postgresql://postgres:[YOUR-PASSWORD]@[YOUR-SUPABASE-HOST]:5432/postgres"
    echo ""
    echo "  (Also ensure TELEGRAM_BOT_TOKEN, WEBHOOK_URL, and WEBHOOK_SECRET are configured)"
    echo "================================================================================"
    exit 1
fi

echo "==> Running database migrations (alembic upgrade head)..."
alembic upgrade head || echo "⚠️ Alembic upgrade finished with non-zero exit code. Continuing to application startup (self-healing schema migrations will run in lifespan)..."
echo "==> Database migrations step completed."

echo "==> Starting Uvicorn server on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
