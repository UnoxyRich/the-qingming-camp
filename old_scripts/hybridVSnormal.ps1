param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "fixed",
    [double]$HybridActionTickSeconds = 0.03,
    [double]$NormalActionTickSeconds = 0.03,
    [int]$LeaderStartupDelaySeconds = 3,
    [int]$TeamSpacingDelaySeconds = 1,
    [switch]$Wait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

function New-RandomMemberTag {
    param([string[]]$Exclude = @())
    $letters = @("A","B","C","D","E","F","G","H","J","K","L","M","N","P","Q","R","S","T","U","V","W","X","Y","Z")
    $choices = $letters | Where-Object { $_ -notin $Exclude }
    return Get-Random -InputObject $choices
}

$Server = "10.31.0.101"
$PerTeamPlayer = 2

$HybridTeamNum = "27"
$NormalTeamNum = "26"

$hybridLeaderTag = New-RandomMemberTag
$hybridFollowerTag = New-RandomMemberTag -Exclude @($hybridLeaderTag)
$normalLeaderTag = New-RandomMemberTag -Exclude @($hybridLeaderTag, $hybridFollowerTag)
$normalFollowerTag = New-RandomMemberTag -Exclude @($hybridLeaderTag, $hybridFollowerTag, $normalLeaderTag)

$hybridLeaderUsername = "CTF-$HybridTeamNum-$hybridLeaderTag"
$hybridFollowerUsername = "CTF-$HybridTeamNum-$hybridFollowerTag"
$normalLeaderUsername = "CTF-$NormalTeamNum-$normalLeaderTag"
$normalFollowerUsername = "CTF-$NormalTeamNum-$normalFollowerTag"

$hybridStrategyName = "hybrid_strategy.HybridStrategy"
$normalStrategyName = "hybrid_strategy.HybridStrategy"

$botSpecs = @(
    @{
        Label = "normal-2"
        Arguments = @(
            "main.py",
            "--my-team", "$NormalTeamNum",
            "--my-no", "$normalFollowerTag",
            "--username", "$normalFollowerUsername",
            "--server", "$Server",
            "--against", "$HybridTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "hybrid-2"
        Arguments = @(
            "main.py",
            "--my-team", "$HybridTeamNum",
            "--my-no", "$hybridFollowerTag",
            "--username", "$hybridFollowerUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$HybridActionTickSeconds",
            "--strategy", "$hybridStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $TeamSpacingDelaySeconds
    },
    @{
        Label = "normal-1"
        Arguments = @(
            "main.py",
            "--my-team", "$NormalTeamNum",
            "--my-no", "$normalLeaderTag",
            "--username", "$normalLeaderUsername",
            "--server", "$Server",
            "--against", "$HybridTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "hybrid-1"
        Arguments = @(
            "main.py",
            "--my-team", "$HybridTeamNum",
            "--my-no", "$hybridLeaderTag",
            "--username", "$hybridLeaderUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$HybridActionTickSeconds",
            "--strategy", "$hybridStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== HYBRID VS HYBRID (archived comparison launcher) ===" -ForegroundColor Cyan
Write-Host "Server:        $Server"
Write-Host "Hybrid team:   $HybridTeamNum  ($hybridLeaderUsername, $hybridFollowerUsername)"
Write-Host "Strategy:      HybridStrategy"
Write-Host "Action tick:   ${HybridActionTickSeconds}s"
Write-Host "Normal team:   $NormalTeamNum  ($normalLeaderUsername, $normalFollowerUsername)"
Write-Host "Strategy:      HybridStrategy"
Write-Host "Action tick:   ${NormalActionTickSeconds}s"
Write-Host "Match:         $HybridTeamNum vs $NormalTeamNum, $PerTeamPlayer players each, map=$MapMode"
Write-Host ""

foreach ($bot in $botSpecs) {
    $cmd = "python " + (($bot.Arguments | ForEach-Object {
        if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
    }) -join " ")
    Write-Host ("[{0}] {1}" -f $bot.Label, $cmd)
}
Write-Host ""

if ($DryRun) {
    Write-Host "(dry run - no processes started)" -ForegroundColor Yellow
    exit 0
}

$processes = foreach ($bot in $botSpecs) {
    if ($bot.DelayBeforeStartSeconds -gt 0) {
        Start-Sleep -Seconds $bot.DelayBeforeStartSeconds
    }

    Start-Process `
        -FilePath "python" `
        -ArgumentList $bot.Arguments `
        -WorkingDirectory $repoRoot `
        -PassThru
}

if ($Wait) {
    Write-Host "Waiting for all bots to exit..." -ForegroundColor DarkGray
    $processes | ForEach-Object { $_.WaitForExit() }
    Write-Host "All bots have exited." -ForegroundColor Green
}