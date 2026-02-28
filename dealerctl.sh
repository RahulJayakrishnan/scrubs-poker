#!/usr/bin/env bash
set -euo pipefail

SERVICE=poker-autodealer.service

case "${1:-status}" in
  start)
    systemctl --user start "$SERVICE"
    ;;
  stop)
    systemctl --user stop "$SERVICE"
    ;;
  restart)
    systemctl --user restart "$SERVICE"
    ;;
  enable)
    systemctl --user enable --now "$SERVICE"
    ;;
  disable)
    systemctl --user disable --now "$SERVICE"
    ;;
  status)
    systemctl --user --no-pager --full status "$SERVICE"
    ;;
  logs)
    journalctl --user -u "$SERVICE" -f
    ;;
  tail)
    journalctl --user -u "$SERVICE" -n "${2:-80}" --no-pager
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|enable|disable|status|logs|tail [n]}" >&2
    exit 1
    ;;
esac
