#!/usr/bin/env bash
set -euo pipefail

echo "=== AI Research Agent Setup ==="

# 1. Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv .venv
fi

# 2. Activate
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null

# 3. Install dependencies
echo "[*] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Build Docker image
echo "[*] Building Docker sandbox image..."
docker build -t ai_sandbox_image .

# 5. Create container if it doesn't exist
if ! docker inspect ai_sandbox &>/dev/null; then
    echo "[*] Creating ai_sandbox container..."
    docker run -d --name ai_sandbox \
        -v "$(pwd)/workspace:/workspace" \
        ai_sandbox_image
else
    echo "[*] ai_sandbox container already exists. Starting if stopped..."
    docker start ai_sandbox 2>/dev/null || true
fi

echo ""
echo "=== Setup complete! ==="
echo "Run: python web_app.py"
