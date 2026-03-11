#!/bin/bash
#
# Deploy New Instances Script
# Runs preflight check and optionally deploys to new instances only
#
# Usage:
#   ./deploy_new_instances.sh -i inventory.aws.yml    # AWS inventory
#   ./deploy_new_instances.sh -i inventory.gcp.yml    # GCP inventory
#   ./deploy_new_instances.sh -i inventory.oci.yml    # OCI inventory
#   ./deploy_new_instances.sh -i inventory.aws.yml --check   # Dry run
#   ./deploy_new_instances.sh -i inventory.aws.yml --auto    # No confirmation
#
# Options:
#   -i, --inventory FILE   Specify inventory file (REQUIRED)
#   -c, --check            Dry run mode (ansible --check)
#   -a, --auto             Auto-deploy without confirmation
#   -p, --precheck-only    Run precheck tags only (no full deployment)
#   -t, --tags TAGS        Additional ansible tags
#   -h, --help             Show this help message
#
# Prerequisites:
#   AWS:  pip3 install boto3 botocore && aws configure
#   GCP:  pip3 install google-auth requests && gcloud auth application-default login
#   OCI:  pip3 install oci && oci setup config
#

set -e

# Default values
INVENTORY=""
INVENTORY_FILE=""
CHECK_MODE=""
AUTO_MODE=false
PRECHECK_ONLY=false
EXTRA_TAGS=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

show_help() {
    echo "Usage: $0 -i INVENTORY_FILE [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -i, --inventory FILE   Specify inventory file (REQUIRED)"
    echo "  -c, --check            Dry run mode (ansible --check)"
    echo "  -a, --auto             Auto-deploy without confirmation"
    echo "  -p, --precheck-only    Run precheck tags only"
    echo "  -t, --tags TAGS        Additional ansible tags"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 -i inventory.aws.yml"
    echo "  $0 -i inventory.gcp.yml --check"
    echo "  $0 -i inventory.oci.yml --auto"
    echo ""
    echo "Prerequisites:"
    echo "  AWS:  pip3 install boto3 botocore && aws configure"
    echo "  GCP:  pip3 install google-auth requests && gcloud auth application-default login"
    echo "  OCI:  pip3 install oci && oci setup config"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--inventory)
            INVENTORY_FILE="$2"
            INVENTORY="-i $2"
            shift 2
            ;;
        -c|--check)
            CHECK_MODE="--check"
            shift
            ;;
        -a|--auto)
            AUTO_MODE=true
            shift
            ;;
        -p|--precheck-only)
            PRECHECK_ONLY=true
            shift
            ;;
        -t|--tags)
            EXTRA_TAGS="--tags $2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# Check required arguments
if [[ -z "$INVENTORY_FILE" ]]; then
    echo -e "${RED}Error: Inventory file is required${NC}"
    echo ""
    show_help
    exit 1
fi

# Check inventory file exists
if [[ ! -f "$SCRIPT_DIR/$INVENTORY_FILE" ]] && [[ ! -f "$INVENTORY_FILE" ]]; then
    echo -e "${RED}Error: Inventory file not found: $INVENTORY_FILE${NC}"
    echo ""
    echo "Available inventory files:"
    ls -1 "$SCRIPT_DIR"/inventory*.yml 2>/dev/null | xargs -n1 basename
    exit 1
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Hammerspace Tier 0 - Deploy New Instances${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Step 1: Run preflight check
echo -e "${YELLOW}Step 1: Running preflight check...${NC}"
cd "$SCRIPT_DIR"
ansible-playbook preflight_check.yml $INVENTORY

# Read the limit file
LIMIT_FILE="$SCRIPT_DIR/.new_instances_limit"
if [[ -f "$LIMIT_FILE" ]]; then
    NEW_INSTANCES=$(cat "$LIMIT_FILE")
else
    echo -e "${RED}Error: Limit file not found at $LIMIT_FILE${NC}"
    exit 1
fi

# Check if there are new instances
if [[ -z "$NEW_INSTANCES" ]]; then
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  No new instances to deploy!${NC}"
    echo -e "${GREEN}  All inventory hosts are already registered.${NC}"
    echo -e "${GREEN}============================================${NC}"
    exit 0
fi

# Count new instances
INSTANCE_COUNT=$(echo "$NEW_INSTANCES" | tr ',' '\n' | wc -l | tr -d ' ')

echo ""
echo -e "${YELLOW}============================================${NC}"
echo -e "${YELLOW}  Found $INSTANCE_COUNT new instance(s) to deploy:${NC}"
echo -e "${YELLOW}============================================${NC}"
echo "$NEW_INSTANCES" | tr ',' '\n' | while read -r instance; do
    echo "  - $instance"
done
echo ""

# Step 2: Confirm deployment
if [[ "$AUTO_MODE" == false ]]; then
    echo -e "${YELLOW}Do you want to proceed with deployment?${NC}"
    echo "  1) Yes, deploy to new instances"
    echo "  2) Yes, but dry run first (--check)"
    echo "  3) Run precheck only (--tags precheck)"
    echo "  4) No, exit"
    echo ""
    read -p "Select option [1-4]: " choice

    case $choice in
        1)
            CHECK_MODE=""
            ;;
        2)
            CHECK_MODE="--check"
            ;;
        3)
            PRECHECK_ONLY=true
            ;;
        4)
            echo "Exiting."
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid option${NC}"
            exit 1
            ;;
    esac
fi

# Step 3: Run deployment
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Starting deployment...${NC}"
echo -e "${BLUE}============================================${NC}"

# Build ansible command
ANSIBLE_CMD="ansible-playbook site.yml $INVENTORY --limit \"$NEW_INSTANCES\""

if [[ "$PRECHECK_ONLY" == true ]]; then
    ANSIBLE_CMD="$ANSIBLE_CMD --tags precheck"
elif [[ -n "$EXTRA_TAGS" ]]; then
    ANSIBLE_CMD="$ANSIBLE_CMD $EXTRA_TAGS"
fi

if [[ -n "$CHECK_MODE" ]]; then
    ANSIBLE_CMD="$ANSIBLE_CMD $CHECK_MODE"
fi

echo "Running: $ANSIBLE_CMD"
echo ""

# Execute
eval $ANSIBLE_CMD
RESULT=$?

# Step 4: Summary
echo ""
if [[ $RESULT -eq 0 ]]; then
    echo -e "${GREEN}============================================${NC}"
    if [[ -n "$CHECK_MODE" ]]; then
        echo -e "${GREEN}  Dry run completed successfully!${NC}"
        echo -e "${GREEN}  To deploy for real, run without --check${NC}"
    elif [[ "$PRECHECK_ONLY" == true ]]; then
        echo -e "${GREEN}  Precheck completed successfully!${NC}"
        echo -e "${GREEN}  To run full deployment:${NC}"
        echo -e "${GREEN}    ./deploy_new_instances.sh${NC}"
    else
        echo -e "${GREEN}  Deployment completed successfully!${NC}"
    fi
    echo -e "${GREEN}============================================${NC}"
else
    echo -e "${RED}============================================${NC}"
    echo -e "${RED}  Deployment failed with exit code: $RESULT${NC}"
    echo -e "${RED}============================================${NC}"
    exit $RESULT
fi
