<#
LiteLLM gateway helper. The key never goes into a file, the repo or a log.

  First time (once per key):   .\scripts\litellm.ps1 -SetKey
      Reads the key from the clipboard (or asks for it, hidden) and stores it as the user
      environment variable LITELLM_API_KEY. New terminals see it; clear the clipboard after.

  Every session:               . .\scripts\litellm.ps1
      (dot-source it) Opens the SSH tunnel to the gateway through lab-gate if it is not up,
      and sets CASE_LIFECYCLE_API_BASE / CASE_LIFECYCLE_API_KEY (and the user-model pair)
      for this shell only.

  Stop the tunnel:             .\scripts\litellm.ps1 -Stop
#>
param([switch]$SetKey, [switch]$Stop, [int]$Port = 18010)

$gateway = "10.130.138.46:8010"

if ($SetKey) {
    $key = ""
    try { $key = (Get-Clipboard -Raw).Trim() } catch {}
    if ($key -notmatch '^sk-[A-Za-z0-9_\-]{8,}$') {
        $secure = Read-Host "LiteLLM key (hidden)" -AsSecureString
        $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    if (-not $key) { Write-Error "no key given"; return }
    [Environment]::SetEnvironmentVariable("LITELLM_API_KEY", $key, "User")
    Write-Host "LITELLM_API_KEY stored for your user account ($($key.Length) characters). Open a new terminal to use it."
    try { Set-Clipboard -Value " " } catch {}
    return
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Stop) {
    if ($listener) { Stop-Process -Id $listener.OwningProcess -Force; Write-Host "tunnel stopped" } else { Write-Host "no tunnel" }
    return
}

if (-not $listener) {
    Start-Process ssh -WindowStyle Hidden -ArgumentList @(
        "-N", "-L", "127.0.0.1:${Port}:$gateway", "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "lab-gate")
    for ($i = 0; $i -lt 20 -and -not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
}

$key = [Environment]::GetEnvironmentVariable("LITELLM_API_KEY", "User")
if (-not $key) { $key = $env:LITELLM_API_KEY }
if (-not $key) { Write-Warning "LITELLM_API_KEY is not set; run .\scripts\litellm.ps1 -SetKey once"; return }

$env:LITELLM_API_KEY = $key
$env:NO_PROXY = "127.0.0.1,localhost"
$env:CASE_LIFECYCLE_API_BASE = "http://127.0.0.1:$Port/v1"
$env:CASE_LIFECYCLE_API_KEY = $key
$env:CASE_LIFECYCLE_USER_API_BASE = $env:CASE_LIFECYCLE_API_BASE
$env:CASE_LIFECYCLE_USER_API_KEY = $key
try {
    $code = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Headers @{ Authorization = "Bearer $key" } "http://127.0.0.1:$Port/v1/models").StatusCode
    Write-Host "gateway reachable on 127.0.0.1:$Port (HTTP $code); CASE_LIFECYCLE_* set for this shell"
} catch { Write-Warning "tunnel is up but the gateway did not answer: $($_.Exception.Message)" }
