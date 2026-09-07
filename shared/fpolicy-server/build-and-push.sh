#!/bin/bash
set -euo pipefail

# FPolicy Server — Build and Push to ECR
#
# Builds a multi-platform manifest covering both compute modes of
# shared/templates/fpolicy-apigw.yaml. Each mode resolves the same tag to its own
# architecture:
#
#   ComputeType=fargate  RuntimePlatform pins CpuArchitecture: X86_64  linux/amd64
#   ComputeType=ec2      al2023 arm64 AMI, t4g.* instance types        linux/arm64
#
# This used to build linux/amd64 alone, which meant the EC2 mode could not run
# the only image this repository produces. Building one platform is still
# possible -- PLATFORMS=linux/amd64 ./build-and-push.sh -- but a single-platform
# image will fail on the mode it does not match:
#
#   Fargate, wrong platform: "CannotPullContainerError: image Manifest does not
#                             contain descriptor matching platform 'linux/amd64'"
#   EC2, wrong platform:     "exec /fpolicy-server: exec format error", visible in
#                            the FPolicyServerLogGroup container log stream
#
# Building on Apple Silicon does not change any of this: buildx cross-compiles
# whatever PLATFORMS asks for, and the host architecture is not consulted.
#
# Usage:
#   ./build-and-push.sh [tag]
#
# Examples:
#   ./build-and-push.sh                    # Uses 'latest' tag
#   ./build-and-push.sh v2-timeout-fix     # Uses specified tag
#   PLATFORMS=linux/arm64 ./build-and-push.sh   # Single platform, EC2 mode only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="${1:-latest}"

# Configuration — update these for your environment
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
ECR_REPO="fsxn-fpolicy-server"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
BUILDER="fsxn-fpolicy-builder"

echo "=== FPolicy Server Build & Push ==="
echo "  Region:    ${AWS_REGION}"
echo "  Account:   ${AWS_ACCOUNT_ID}"
echo "  Image:     ${ECR_URI}:${TAG}"
echo "  Platforms: ${PLATFORMS}"
echo ""

# Step 1: ECR Login
echo "🔐 Authenticating to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Step 2: Ensure a builder that can do multi-platform
#
# The default "docker" buildx driver builds one platform per invocation and
# rejects a comma-separated --platform with "Multi-platform build is not
# supported for the docker driver". The docker-container driver does support it,
# so create one and reuse it across runs.
echo "🧰 Preparing buildx builder '${BUILDER}'..."
if docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
  docker buildx use "${BUILDER}"
else
  docker buildx create --name "${BUILDER}" --driver docker-container --use
fi
docker buildx inspect --bootstrap >/dev/null

# Step 3: Build and push the manifest
echo "🔨 Building image for ${PLATFORMS}..."
docker buildx build \
  --platform "${PLATFORMS}" \
  -t "${ECR_URI}:${TAG}" \
  --push \
  "${SCRIPT_DIR}"

# Step 4: Confirm what actually landed in ECR
#
# A single-platform push succeeds just as quietly as a multi-platform one, and
# the mode that does not match only fails later, at task start or container run.
# Print the manifest so the mismatch is visible here instead.
echo ""
echo "📦 Platforms present in ${ECR_URI}:${TAG}:"
docker buildx imagetools inspect "${ECR_URI}:${TAG}" \
  --format '{{range .Manifest.Manifests}}{{if .Platform}}  {{.Platform.OS}}/{{.Platform.Architecture}}
{{end}}{{end}}' 2>/dev/null \
  || echo "  (could not read manifest; check with: docker buildx imagetools inspect ${ECR_URI}:${TAG})"

echo ""
echo "✅ Successfully built and pushed: ${ECR_URI}:${TAG}"
echo ""
echo "To pick up the new image — ComputeType=fargate:"
echo "  aws ecs update-service \\"
echo "    --cluster fsxn-fpolicy-server-cluster \\"
echo "    --service fsxn-fpolicy-server-service \\"
echo "    --force-new-deployment \\"
echo "    --region ${AWS_REGION}"
echo ""
echo "ComputeType=ec2 — redeploy the stack with the new tag:"
echo "  aws cloudformation deploy --stack-name <stack> \\"
echo "    --template-file shared/templates/fpolicy-apigw.yaml \\"
echo "    --parameter-overrides ComputeType=ec2 ContainerImage=${ECR_URI}:${TAG} ... \\"
echo "    --capabilities CAPABILITY_NAMED_IAM --region ${AWS_REGION}"
echo "  # The instance pulls the image once, in UserData at launch, and"
echo "  # --restart always restarts the existing container rather than re-pulling."
echo "  # A new ContainerImage changes UserData, which replaces the instance."
echo "  # The replacement gets a new private IP: re-register it on the ONTAP"
echo "  # FPolicy external engine from the Ec2PrivateIp stack output."
