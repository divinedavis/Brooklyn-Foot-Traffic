#!/bin/bash
set -e
cd /home/foottraffic

# Refresh data from NYC Open Data
curl -s http://127.0.0.1:8082/api/refresh > /dev/null

# Push any changes to GitHub
git config user.email divinejdavis@gmail.com
git config user.name Divine Davis

git add -A
if git diff --cached --quiet; then
    echo Wed Mar 11 09:21:07 EDT 2026: No changes to commit >> /home/foottraffic/autopush.log
else
    git commit -m Data refresh: 2026-03-11
    git push origin main
    echo Wed Mar 11 09:21:07 EDT 2026: Pushed successfully >> /home/foottraffic/autopush.log
fi
