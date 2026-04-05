param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [double]$SmartActionTickSeconds = 0.03,
    [double]$AfkActionTickSeconds = 0.10,
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

$SmartTeam = "26"
$AfkTeam = "27"

$smartLeaderTag = New-RandomMemberTag
$smartFollowerTag = New-RandomMemberTag -Exclude @($smartLeaderTag)
$afkLeaderTag = New-RandomMemberTag -Exclude @($smartLeaderTag, $smartFollowerTag)
$afkFollowerTag = New-RandomMemberTag -Exclude @($smartLeaderTag, $smartFollowerTag, $afkLeaderTag)

$smartLeaderUsername = "CTF-$SmartTeam-$smartLeaderTag"
$smartFollowerUsername = "CTF-$SmartTeam-$smartFollowerTag"
$afkLeaderUsername = "CTF-$AfkTeam-$afkLeaderTag"
$afkFollowerUsername = "CTF-$AfkTeam-$afkFollowerTag"

$smartStrategyName = "hybrid_strategy.HybridStrategy"
$afkStrategyName = "afk_strategy.AfkStrategy"

$botSpecs = @(
    @{
        Label = "smart-2"
        Arguments = @(
            "main.py",
            "--my-team", "$SmartTeam",
            "--my-no", "$smartFollowerTag",
            "--username", "$smartFollowerUsername",
            "--server", "$Server",
            "--against", "$AfkTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$SmartActionTickSeconds",
            "--strategy", "$smartStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "afk-2"
        Arguments = @(
            "main.py",
            "--my-team", "$AfkTeam",
            "--my-no", "$afkFollowerTag",
            "--username", "$afkFollowerUsername",
            "--server", "$Server",
            "--against", "$SmartTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$AfkActionTickSeconds",
            "--strategy", "$afkStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $TeamSpacingDelaySeconds
    },
    @{
        Label = "smart-1"
        Arguments = @(
            "main.py",
            "--my-team", "$SmartTeam",
            "--my-no", "$smartLeaderTag",
            "--username", "$smartLeaderUsername",
            "--server", "$Server",
            "--against", "$AfkTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$SmartActionTickSeconds",
            "--strategy", "$smartStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "afk-1"
        Arguments = @(
            "main.py",
            "--my-team", "$AfkTeam",
            "--my-no", "$afkLeaderTag",
            "--username", "$afkLeaderUsername",
            "--server", "$Server",
            "--against", "$SmartTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$AfkActionTickSeconds",
            "--strategy", "$afkStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== 2V2 SMART VS AFK ===" -ForegroundColor Cyan
Write-Host "Server:        $Server"
Write-Host "Smart team:    $SmartTeam  ($smartLeaderUsername, $smartFollowerUsername)"
Write-Host "Strategy:      HybridStrategy"
Write-Host "AFK team:      $AfkTeam  ($afkLeaderUsername, $afkFollowerUsername)"
Write-Host "Strategy:      AfkStrategy"
Write-Host "Players/team:  $PerTeamPlayer"
Write-Host "Map:           $MapMode"
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
    if ($running) {
        $running | Stop-Process -Force
    }
}