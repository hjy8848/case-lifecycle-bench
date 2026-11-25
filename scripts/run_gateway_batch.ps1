<#
Run all 20 templates against the LiteLLM gateway with the simulated (LLM) customer, one background
process per template. Needs the tunnel and key: dot-source scripts\litellm.ps1 first.

    . .\scripts\litellm.ps1
    .\scripts\run_gateway_batch.ps1 -Tag 20261125g -Model dashscope/qwen3.7-plus -Workers 4

Result files are reused, so running it again fills in only what is missing.
#>
param(
    [string]$Tag = (Get-Date -Format "yyyyMMddHHmm"),
    [string]$Model = "dashscope/qwen3.7-plus",
    [int]$Workers = 4,
    [string[]]$Only = @()
)

$py = "C:\Users\sylva\AppData\Local\Programs\Python\Python312\python.exe"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "runs\gw-$Tag"
New-Item -ItemType Directory -Force $out | Out-Null
$env:PYTHONUTF8 = "1"

$names = & $py -c "from case_lifecycle_bench.runnable_templates import ALL; print(' '.join(ALL))"
if ($Only.Count) { $names = ($names -split ' ') | Where-Object { $Only -contains $_ } } else { $names = $names -split ' ' }

$commit = (git -C $root rev-parse --short HEAD)
$dirty = [bool](git -C $root status --porcelain)
@{ commit = $commit; tree_clean = (-not $dirty); model = $Model; customer = "llm"; backend = "litellm gateway" } | ConvertTo-Json | Set-Content (Join-Path $out "PROVENANCE.json") -Encoding ascii

foreach ($name in $names) {
    $dir = Join-Path $out "$name-llm"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $arguments = @(
        "-m", "case_lifecycle_bench.template_run", "--template", $name, "--start-seed", "300", "--seeds", "10", "--p7",
        "--model", $Model, "--api-bases", $env:CASE_LIFECYCLE_API_BASE, "--workers-per-replica", $Workers,
        "--output", $dir, "--disable-thinking", "--llm-customer"
    )
    Start-Process $py -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $dir "summary.json") -RedirectStandardError (Join-Path $dir "err.txt")
}
Write-Host "started $($names.Count) templates into $out (commit $commit, clean tree: $(-not $dirty))"
