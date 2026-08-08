#!/bin/bash
# =============================================================================
# build-layer.sh — Package shared Python modules as a Lambda Layer
#
# Creates a zip file suitable for deployment as an AWS Lambda Layer, providing
# the shared modules in this directory to any Lambda function in this project.
#
# Usage:
#   bash shared/python/build-layer.sh
#   bash shared/python/build-layer.sh --list    # print contents, build nothing
#   # Output: shared/python/dist/fsxn-shared-python-layer.zip
#
# Deploy:
#   aws lambda publish-layer-version \
#     --layer-name fsxn-shared-python \
#     --zip-file fileb://shared/python/dist/fsxn-shared-python-layer.zip \
#     --compatible-runtimes python3.12 \
#     --description "FSx for ONTAP shared modules (ontap_response, auth_cache, etc.)"
#
# The contents are discovered from the directory rather than listed here. The
# previous version carried a hardcoded list of seven modules that had drifted
# from the thirteen actually present, so the layer shipped without
# ontap_audit_parser, vendor_shipper, ems_event or fpolicy_event -- modules the
# vendor handlers import. Those handlers import defensively and fall back to a
# reduced local parser, so the layer was quietly delivering the degraded path
# instead of failing. The list also still named idempotency.py, which had been
# removed; the copy loop skipped missing files without comment.
#
# Anything deliberately left out belongs in EXCLUDE below, with a reason.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_DIR="${DIST_DIR}/build"
LAYER_ZIP="${DIST_DIR}/fsxn-shared-python-layer.zip"

# Modules intentionally not shipped in the layer.
#   restore_verification.py  the Step Functions tasks in
#                            shared/templates/restore-verification.yaml carry
#                            their own inline code and do not attach this layer
EXCLUDE=(
  restore_verification.py
)

is_excluded() {
  local candidate="$1"
  local skip
  for skip in "${EXCLUDE[@]}"; do
    [[ "${candidate}" == "${skip}" ]] && return 0
  done
  return 1
}

# Discover modules from the directory, in a stable order.
MODULES=()
while IFS= read -r path; do
  name="$(basename "${path}")"
  if is_excluded "${name}"; then
    continue
  fi
  MODULES+=("${name}")
done < <(find "${SCRIPT_DIR}" -maxdepth 1 -name '*.py' | sort)

if [[ ${#MODULES[@]} -eq 0 ]]; then
  echo "ERROR: no Python modules found in ${SCRIPT_DIR}" >&2
  exit 1
fi

if [[ "${1:-}" == "--list" ]]; then
  echo "Layer would contain ${#MODULES[@]} module(s):"
  for mod in "${MODULES[@]}"; do
    echo "  python/${mod}"
  done
  if [[ ${#EXCLUDE[@]} -gt 0 ]]; then
    echo "Excluded:"
    for mod in "${EXCLUDE[@]}"; do
      echo "  ${mod}"
    done
  fi
  exit 0
fi

echo "=== Building Lambda Layer: fsxn-shared-python ==="

# Clean previous build
rm -rf "${BUILD_DIR}" "${LAYER_ZIP}"
mkdir -p "${BUILD_DIR}/python"

# Copy shared modules (Lambda Layer expects the python/ prefix)
for mod in "${MODULES[@]}"; do
  cp "${SCRIPT_DIR}/${mod}" "${BUILD_DIR}/python/${mod}"
  echo "  Added: python/${mod}"
done

# Create zip
cd "${BUILD_DIR}"
zip -q -r "${LAYER_ZIP}" python/ -x "python/__pycache__/*"
cd "${SCRIPT_DIR}"

# Verify every discovered module made it into the archive. A layer that is
# missing a module the handlers import does not fail loudly -- the handlers
# degrade -- so the check happens here instead.
#
# The listing is captured once rather than piped per module. Under `set -o
# pipefail`, `unzip -l | grep -q` reports failure for entries that are not last
# in the archive: grep exits at the first match, unzip takes SIGPIPE, and the
# pipeline's status comes from unzip. That produced a "missing" error for 11 of
# 12 modules that were all present.
ARCHIVE_LISTING="$(unzip -l "${LAYER_ZIP}")"
MISSING=0
for mod in "${MODULES[@]}"; do
  if ! printf '%s\n' "${ARCHIVE_LISTING}" | grep -qF "python/${mod}"; then
    echo "ERROR: python/${mod} is missing from the archive" >&2
    MISSING=1
  fi
done
if [[ "${MISSING}" -eq 1 ]]; then
  exit 1
fi

# Clean build dir
rm -rf "${BUILD_DIR}"

# Output info
ZIP_SIZE=$(du -h "${LAYER_ZIP}" | cut -f1)
echo ""
echo "=== Layer built successfully ==="
echo "  Output:  ${LAYER_ZIP}"
echo "  Size:    ${ZIP_SIZE}"
echo "  Modules: ${#MODULES[@]}"
echo ""
echo "Deploy with:"
echo "  aws lambda publish-layer-version \\"
echo "    --layer-name fsxn-shared-python \\"
echo "    --zip-file fileb://${LAYER_ZIP} \\"
echo "    --compatible-runtimes python3.12 \\"
echo '    --description "FSx for ONTAP shared modules (ontap_response, auth_cache, etc.)"'
