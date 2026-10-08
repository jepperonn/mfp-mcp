#!/bin/sh
# Fly mounts the volume as root; fix ownership, then drop privileges for the server.
set -e
mkdir -p "$DATA_DIR"
chown -R mfp:mfp "$DATA_DIR"
exec setpriv --reuid=mfp --regid=mfp --init-groups "$@"
