#!/bin/bash
# Export a tracked snapshot without copying refs, remotes, objects or reflogs.
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo "Usage: bash bench/taskkit/export_source.sh SOURCE_REPO COMMIT NEW_DESTINATION" >&2
  exit 2
fi
source_repo="$(realpath "$1")"
commit="$(git -C "$source_repo" rev-parse --verify "${2}^{commit}")"
destination="$(realpath -m "$3")"
if [ -e "$destination" ] || [ -L "$destination" ]; then
  echo "Destination must not exist: $destination" >&2
  exit 2
fi
mkdir -- "$destination"
git -C "$source_repo" archive "$commit" | tar -x -C "$destination"
if [ -e "$destination/.git" ] || [ -L "$destination/.git" ]; then
  echo "Unexpected .git in exported snapshot" >&2
  exit 1
fi
printf 'Exported %s to %s without git history\n' "$commit" "$destination"
