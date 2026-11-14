param(
    [string]$InputPath = 'C:\Users\vikas\Downloads\MobileRobotRecharging-main\MobileRobotRecharging-main\mobile-recharger-path-planning-scheduling\approach_two-shot\examples\input-warehouse',
    [switch]$Quick
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $InputPath 'workspace.txt'))) {
    throw "Dataset not found: $InputPath"
}
$pythonExe = (Get-Command python -ErrorAction Stop).Source
$logDir = Join-Path $PSScriptRoot 'comparison_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir ('comparison-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
$cases = @(
    @{ Name = 'Small centralized vs decentralized'; Args = @('--workers', '1', '--rechargers', '1') }
)
if (-not $Quick) {
    $cases += @(
        @{ Name = 'Three-worker centralized vs decentralized'; Args = @('--workers', '3', '--rechargers', '2') },
        @{ Name = 'Three-worker refinement variant (compare with previous baseline)'; Args = @('--workers', '3', '--rechargers', '2', '--refine', '2', '--skip-central') },
        @{ Name = 'One-worker one-recharger Phase 2 cost variant (applied to both approaches)'; Args = @('--workers', '1', '--rechargers', '1', '--phase2-cost') },
        @{ Name = 'Two-worker one-recharger optimization-off baseline'; Args = @('--workers', '2', '--rechargers', '1') },
        @{ Name = 'Two-worker one-recharger Phase 2 cost variant'; Args = @('--workers', '2', '--rechargers', '1', '--phase2-cost') }
    )
}

$previousLocation = Get-Location
Start-Transcript -LiteralPath $logPath | Out-Null
try {
    Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
    Write-Host "Live comparisons: $($cases.Count) cases. Solver calls may take several minutes without intermediate output."
    Write-Host "Log: $logPath"
    Write-Host 'Dataset: warehouse; T=8; R_s=6; collision constraints enabled; legacy waypoint model.'
    Write-Host 'Wall times include the displayed pipeline stages; treat this as an initial comparison suite.'
    for ($caseIndex = 0; $caseIndex -lt $cases.Count; $caseIndex++) {
        $case = $cases[$caseIndex]
        Write-Host "`n[$($caseIndex + 1)/$($cases.Count)] $($case.Name) -- $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
        $caseArgs = @('-u', '-m', 'mrp.compare', '--input', $InputPath, '--T', '8', '--Rs', '6') + $case.Args
        & $pythonExe @caseArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Comparison process failed with exit code $LASTEXITCODE"
        }
    }
    Write-Host "`nAll comparison processes finished. Review coverage, solver status, conflicts and deployment for each case." -ForegroundColor Green
    Write-Host "Saved log: $logPath"
}
finally {
    Set-Location -LiteralPath $previousLocation
    Stop-Transcript | Out-Null
}
