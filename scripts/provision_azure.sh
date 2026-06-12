#!/usr/bin/env bash
# Provision the competition Windows VPS on Azure (UK South = London).
# Prereqs: az CLI installed, `az login` done, free-trial subscription active.
#
#   bash scripts/provision_azure.sh            # create everything
#   bash scripts/provision_azure.sh ip         # re-allow RDP from current IP
#   bash scripts/provision_azure.sh destroy    # delete it all after Jun 27
set -euo pipefail

# uksouth/ukwest/northeurope have no free-trial VM capacity (checked 2026-06-12);
# westeurope D-v6 sizes are available. v6 requires the NVMe-capable Azure Edition image.
RG="mt5-comp"
LOC="westeurope"
VM="mt5-vps"
SIZE="Standard_D2als_v6"     # 2 vCPU AMD / 4 GB — ~\$0.08/hr against trial credit
IMAGE="MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition:latest"
ADMIN_USER="shikang"

myip() { curl -s ifconfig.me; }

case "${1:-create}" in
create)
    # Azure password rules: 12-123 chars, 3 of: lower/upper/digit/symbol
    read -rs -p "Choose Windows admin password: " ADMIN_PASS; echo

    az group create -n "$RG" -l "$LOC" -o none
    az vm create \
        -g "$RG" -n "$VM" \
        --image "$IMAGE" \
        --size "$SIZE" \
        --admin-username "$ADMIN_USER" \
        --admin-password "$ADMIN_PASS" \
        --public-ip-sku Standard \
        --nsg-rule NONE \
        -o none
    # RDP reachable only from this machine's current public IP
    az network nsg rule create \
        -g "$RG" --nsg-name "${VM}NSG" -n AllowRDPFromMe \
        --priority 1000 --direction Inbound --access Allow --protocol Tcp \
        --source-address-prefixes "$(myip)/32" \
        --destination-port-ranges 3389 \
        -o none

    IP=$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)
    echo
    echo "VPS ready:"
    echo "  RDP host: $IP"
    echo "  User:     $ADMIN_USER"
    echo "Connect with the 'Windows App' on macOS. If your local IP changes:"
    echo "  bash scripts/provision_azure.sh ip"
    ;;
ip)
    az network nsg rule update \
        -g "$RG" --nsg-name "${VM}NSG" -n AllowRDPFromMe \
        --source-address-prefixes "$(myip)/32" -o none
    az network nsg rule update \
        -g "$RG" --nsg-name "${VM}NSG" -n AllowSSHFromMe \
        --source-address-prefixes "$(myip)/32" -o none 2>/dev/null || true
    echo "RDP+SSH now allowed from $(myip)"
    ;;
destroy)
    echo "This deletes the VM, disk and all data in resource group '$RG'."
    read -rp "Type '$RG' to confirm: " CONFIRM
    [ "$CONFIRM" = "$RG" ] && az group delete -n "$RG" --yes --no-wait \
        && echo "Deletion started." || echo "Aborted."
    ;;
*)
    echo "usage: $0 [create|ip|destroy]"; exit 1 ;;
esac
