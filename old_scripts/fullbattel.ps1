param(
    [string]$TeamA = "",
    [string]$TeamB = "",
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [string]$TeamAPrefix = "",
    [string]$TeamBPrefix = "",
    [string]$TeamALeaderName = "",
    [string]$TeamAFollowerName = "",
    [string]$TeamBLeaderName = "",
    [string]$TeamBFollowerName = "",
    [int]$SecondWaveDelaySeconds = 3,
    [switch]$Wait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = $scriptDir
$partnerDir = Join-Path $scriptDir "partner_build"

function New-RandomMemberTag {
    param([string[]]$Exclude = @())
    $letters = @("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")
    $choices = $letters | Where-Object { $_ -notin $Exclude }
    return Get-Random -InputObject $choices
}

function New-RandomTeamNumber {
    param([string[]]$Exclude = @())

    do {
        $candidate = Get-Random -Minimum 100 -Maximum 1000
    } while ("$candidate" -in $Exclude)

    return "$candidate"
}

if (-not $TeamA) {
    $TeamA = New-RandomTeamNumber
}
if (-not $TeamB) {
    $TeamB = New-RandomTeamNumber -Exclude @($TeamA)
}

if (-not $TeamALeaderName) {
    $TeamALeaderName = New-RandomMemberTag
}
if (-not $TeamAFollowerName) {
    $TeamAFollowerName = New-RandomMemberTag -Exclude @($TeamALeaderName)
}
if (-not $TeamBLeaderName) {
    $TeamBLeaderName = New-RandomMemberTag
}
if (-not $TeamBFollowerName) {
    $TeamBFollowerName = New-RandomMemberTag -Exclude @($TeamBLeaderName)
}

if (-not $TeamAPrefix) {
    $TeamAPrefix = $TeamA
}
if (-not $TeamBPrefix) {
    $TeamBPrefix = $TeamB
}

$teamALeaderUsername = "CTF-$TeamAPrefix-$TeamALeaderName"
$teamAFollowerUsername = "CTF-$TeamAPrefix-$TeamAFollowerName"
$teamBLeaderUsername = "CTF-$TeamBPrefix-$TeamBLeaderName"
$teamBFollowerUsername = "CTF-$TeamBPrefix-$TeamBFollowerName"

$botSpecs = @(
    @{
        Label = "team-a-attacker-2"
        WorkingDirectory = $rootDir
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$TeamAFollowerName",
            "--username", "$teamAFollowerUsername",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "team-b-attacker-2"
        WorkingDirectory = $rootDir
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$TeamBFollowerName",
            "--username", "$teamBFollowerUsername",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "team-a-attacker-1"
        WorkingDirectory = $rootDir
        Arguments = @(
            "main.py",
            "--my-team", "$TeamA",
            "--my-no", "$TeamALeaderName",
            "--username", "$teamALeaderUsername",
            "--against", "$TeamB",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = $SecondWaveDelaySeconds
    },
    @{
        Label = "team-b-attacker-1"
        WorkingDirectory = $rootDir
        Arguments = @(
            "main.py",
            "--my-team", "$TeamB",
            "--my-no", "$TeamBLeaderName",
            "--username", "$teamBLeaderUsername",
            "--against", "$TeamA",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    }
)

foreach ($bot in $botSpecs) {
    $commandPreview = "python " + (($bot.Arguments | ForEach-Object {
        if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
    }) -join " ")
    Write-Host ("[{0}] {1}" -f $bot.Label, $commandPreview)
}

Write-Host ("Using team A bots: {0}, {1}" -f $teamALeaderUsername, $teamAFollowerUsername)
Write-Host ("Using team B bots: {0}, {1}" -f $teamBLeaderUsername, $teamBFollowerUsername)

if ($DryRun) {
    exit 0
}

$processes = foreach ($bot in $botSpecs) {
    if ($bot.DelayBeforeStartSeconds -gt 0) {
        Start-Sleep -Seconds $bot.DelayBeforeStartSeconds
    }
    Start-Process `
        -FilePath "python" `
        -ArgumentList $bot.Arguments `
        -WorkingDirectory $bot.WorkingDirectory `
        -PassThru
}

if (-not $Wait) {
    Write-Host "Started 4-bot full battle. Use -Wait to block until they exit."
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