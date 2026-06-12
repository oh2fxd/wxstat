#!/bin/bash
set -e
cd "$(dirname "$0")"

# Set your remote URL here (create the repo on GitHub/GitLab first):
REMOTE="${WXSTAT_REMOTE:-}"
REPO_URL="https://github.com/YOU/wxstat.git"

if [ -z "$REMOTE" ]; then
    echo "First time: set your remote."
    echo "  export WXSTAT_REMOTE=git@github.com:YOU/wxstat.git"
    echo "  or edit push.sh and fill in REPO_URL"
    echo ""
    echo "If no remote is set, I'll commit locally only."
    read -p "Continue with local-only commit? [Y/n] " ans
    if [ "$ans" = "n" ]; then exit 0; fi
fi

# Stage & commit
git add -A
git status

TS=$(date '+%Y-%m-%d %H:%M')
if git diff --cached --quiet; then
    echo "Nothing to commit."
else
    COMMIT_MSG="${1:-auto-save $TS}"
    git commit -m "$COMMIT_MSG"
    echo "Committed: $COMMIT_MSG"
fi

# Push if remote configured
REMOTE_NAME=$(git remote 2>/dev/null | head -1)
if [ -n "$REMOTE_NAME" ]; then
    git push "$REMOTE_NAME" main 2>/dev/null || git push "$REMOTE_NAME" master
    echo "Pushed!"
elif [ -n "$REMOTE" ]; then
    git remote add origin "$REMOTE"
    git branch -M main
    git push -u origin main
    echo "Pushed to $REMOTE"
else
    echo "No remote configured — local commit done."
fi
