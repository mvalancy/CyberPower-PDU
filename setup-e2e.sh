#!/usr/bin/env bash
# ------------------------------------------------------------------
# setup-e2e.sh — Install Node.js + Playwright for E2E / screenshot tests
#
# This is separate from the main bridge (Python) dependencies.
# Run once:  sudo ./setup-e2e.sh
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== CyberPDU E2E Test Setup ==="
echo ""

# 1. Install Node.js 20.x LTS via NodeSource (needs root)
if command -v node &>/dev/null; then
  echo "[ok] Node.js already installed: $(node --version)"
else
  echo "[+] Installing Node.js 20.x LTS..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
  echo "[ok] Node.js installed: $(node --version)"
fi

# 2. Install Playwright system dependencies (needs root)
echo "[+] Installing Playwright system dependencies..."
npx --yes playwright install-deps chromium

# 3. Install npm packages (as the calling user, not root)
echo "[+] Installing npm packages..."
REAL_USER="${SUDO_USER:-$(whoami)}"
su "$REAL_USER" -c "cd '$SCRIPT_DIR/tests/e2e' && npm install"

# 4. Download Playwright Chromium browser (as the calling user)
echo "[+] Downloading Playwright Chromium..."
su "$REAL_USER" -c "cd '$SCRIPT_DIR/tests/e2e' && npx playwright install chromium"

echo ""
echo "=== E2E setup complete ==="
echo "Run screenshots:  cd tests/e2e && npx playwright test capture-screenshots.spec.ts"
echo "Run all E2E:      cd tests/e2e && npx playwright test"
