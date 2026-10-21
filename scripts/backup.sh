#!/bin/sh
# Quiesce writers so PostgreSQL metadata and the file snapshot agree.
set -eu
umask 077
backup_dir=${1:-./backups/$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$backup_dir"
backup_dir=$(cd "$backup_dir" && pwd)
docker compose stop web api
trap 'docker compose start api web' EXIT INT TERM
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup_dir/database.sql"
docker compose run --rm --no-deps -v "$backup_dir:/backup" api python -c 'import tarfile; archive=tarfile.open("/backup/files.tar.gz","w:gz"); archive.add("/data",arcname="."); archive.close()'
printf 'Backup saved: %s\n' "$backup_dir"
