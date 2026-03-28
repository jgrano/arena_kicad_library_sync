#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_DIR/docker"

echo "=== Arena KiCad Library Sync — Server Setup ==="
echo ""

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "Docker not found. Please install Docker first:"
    echo "  https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "docker-compose not found. Please install Docker Compose."
    exit 1
fi

cd "$DOCKER_DIR"

# Create .env if not exists
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env

    read -rp "Arena Email: " arena_email
    read -rsp "Arena Password: " arena_password
    echo ""
    read -rp "Arena Workspace ID: " arena_workspace
    read -rsp "Server API Key (leave blank to disable auth): " api_key
    echo ""

    sed -i.bak "s/^ARENA_EMAIL=.*/ARENA_EMAIL=$arena_email/" .env
    sed -i.bak "s/^ARENA_PASSWORD=.*/ARENA_PASSWORD=$arena_password/" .env
    sed -i.bak "s/^ARENA_WORKSPACE_ID=.*/ARENA_WORKSPACE_ID=$arena_workspace/" .env
    sed -i.bak "s/^SERVER_API_KEY=.*/SERVER_API_KEY=$api_key/" .env
    rm -f .env.bak
fi

# Create data directory
mkdir -p data config

# Start services
echo ""
echo "Starting services..."
if docker compose version &>/dev/null 2>&1; then
    docker compose up -d --build
else
    docker-compose up -d --build
fi

# Wait and verify
echo "Waiting for server to start..."
sleep 5

if curl -sf http://localhost:8765/health >/dev/null 2>&1; then
    echo ""
    echo "=== Server is running! ==="
    echo ""
    echo "Health check: http://localhost:8765/health"
    echo "HTTP lib API: http://localhost:8765/v1/categories"
    echo "Sync status:  http://localhost:8765/v1/sync/status"
    echo ""
    echo "To use with KiCad HTTP lib, add this to your .kicad_httplib file:"
    echo '  {"meta":{"version":1},"name":"Arena PLM","source":{"type":"REST_API","api_version":"v1","root_url":"http://localhost:8765"}}'
else
    echo "WARNING: Server may not be running. Check logs:"
    echo "  docker logs arena-kicad-library-sync"
fi
