param(
    [double]$ActionTickSeconds = 0.03,
    [int]$LeaderStartupDelaySeconds = 3,
    [switch]$Wait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

function New-RandomMemberTag {
    param(
        [string[]]$Exclude = @(),
        [int]$Length = 3
    )

    $letters = @("A","B","C","D","E","F","G","H","J","K","L","M","N","P","Q","R","S","T","U","V","W","X","Y","Z")

    do {
        $tag = -join (1..$Length | ForEach-Object { Get-Random -InputObject $letters })
    } while ($tag -in $Exclude)

    return $tag
}

$Server = "10.31.0.101"
$TeamNum = "26"
$AgainstTeam = "random"
$PerTeamPlayer = 2
$MapMode = "random"
$StrategyName = "pressure_strategy.PressureStrategy"

$leaderTag = New-RandomMemberTag
$followerTag = New-RandomMemberTag -Exclude @($leaderTag)

$leaderUsername = "CTF-$TeamNum-$leaderTag"
$followerUsername = "CTF-$TeamNum-$followerTag"

$botSpecs = @(
    @{
        Label = "bot-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$followerTag",
            "--username", "$followerUsername",
            "--server", "$Server",
            "--against", "$AgainstTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "bot-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$leaderTag",
            "--username", "$leaderUsername",
            "--server", "$Server",
            "--against", "$AgainstTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--action-tick", "$ActionTickSeconds",
            "--strategy", "$StrategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    }
)

Write-Host ""
Write-Host "=== PRESSURE STRATEGY RANDOM MAP ===" -ForegroundColor Cyan
Write-Host "Our team:      $TeamNum  ($leaderUsername, $followerUsername)"
Write-Host "Opponent:      $AgainstTeam"
Write-Host "Server:        $Server"
Write-Host "Strategy:      PressureStrategy"
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