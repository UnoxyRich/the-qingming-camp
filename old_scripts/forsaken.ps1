param(
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "fixed",
    [double]$ActionTickSeconds = 0.02,
    [int]$LeaderStartupDelaySeconds = 3,
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

# --- Team config ---
$TeamNum = "26"
$Server = "10.31.0.101"

# --- Against team ---
$AgainstTeam = "random"

# --- Generate unique bot tags ---
$leaderTag  = New-RandomMemberTag
$followerTag = New-RandomMemberTag -Exclude @($leaderTag)

$leaderUsername  = "CTF-$TeamNum-$leaderTag"
$followerUsername = "CTF-$TeamNum-$followerTag"

$strategyName = "forsaken_strategy.ForsakenStrategy"

# --- Bot launch specs ---
$botSpecs = @(
    @{
        Label = "forsaken-2"
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
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "forsaken-1"
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
            "--strategy", "$strategyName",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    }
)

# --- Preview ---
Write-Host ""
Write-Host "=== FORSAKEN (Fast Sprint) ===" -ForegroundColor Magenta
Write-Host "Our team:     $TeamNum  ($leaderUsername, $followerUsername)"
Write-Host "Server:       $Server"
Write-Host "Opponent:     $AgainstTeam"
Write-Host "Strategy:     ForsakenStrategy (aggressive pathfinder, 0.02s tick)"
Write-Host "Action tick:  ${ActionTickSeconds}s"
Write-Host "Chat intent:  with $AgainstTeam $PerTeamPlayer $MapMode"
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

# --- Launch ---
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
