param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [double]$ActionTickSeconds = 0.03,
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

$TeamA = "26"
$TeamB = "27"

$teamALeaderTag = New-RandomMemberTag
$teamAFollowerTag = New-RandomMemberTag -Exclude @($teamALeaderTag)
$teamBLeaderTag = New-RandomMemberTag -Exclude @($teamALeaderTag, $teamAFollowerTag)
$teamBFollowerTag = New-RandomMemberTag -Exclude @($teamALeaderTag, $teamAFollowerTag, $teamBLeaderTag)

$teamALeaderUsername = "CTF-$TeamA-$teamALeaderTag"
$teamAFollowerUsername = "CTF-$TeamA-$teamAFollowerTag"
$teamBLeaderUsername = "CTF-$TeamB-$teamBLeaderTag"
$teamBFollowerUsername = "CTF-$TeamB-$teamBFollowerTag"

$StrategyName = "hybrid_strategy.HybridStrategy"

$botSpecs = @(
    @{
        Label = "teamA-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$teamAFollowerTag",
            "--username", "$teamAFollowerUsername",
            "--server", "$Server",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "teamB-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$teamBFollowerTag",
            "--username", "$teamBFollowerUsername",
            "--server", "$Server",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $TeamSpacingDelaySeconds
    },
    @{
        Label = "teamA-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$teamALeaderTag",
            "--username", "$teamALeaderUsername",
            "--server", "$Server",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "teamB-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$teamBLeaderTag",
            "--username", "$teamBLeaderUsername",
            "--server", "$Server",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== HYBRID STRATEGY 2V2 ===" -ForegroundColor Cyan
Write-Host "Server:        $Server"
Write-Host "Team A:        $TeamA  ($teamALeaderUsername, $teamAFollowerUsername)"
Write-Host "Team B:        $TeamB  ($teamBLeaderUsername, $teamBFollowerUsername)"
Write-Host "Strategy:      HybridStrategy"
Write-Host "Players/team:  $PerTeamPlayer"
Write-Host "Map:           $MapMode"
Write-Host "Action tick:   ${ActionTickSeconds}s"
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

if (-not $Wait) {
    Write-Host "Bots launched. Use -Wait to block until they exit." -ForegroundColor Green
    exit 0
}

try {
    $processes | Wait-Process
}
finally {
    $running = $processes | Where-Object { -not $_.HasExited }
    if ($running) {
        $running | Stop-Process -Force
    }
}