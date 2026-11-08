#!/usr/bin/env sh
# AI DEV DIRECTOR companion launcher for macOS / Linux (unverified; the Windows launcher is start-companion.ps1).
# Starts the 8081 console in the background and records its PID in data/companion.pid.
set -e
cd "$(dirname "$0")"
mkdir -p data
if [ -f data/companion.pid ] && kill -0 "$(cat data/companion.pid)" 2>/dev/null; then
  echo "AI DEV companion already running (PID $(cat data/companion.pid))"
  exit 0
fi
# 저장소의 .venv 가 있으면 그것을 쓴다 — 시스템 python3(3.9) 에는 pydantic 이 없다
if [ -z "${PYTHON:-}" ] && [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi
PY="${PYTHON:-python3}"
nohup "$PY" companion_server.py >data/companion.log 2>data/companion.err &
echo $! >data/companion.pid
echo "AI DEV companion started (PID $!) — 주소는 data/companion.log 첫 줄"
