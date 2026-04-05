param(
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "fixed",
    [double]$NormalActionTickSeconds = 0.05,
    [double]$ForkedActionTickSeconds = 0.04,
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

$NormalTeamNum = "26"
$ForkedTeamNum = "27"

$normalLeaderTag = New-RandomMemberTag
$normalFollowerTag = New-RandomMemberTag -Exclude @($normalLeaderTag)
$forkedLeaderTag = New-RandomMemberTag -Exclude @($normalLeaderTag, $normalFollowerTag)
$forkedFollowerTag = New-RandomMemberTag -Exclude @($normalLeaderTag, $normalFollowerTag, $forkedLeaderTag)

$normalLeaderUsername = "CTF-$NormalTeamNum-$normalLeaderTag"
$normalFollowerUsername = "CTF-$NormalTeamNum-$normalFollowerTag"
$forkedLeaderUsername = "CTF-$ForkedTeamNum-$forkedLeaderTag"
$forkedFollowerUsername = "CTF-$ForkedTeamNum-$forkedFollowerTag"

$normalStrategyName = "normal_strategy.NormalStrategy"
$forkedStrategyName = "forked_normal_strategy.ForkedNormalStrategy"

$botSpecs = @(
    @{
        Label = "normal-2"
        Arguments = @(
            "main.py",
            "--my-team", "$NormalTeamNum",
            "--my-no", "$normalFollowerTag",
            "--username", "$normalFollowerUsername",
            "--server", "$Server",
            "--against", "$ForkedTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "forked-2"
        Arguments = @(
            "main.py",
            "--my-team", "$ForkedTeamNum",
            "--my-no", "$forkedFollowerTag",
            "--username", "$forkedFollowerUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ForkedActionTickSeconds",
            "--strategy", "$forkedStrategyName",
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
            "--against", "$ForkedTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$NormalActionTickSeconds",
            "--strategy", "$normalStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    },
    @{
        Label = "forked-1"
        Arguments = @(
            "main.py",
            "--my-team", "$ForkedTeamNum",
            "--my-no", "$forkedLeaderTag",
            "--username", "$forkedLeaderUsername",
            "--server", "$Server",
            "--against", "$NormalTeamNum",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ForkedActionTickSeconds",
            "--strategy", "$forkedStrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = ($LeaderStartupDelaySeconds + $TeamSpacingDelaySeconds)
    }
)

Write-Host ""
Write-Host "=== NORMAL VS FORKED NORMAL ===" -ForegroundColor Cyan
Write-Host "Server:          $Server"
Write-Host "Normal team:     $NormalTeamNum  ($normalLeaderUsername, $normalFollowerUsername)"
Write-Host "Strategy:        NormalStrategy"
Write-Host "Action tick:     ${NormalActionTickSeconds}s"
Write-Host "Forked team:     $ForkedTeamNum  ($forkedLeaderUsername, $forkedFollowerUsername)"
Write-Host "Strategy:        ForkedNormalStrategy"
Write-Host "Action tick:     ${ForkedActionTickSeconds}s"
Write-Host "Match:           $NormalTeamNum vs $ForkedTeamNum, $PerTeamPlayer players each, map=$MapMode"
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