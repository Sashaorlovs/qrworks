#!/bin/bash
BACKUP_DIR="/opt/qr_simple/backups"
DB_FILE="/opt/qr_simple/db.sqlite3"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$DB_FILE" "$BACKUP_DIR/db_$TIMESTAMP.sqlite3"

# Удаляем бэкапы старше 7 дней
find "$BACKUP_DIR" -name "db_*.sqlite3" -mtime +7 -delete
find /opt/qr_simple/backups/ -name "db_*.sqlite3" -mtime +14 -delete
