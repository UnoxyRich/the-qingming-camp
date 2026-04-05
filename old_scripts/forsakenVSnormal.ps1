param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "fixed",
    [double]$ForsakenActionTickSeconds = 0.02,
    [double]$NormalActionTickSeconds = 0.05,
    [int]$LeaderStartupDelaySeconds = 3,
    [int]$TeamSpacingDelaySeconds = 1,
    [switch]$Wait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function New-RandomMemberTag {
    param([string[]]$Exclude = @())
    $letters = @("A","B","C","D","E","F","G","H","J","K","L","M","N","P","Q","R","S","T","U","V","W","X","Y","Z")
    $choices = $letters | Where-Object { $_ -notin $Exclude }
    return Get-Random -InputObject $choices
}

$Server = "10.31.0.101"
$PerTeamPlayer = 2

$ForsakenTeamNum = "27"
$NormalTeamNum = "26"

$forsakenLeaderTag = New-RandomMemberTag
$forsakenFollowerTag = New-RandomMemberTag -Exclude @($forsakenLeaderTag)
$normalLeaderTag = New-RandomMemberTag -Exclude @($forsakenLeaderTag, $forsakenFollowerTag)
$normalFollowerTag = New-RandomMemberTag -Exclude @($forsakenLeaderTag, $forsakenFollowerTag, $normalLeaderTag)

$forsakenLeaderUsername = "CTF-$ForsakenTeamNum-$forsakenLeaderTag"
$forsakenFollowerUsername = "CTF-$ForsakenTeamNum-$forsakenFollowerTag"
$normalLeaderUsername = "CTF-$NormalTeamNum-$normalLeaderTag"
$normalFollowerUsername = "CTF-$NormalTeamNum-$normalFollowerTag"

$forsakenStrategyName = "forsaken_strategy.ForsakenStrategy"
$normalStrategyName = "normal_strategy.NormalStrategy"

$botSpecs = @(
    @{
        Label = "normal-2"
        Arguments = @(
            "main.py",
            "--my-team", "$NormalTeamNum",
            "--my-no", "$normalFollowerTag",
            "--username", "$normalFollowerUsername",
            "--server", "$Server",
            "--against", "$ForsakenTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "forsaken-2"
        Arguments = @(
            "main.py",
            "--my-team", "$ForsakenTeamNum",
            "--my-no", "$forsakenFollowerTag",
            "--username", "$forsakenFollowerUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ForsakenActionTickSeconds",
            "--strategy", "$forsakenStrategyName",
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
            "--against", "$ForsakenTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "forsaken-1"
        Arguments = @(
            "main.py",
            "--my-team", "$ForsakenTeamNum",
            "--my-no", "$forsakenLeaderTag",
            "--username", "$forsakenLeaderUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ForsakenActionTickSeconds",
            "--strategy", "$forsakenStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== FORSAKEN VS NORMAL ===" -ForegroundColor Cyan
Write-Host "Server:         $Server"
Write-Host "Forsaken team:  $ForsakenTeamNum  ($forsakenLeaderUsername, $forsakenFollowerUsername)"
Write-Host "Strategy:       ForsakenStrategy"
Write-Host "Action tick:    ${ForsakenActionTickSeconds}s"
Write-Host "Normal team:    $NormalTeamNum  ($normalLeaderUsername, $normalFollowerUsername)"
Write-Host "Strategy:       NormalStrategy"
Write-Host "Action tick:    ${NormalActionTickSeconds}s"
Write-Host "Match:          $ForsakenTeamNum vs $NormalTeamNum, $PerTeamPlayer players each, map=$MapMode"
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
        -WorkingDirectory $scriptDir `
        -PassThru
}

if (-not $Wait) {
    Write-Host "Bots launched. Use -Wait to block until they exit." -ForegroundColor Green
    exit 0
}

try {
    $processes | Wait-Process
}
finally {
    $running = $processes | Where-Object { -not $_.HasExited }
    foreach ($p in $running) {
        try { $p.Kill() } catch {}
    }
}