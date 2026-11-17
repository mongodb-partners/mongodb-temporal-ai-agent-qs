#!/bin/sh
# Renders the Temporal config template by substituting ${VAR}
# placeholders with environment values.
#
# Usage: render-config.sh <template> <output>
#
# Each value is escaped for sed's replacement side first: '&' (the
# matched text), '\' and the '|' delimiter would otherwise corrupt
# connection strings such as "...?retryWrites=true&w=majority" or
# passwords containing those characters. POSIX sh only: the runtime
# image is alpine/busybox.
set -eu

src=$1
dst=$2

escape() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

uri=$(escape "${TEMPORAL_MONGO_URI:?TEMPORAL_MONGO_URI is required}")
mode=$(escape "${TEMPORAL_VISIBILITY_MODE:-aggregation}")

sed \
    -e "s|\${TEMPORAL_MONGO_URI}|${uri}|g" \
    -e "s|\${TEMPORAL_VISIBILITY_MODE}|${mode}|g" \
    "$src" > "$dst"
