<#
Keep the SSH tunnel to the LiteLLM gateway up: start it, and start it again whenever it ends.
Run it hidden and leave it; stop it with scripts\litellm.ps1 -Stop (which also ends this loop's ssh).

    Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-File','scripts\tunnel_keepalive.ps1'
#>
param([int]$Port = 18010, [string]$Gateway = "10.130.138.46:8010")

while ($true) {
    if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        $p = Start-Process ssh -WindowStyle Hidden -PassThru -ArgumentList @(
            "-N", "-L", "127.0.0.1:${Port}:$Gateway", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "lab-gate")
        Add-Content (Join-Path $PSScriptRoot "..\runs\tunnel.log") "$(Get-Date -Format 'HH:mm:ss') tunnel started (ssh pid $($p.Id))"
    }
    Start-Sleep 10
}
