#!/bin/bash
set -e
cd /home/foottraffic

# Refresh data from NYC Open Data
curl -s http://127.0.0.1:8082/api/refresh > /dev/null

# Push any changes to GitHub
git config user.email "${GIT_USER_EMAIL}"
git config user.name "Divine Davis"

git add -A
if git diff --cached --quiet; then
    echo "$(date): No changes to commit" >> /home/foottraffic/autopush.log
else
    git commit -m "Data refresh: $(date '+%Y-%m-%d')"
    git push origin main
    echo "$(date): Pushed successfully" >> /home/foottraffic/autopush.log
fi
