#!/usr/bin/env bash
# Render the output of `make demo` into docs/demo.gif.
#
# This is a rendering of real output, not a screen capture: every frame is the
# text `make demo` actually printed, revealed a line at a time. It exists because
# a screen recorder is a heavier dependency than the thing being demonstrated, and
# because the raw text is worth committing next to the picture.
#
# Requires ImageMagick (`convert`). Regenerate with: make demo-gif
# A true terminal cast, if you prefer one:
#   asciinema rec demo.cast -c "make demo" && agg demo.cast docs/demo.gif
set -euo pipefail

cd "$(dirname "$0")/.."
out_dir="docs"
raw="$out_dir/demo-output.txt"
canvas="960x560"
lines=30
line_delay=16

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Capture, then normalise the absolute workspace path so the artifact is portable
# and does not publish the machine it was recorded on.
PYTHONPATH=src python3 -m budgetloop.cli demo > "$tmp/original.txt" 2>&1
sed "s|$(pwd)|<repo>|g" "$tmp/original.txt" > "$raw"
head -n "$lines" "$raw" > "$tmp/trimmed.txt"

index=0
while IFS= read -r _; do
  index=$((index + 1))
  head -n "$index" "$tmp/trimmed.txt" > "$tmp/frame.txt"
  # Pass the text as an argument (this ImageMagick has no `label:@file`), and
  # escape % because ImageMagick treats it as a format directive.
  frame_text="$(sed 's/%/%%/g' "$tmp/frame.txt")"
  convert -background '#0d1117' -fill '#d0d7de' -font DejaVu-Sans-Mono -pointsize 13 \
    -size "$canvas" -gravity northwest label:"$frame_text" \
    "$tmp/frame-$(printf '%03d' "$index").png"
done < "$tmp/trimmed.txt"

convert -delay "$line_delay" -loop 0 "$tmp"/frame-*.png -layers Optimize "$out_dir/demo.gif"
echo "wrote $out_dir/demo.gif ($(du -h "$out_dir/demo.gif" | cut -f1)) and $raw"
