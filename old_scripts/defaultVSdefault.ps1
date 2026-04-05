param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "fixed",
    [double]$ActionTickSeconds = 0.05,
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

$teamA_LeaderTag = New-RandomMemberTag
$teamA_FollowerTag = New-RandomMemberTag -Exclude @($teamA_LeaderTag)
$teamB_LeaderTag = New-RandomMemberTag -Exclude @($teamA_LeaderTag, $teamA_FollowerTag)
$teamB_FollowerTag = New-RandomMemberTag -Exclude @($teamA_LeaderTag, $teamA_FollowerTag, $teamB_LeaderTag)

$teamA_LeaderUsername = "CTF-$TeamA-$teamA_LeaderTag"
$teamA_FollowerUsername = "CTF-$TeamA-$teamA_FollowerTag"
$teamB_LeaderUsername = "CTF-$TeamB-$teamB_LeaderTag"
$teamB_FollowerUsername = "CTF-$TeamB-$teamB_FollowerTag"

$strategyName = "hybrid_strategy.HybridStrategy"

$botSpecs = @(
    @{
        Label = "teamA-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$teamA_FollowerTag",
            "--username", "$teamA_FollowerUsername",
            "--server", "$Server",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "teamB-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$teamB_FollowerTag",
            "--username", "$teamB_FollowerUsername",
            "--server", "$Server",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $TeamSpacingDelaySeconds
    },
    @{
        Label = "teamA-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$teamA_LeaderTag",
            "--username", "$teamA_LeaderUsername",
            "--server", "$Server",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "teamB-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$teamB_LeaderTag",
            "--username", "$teamB_LeaderUsername",
            "--server", "$Server",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== HYBRID VS HYBRID (archived 2v2 launcher) ===" -ForegroundColor Cyan
Write-Host "Server:        $Server"
Write-Host "Team A:        $TeamA  ($teamA_LeaderUsername, $teamA_FollowerUsername)"
Write-Host "Team B:        $TeamB  ($teamB_LeaderUsername, $teamB_FollowerUsername)"
Write-Host "Strategy:      $strategyName"
Write-Host "Action tick:   ${ActionTickSeconds}s"
Write-Host "Match:         $TeamA vs $TeamB, $PerTeamPlayer players each, map=$MapMode"
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