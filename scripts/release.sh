#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <version>" >&2
  echo "Example: $0 v2.1.1" >&2
  exit 1
fi

VERSION="$1"

if [[ ! "$VERSION" =~ ^v ]]; then
  echo "Error: version must start with 'v' (got '$VERSION')" >&2
  exit 1
fi

git tag -a "$VERSION" -m "$VERSION"
git push origin "$VERSION"
gh release create "$VERSION" --generate-notes
