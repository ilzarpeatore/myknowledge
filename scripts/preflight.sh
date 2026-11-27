#!/usr/bin/env bash
# Read-only inventory of a server before installing Knowledge OS.
# It changes nothing: it only reads system information and writes a report.
#   Usage: ./scripts/preflight.sh [domain]   -> report in ./preflight-<date>.txt
set -uo pipefail

DOMAIN="${1:-}"
PORT="${KOS_HTTP_PORT:-3000}"
REPORT="preflight-$(date +%Y%m%d-%H%M%S).txt"

section() { printf '\n===== %s =====\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 || printf '(exit %s)\n' "$?"; }
have() { command -v "$1" >/dev/null 2>&1; }

{
  section "System"
  run uname -a
  [ -r /etc/os-release ] && run cat /etc/os-release
  run nproc
  run free -h
  run df -h /
  section "CPU features (int8 embedding model; AVX2 required, AVX-512 VNNI optional)"
  grep -o -m1 -wE 'avx2|avx512f|avx512_vnni' /proc/cpuinfo | sort -u || echo "none of avx2/avx512 found"

  section "Docker"
  if have docker; then
    run docker version --format '{{.Server.Version}}'
    run docker compose version
    run docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
    run docker network ls
    run docker volume ls
    run docker system df
  else
    echo "docker not installed"
  fi

  section "Listening ports (is ${PORT} free?)"
  if have ss; then run ss -ltnp; else run netstat -ltnp; fi
  if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]${PORT}\b"; then
    echo "WARNING: port ${PORT} is in use. Set KOS_HTTP_PORT to a free port in .env."
  else
    echo "port ${PORT} is free"
  fi

  section "Caddy"
  if have caddy; then run caddy version; fi
  if have systemctl; then systemctl is-active caddy 2>/dev/null && run systemctl status caddy --no-pager -n 0; fi
  for f in /etc/caddy/Caddyfile /etc/caddy/conf.d /etc/caddy/sites-enabled; do
    if [ -e "$f" ]; then
      run ls -la "$f"
      # Site addresses only (no other configuration is printed).
      if [ -f "$f" ]; then
        echo "-- site blocks in $f:"
        grep -nE '^[^#[:space:]].*\{\s*$' "$f" || true
      fi
    fi
  done
  if have docker; then docker ps --format '{{.Names}} {{.Image}}' | grep -i caddy || true; fi

  section "Firewall"
  if have ufw; then run ufw status; else echo "ufw not installed"; fi

  if [ -n "$DOMAIN" ]; then
    section "DNS for ${DOMAIN}"
    if have dig; then run dig +short A "$DOMAIN"; run dig +short AAAA "$DOMAIN";
    else run getent hosts "$DOMAIN"; fi
    echo "-- this server's public addresses:"
    run hostname -I
  fi

  section "Done"
  echo "Nothing was changed on this server."
} >"$REPORT" 2>&1

echo "Report written to $REPORT"
