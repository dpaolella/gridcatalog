#!/usr/bin/env bash
# Fail a static build whose catalog page has outgrown being one file.
#
# Point this at the page that carries the catalog, which since the nav became
# four peers is `/datasets`, not `/`. The Hub landing page is a menu and will
# never grow; checking it would be a green tick that measures nothing, which is
# worse than no check because it reads like one.
#
# The static site has no server, so `StaticSearch` filters the whole catalog in
# the browser and the whole catalog ships inside `index.html`. That is the right
# trade at this size and it does not scale: measured at 66 records the page is
# 592 KB, about 9 KB per record, so a thousand records is a 9 MB page and ten
# thousand is unservable.
#
# The tradeoff was documented at the top of `StaticSearch.tsx` and nothing
# enforced it, which meant the limit lived in whoever remembered reading that
# comment. This is the enforcement: the build fails, on the run that crosses the
# line, with the fix written down — rather than a Pages deploy quietly getting
# slower until somebody on a phone gives up.
#
# The page, not the record count, because the page is the thing a reader waits
# for and a record's contribution varies by an order of magnitude with the
# length of its description.
set -euo pipefail

PAGE="${1:-web/out/datasets/index.html}"
LIMIT_KB="${STATIC_PAGE_LIMIT_KB:-2048}"

if [ ! -f "$PAGE" ]; then
  echo "no static page at $PAGE — did the export build run?" >&2
  exit 1
fi

SIZE_KB=$(( ($(wc -c < "$PAGE") + 1023) / 1024 ))
printf 'static catalog page: %s KB (limit %s KB)\n' "$SIZE_KB" "$LIMIT_KB"

if [ "$SIZE_KB" -gt "$LIMIT_KB" ]; then
  cat >&2 <<EOF

$PAGE is ${SIZE_KB} KB, over the ${LIMIT_KB} KB limit.

The static build ships every published record into the catalog page so that
search works with no server behind it. The catalog has outgrown that.

Three ways forward, roughly in order of how much they cost:

  * Serve the live API and point the site at it. Search stops being a copy of
    the catalog and becomes a query, which is what it is for.
  * Pre-render the list paginated, and load each page's records on demand.
  * Raise STATIC_PAGE_LIMIT_KB, if this size is genuinely acceptable for the
    readers this deployment has. Say why in the commit.

Do not fix this by trimming what each row shows: the row is what makes the
result useful, and the page will cross the line again as the corpus grows.
EOF
  exit 1
fi
