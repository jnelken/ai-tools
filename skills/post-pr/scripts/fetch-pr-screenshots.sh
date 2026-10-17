#!/usr/bin/env bash
# Download the screenshots embedded in a PR description, one local file path per line on stdout.
#
# usage: fetch-pr-screenshots.sh <owner/repo> <pr_number> <out_dir>
#
# Screenshots are `https://github.com/user-attachments/assets/<uuid>` URLs (from <img src> or
# markdown image syntax). They 404 without auth on private repos; the gh token follows the redirect
# to the signed image URL. Prints nothing (exit 0) when the PR has no screenshots. Skips, with a
# note on stderr, any asset that isn't a real image or exceeds 10 MB. Caps at 4 images.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <owner/repo> <pr_number> <out_dir>" >&2
  exit 2
fi
repo="$1"; pr="$2"; out="$3"
mkdir -p "$out"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
gh pr view "$pr" --repo "$repo" --json body -q .body > "$body"

urls="$(grep -oE 'https://github\.com/user-attachments/assets/[0-9a-f-]+' "$body" | awk '!seen[$0]++' | head -4 || true)"
[ -n "$urls" ] || exit 0

token="$(gh auth token)"
n=0
while IFS= read -r url; do
  n=$((n + 1))
  tmp="$out/raw-$n"
  code="$(curl -sL -H "Authorization: Bearer $token" -o "$tmp" -w '%{http_code}' --max-filesize 10485760 "$url" || true)"
  if [ "$code" != "200" ]; then
    echo "skip $url: HTTP $code" >&2; rm -f "$tmp"; continue
  fi
  case "$(file -b --mime-type "$tmp")" in
    image/png)  ext=png ;;
    image/jpeg) ext=jpg ;;
    image/gif)  ext=gif ;;
    image/webp) ext=webp ;;
    *) echo "skip $url: not an image" >&2; rm -f "$tmp"; continue ;;
  esac
  mv "$tmp" "$out/screenshot-$n.$ext"
  echo "$out/screenshot-$n.$ext"
done <<<"$urls"
