#!/bin/bash
# deploy.sh — push to GitHub and stream Railway logs
# Usage: ./deploy.sh "commit message"

set -e

MSG="${1:-update}"

echo "── Pushing to GitHub ─────────────────────────────"
git add -A
git commit -m "$MSG" 2>/dev/null || echo "(nothing new to commit)"
git push

echo ""
echo "── Waiting for Railway to pick up deploy (~5s) ───"
sleep 5

echo ""
echo "── Streaming Railway logs (Ctrl+C to stop) ───────"
railway logs --build -n 50
echo ""
railway logs -n 100
