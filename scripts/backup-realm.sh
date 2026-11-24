#!/bin/sh
# Back up the stateful realm volumes.
#   scripts/backup-realm.sh [dest-dir]      (default: ./backups)
#
# pvpgn-var holds every account and character; d2gs-work holds d2vp.ini,
# logs and stash pools. Both are needed for a faithful restore.
set -eu

DEST="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"

volume_name() {
  # Compose prefixes volumes with the project name.
  docker volume ls --format '{{.Name}}' | grep -E "(^|_)${1}$" | head -1
}

for v in pvpgn-var d2gs-work pvpgn-etc; do
  vol="$(volume_name "$v")"
  if [ -z "$vol" ]; then
    echo "skip: volume for '$v' not found" >&2
    continue
  fi
  out="$DEST/${v}-${STAMP}.tar.gz"
  echo "backing up $vol -> $out"
  docker run --rm -v "$vol":/data:ro -v "$(cd "$DEST" && pwd)":/backup alpine \
    tar czf "/backup/$(basename "$out")" -C /data .
done

echo "done: $DEST"
