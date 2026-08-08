#!/bin/bash
# Check bilingual documentation sync between ja/ and en/ directories.
# Reports files that exist in one language but not the other,
# and files where the heading structure differs significantly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

# Markdown headings, excluding anything inside a fenced code block. A `#` at the
# start of a line inside a bash block is a shell comment, not a heading.
count_headings() {
  awk '
    /^[[:space:]]*```/ { fence = !fence; next }
    !fence && /^#{1,6}[[:space:]]/ { n++ }
    END { print n + 0 }
  ' "$1"
}

count_fences() {
  awk '/^[[:space:]]*```/ { n++ } END { print n + 0 }' "$1"
}

echo "=== Bilingual Documentation Sync Check ==="
echo ""

# Check docs/ja vs docs/en
check_directory_pair() {
  local ja_dir="$1"
  local en_dir="$2"
  local label="$3"

  if [ ! -d "$ja_dir" ] && [ ! -d "$en_dir" ]; then
    return
  fi

  echo "--- ${label} ---"

  # Files in ja/ but not in en/
  if [ -d "$ja_dir" ]; then
    for ja_file in "$ja_dir"/*.md; do
      [ -f "$ja_file" ] || continue
      local basename
      basename=$(basename "$ja_file")
      if [ ! -f "$en_dir/$basename" ]; then
        echo -e "  ${RED}MISSING EN${NC}: $en_dir/$basename (exists in ja/)"
        ERRORS=$((ERRORS + 1))
      fi
    done
  fi

  # Files in en/ but not in ja/
  if [ -d "$en_dir" ]; then
    for en_file in "$en_dir"/*.md; do
      [ -f "$en_file" ] || continue
      local basename
      basename=$(basename "$en_file")
      if [ ! -f "$ja_dir/$basename" ]; then
        echo -e "  ${YELLOW}MISSING JA${NC}: $ja_dir/$basename (exists in en/)"
        WARNINGS=$((WARNINGS + 1))
      fi
    done
  fi

  # Heading structure comparison for files that exist in both.
  #
  # Headings are counted outside fenced code blocks only, and any difference is
  # reported. The previous version ran `grep -c "^#"` with a tolerance of 3, which
  # went wrong in both directions:
  #
  #   false positives  `# comment` lines inside bash blocks counted as headings.
  #                    demo-automated-response.md was reported as 60 vs 70 while
  #                    its heading structures were identical.
  #   missed gaps      those same comment lines could balance a real difference.
  #                    verification-results-datadog.md read as 46 vs 46 while the
  #                    English copy had 17 headings the Japanese one did not, and
  #                    the tolerance of 3 hid genuine 1-to-3 heading gaps in five
  #                    more files.
  #
  # Counting fences also surfaces unclosed code blocks: an odd fence count means
  # everything after the last fence renders as code, which is reported separately
  # because it is a rendering defect rather than a translation gap.
  if [ -d "$ja_dir" ] && [ -d "$en_dir" ]; then
    for ja_file in "$ja_dir"/*.md; do
      [ -f "$ja_file" ] || continue
      local basename
      basename=$(basename "$ja_file")
      local en_file="$en_dir/$basename"
      [ -f "$en_file" ] || continue

      local ja_headings en_headings ja_fences en_fences
      ja_headings=$(count_headings "$ja_file")
      en_headings=$(count_headings "$en_file")
      ja_fences=$(count_fences "$ja_file")
      en_fences=$(count_fences "$en_file")

      if [ $((ja_fences % 2)) -ne 0 ]; then
        echo -e "  ${RED}UNCLOSED FENCE${NC}: $ja_dir/$basename (${ja_fences} fences; everything after the last one renders as code)"
        ERRORS=$((ERRORS + 1))
      fi
      if [ $((en_fences % 2)) -ne 0 ]; then
        echo -e "  ${RED}UNCLOSED FENCE${NC}: $en_dir/$basename (${en_fences} fences; everything after the last one renders as code)"
        ERRORS=$((ERRORS + 1))
      fi

      if [ "$ja_headings" -ne "$en_headings" ]; then
        echo -e "  ${YELLOW}STRUCTURE DIFF${NC}: $basename (ja: ${ja_headings} headings, en: ${en_headings} headings)"
        WARNINGS=$((WARNINGS + 1))
      fi
    done
  fi

  echo ""
}

# Check main docs directory
check_directory_pair \
  "$PROJECT_ROOT/docs/ja" \
  "$PROJECT_ROOT/docs/en" \
  "docs/"

# Check each vendor's docs
for vendor_dir in "$PROJECT_ROOT"/integrations/*/; do
  [ -d "$vendor_dir" ] || continue
  local_vendor=$(basename "$vendor_dir")
  check_directory_pair \
    "$vendor_dir/docs/ja" \
    "$vendor_dir/docs/en" \
    "integrations/${local_vendor}/docs/"
done

# Summary
echo "=== Summary ==="
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
  echo -e "${GREEN}All bilingual docs are in sync.${NC}"
  exit 0
elif [ $ERRORS -eq 0 ]; then
  echo -e "${YELLOW}${WARNINGS} warning(s) found (missing ja/ files or structure differences).${NC}"
  exit 0
else
  echo -e "${RED}${ERRORS} error(s) found (missing en/ files).${NC}"
  echo -e "${YELLOW}${WARNINGS} warning(s) found.${NC}"
  echo ""
  echo "Japanese is the primary language. Missing English files should be created."
  exit 1
fi
