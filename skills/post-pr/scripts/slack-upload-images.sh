#!/usr/bin/env bash
# Upload one or more images to a Slack channel as a single message, posted as the user
# (the Keychain xoxp token, same one /post-pr step 4 uses for #pr-review).
#
# usage: slack-upload-images.sh <channel_id> <caption_file> <image>...
#
# The token needs the `files:write` user scope; without it Slack answers `missing_scope` and this
# exits nonzero with that error on stderr. Slack's external-upload flow is three calls per file
# (getUploadURLExternal -> PUT bytes -> completeUploadExternal), so it lives here rather than
# inline in the skill. Caption is read from a file because PR titles carry backticks, quotes and `$`.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <channel_id> <caption_file> <image>..." >&2
  exit 2
fi
channel="$1"; caption_file="$2"; shift 2

TOKEN="$(security find-generic-password -s slack-pr-review-user-token -a "$USER" -w)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

files_json='[]'
for img in "$@"; do
  name="$(basename "$img")"
  size="$(wc -c < "$img" | tr -d ' ')"

  curl -s -X POST https://slack.com/api/files.getUploadURLExternal \
    -H "Authorization: Bearer $TOKEN" \
    --data-urlencode "filename=$name" --data-urlencode "length=$size" > "$work/get.json"
  if [ "$(jq -r .ok "$work/get.json")" != "true" ]; then
    echo "files.getUploadURLExternal failed: $(jq -r '.error // "unknown"' "$work/get.json")" >&2
    exit 1
  fi
  upload_url="$(jq -r .upload_url "$work/get.json")"
  file_id="$(jq -r .file_id "$work/get.json")"

  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$upload_url" --data-binary "@$img")"
  if [ "$code" != "200" ]; then
    echo "upload of $name failed: HTTP $code" >&2
    exit 1
  fi

  files_json="$(jq -c --arg id "$file_id" --arg title "$name" '. + [{id: $id, title: $title}]' <<<"$files_json")"
done

jq -n --arg ch "$channel" --argjson files "$files_json" --rawfile cap "$caption_file" \
  '{channel_id: $ch, files: $files, initial_comment: $cap}' > "$work/complete.json"

curl -s -X POST https://slack.com/api/files.completeUploadExternal \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-type: application/json; charset=utf-8' \
  --data @"$work/complete.json" > "$work/done.json"
if [ "$(jq -r .ok "$work/done.json")" != "true" ]; then
  echo "files.completeUploadExternal failed: $(jq -r '.error // "unknown"' "$work/done.json")" >&2
  exit 1
fi
echo "ok: uploaded $# image(s) to $channel"
