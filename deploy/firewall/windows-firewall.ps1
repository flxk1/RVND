# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026 flxk1
# Block every outbound protocol/address for the dedicated agent identity.

param(
    [Parameter(Mandatory = $true)]
    [string]$AgentUser,
    [ValidateSet('Plan', 'Apply', 'Remove')]
    [string]$Mode = 'Plan'
)

$RuleName = 'Rvnd egress lock - block agent'
$agentSid = (New-Object System.Security.Principal.NTAccount($AgentUser)).
    Translate([System.Security.Principal.SecurityIdentifier]).Value

if ($Mode -eq 'Remove') {
    Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    Write-Host 'RVND agent egress block removed.'
    exit 0
}
if ($Mode -eq 'Plan') {
    Write-Host "PLAN ONLY: block all outbound traffic for $AgentUser ($agentSid)."
    exit 0
}

Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $RuleName `
    -Direction Outbound -Action Block `
    -LocalUser "O:LSD:(A;;CC;;;$agentSid)" | Out-Null
Write-Host 'RVND egress lock applied: agent account has no outbound path.'
