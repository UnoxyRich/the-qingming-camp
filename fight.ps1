param(
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [double]$ActionTickSeconds = 0.05,
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

# --- Scan server for opponent team ---
Write-Host "Scanning server for online teams..." -ForegroundColor Yellow
$scanScript = @"
import javascript, json, time, re, sys
from javascript import require, once
mf = require('mineflayer')
bot = mf.createBot({'host': '$Server', 'port': 25565, 'username': 'CTF-$TeamNum-Scan', 'hideErrors': False})
once(bot, 'login')
time.sleep(2)
teams = set()
my_team = $TeamNum
for name in list(bot.players):
    m = re.match(r'^CTF-(\d+)-', str(name))
    if m and int(m.group(1)) != my_team:
        teams.add(int(m.group(1)))
bot.quit('done')
javascript.terminate()
print(json.dumps(sorted(teams)))
"@
$scanResult = python -c $scanScript 2>$null
$otherTeams = @()
if ($scanResult) {
    try { $otherTeams = $scanResult | ConvertFrom-Json } catch { $otherTeams = @() }
}
if ($otherTeams.Count -gt 0) {
    $AgainstTeam = "$($otherTeams | Get-Random)"
    Write-Host "Found teams: $($otherTeams -join ', ')" -ForegroundColor Green
    Write-Host "Selected opponent: $AgainstTeam" -ForegroundColor Green
} else {
    $AgainstTeam = "$(Get-Random -Minimum 100 -Maximum 1000)"
    Write-Host "No other teams found, using random: $AgainstTeam" -ForegroundColor Yellow
}

# --- Generate unique bot tags ---
$leaderTag  = New-RandomMemberTag
$followerTag = New-RandomMemberTag -Exclude @($leaderTag)

$leaderUsername  = "CTF-$TeamNum-$leaderTag"
$followerUsername = "CTF-$TeamNum-$followerTag"

$strategyName = "collect_only_strategy.CollectOnlyStrategy"

# --- Bot launch specs ---
$botSpecs = @(
    @{
        Label = "bot-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$followerTag",
            "--username", "$followerUsername",
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
        Label = "bot-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$leaderTag",
            "--username", "$leaderUsername",
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
Write-Host "=== FIGHT ===" -ForegroundColor Cyan
Write-Host "Our team:     $TeamNum  ($leaderUsername, $followerUsername)"
Write-Host "Opponent:     $AgainstTeam"
Write-Host "Strategy:     CollectOnlyStrategy"
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
    if ($running) {
        $running | Stop-Process -Force
    }
}
