#!/usr/bin/env bash
# Enable SSH access to the Azure Windows VPS from this Mac.
#   bash scripts/setup_ssh_azure.sh          # one-time: install OpenSSH on VM,
#                                            # push your public key, open port 22,
#                                            # add 'mt5-vps' to ~/.ssh/config
# After: ssh mt5-vps
set -euo pipefail

RG="mt5-comp"
VM="mt5-vps"
ADMIN_USER="shikang"
PUBKEY_FILE="$HOME/.ssh/id_ed25519.pub"

IP=$(az vm show -d -g "$RG" -n "$VM" --query publicIps -o tsv)
PUBKEY=$(cat "$PUBKEY_FILE")

echo "==> Installing OpenSSH Server on the VM (takes ~2 min)..."
az vm run-command invoke -g "$RG" -n "$VM" \
    --command-id RunPowerShellScript \
    --scripts "
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
# PowerShell as the default SSH shell
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell ``
  -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' ``
  -PropertyType String -Force | Out-Null
# Authorized key (admin accounts use the ProgramData file; ACL must be tight)
\$f = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path \$f -Value '$PUBKEY'
icacls \$f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
Restart-Service sshd
Write-Output 'OpenSSH ready'
" --query "value[0].message" -o tsv

echo "==> Opening port 22 in the NSG (your current IP only)..."
MYIP=$(curl -s ifconfig.me)
az network nsg rule create \
    -g "$RG" --nsg-name "${VM}NSG" -n AllowSSHFromMe \
    --priority 1010 --direction Inbound --access Allow --protocol Tcp \
    --source-address-prefixes "$MYIP/32" \
    --destination-port-ranges 22 \
    -o none 2>/dev/null || \
az network nsg rule update \
    -g "$RG" --nsg-name "${VM}NSG" -n AllowSSHFromMe \
    --source-address-prefixes "$MYIP/32" -o none

echo "==> Adding 'mt5-vps' host alias to ~/.ssh/config..."
if ! grep -q "^Host mt5-vps$" "$HOME/.ssh/config" 2>/dev/null; then
    cat >> "$HOME/.ssh/config" <<EOF

Host mt5-vps
    HostName $IP
    User $ADMIN_USER
    IdentityFile ~/.ssh/id_ed25519
EOF
fi

echo
echo "Done. Connect with:  ssh mt5-vps"
echo "Pull logs with:      scp mt5-vps:Desktop/MT5_Trader/logs/*.jsonl ."
