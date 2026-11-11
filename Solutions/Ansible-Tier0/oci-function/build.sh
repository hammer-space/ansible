#!/bin/bash
# Build and push the OCI Function image to OCIR (OCI Container Registry).
#
# Usage:
#   ./build.sh <region> <tenancy-namespace> [repo-name]
#
# Example:
#   ./build.sh us-sanjose-1 mytenancy tier0-functions
#
# Prerequisites:
#   - Docker or podman installed
#   - Logged in to OCIR: docker login <region>.ocir.io

set -euo pipefail

REGION="${1:?Usage: $0 <region> <tenancy-namespace> [repo-name]}"
NAMESPACE="${2:?Usage: $0 <region> <tenancy-namespace> [repo-name]}"
REPO="${3:-tier0-functions}"
IMAGE_NAME="tier0-auto-deploy"
TAG="latest"

FULL_IMAGE="${REGION}.ocir.io/${NAMESPACE}/${REPO}/${IMAGE_NAME}:${TAG}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Building OCI Function image..."
echo "  Image: ${FULL_IMAGE}"
echo "  Context: ${REPO_ROOT}"

# Build from repo root so Dockerfile can COPY oci_deploy.py and cloud-init/
docker build \
    -f "${SCRIPT_DIR}/Dockerfile" \
    -t "${FULL_IMAGE}" \
    "${REPO_ROOT}"

echo ""
echo "Pushing to OCIR..."
docker push "${FULL_IMAGE}"

echo ""
echo "Done. Image: ${FULL_IMAGE}"
echo ""
echo "Update your Terraform with:"
echo "  function_image = \"${FULL_IMAGE}\""
