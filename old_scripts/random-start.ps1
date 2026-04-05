param(
    [string]$TeamNum = "",
    [string]$AgainstTeam = "random",
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [string]$NameTeamPrefix = "",
    [string]$LeaderName = "",
    [string]$FollowerName = "",
    [int]$LeaderStartupDelaySeconds = 3,
    [switch]$Wait,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-AgainstTeamValue {
    param([string]$Value)

    if ($Value -in @("none", "random")) {
        return $true
    }

    return $Value -match '^[0-9]+$'
}

function New-RandomMemberTag {
    param([string[]]$Exclude = @())

    $letters = @("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")
    $choices = $letters | Where-Object { $_ -notin $Exclude }
    return Get-Random -InputObject $choices
}

function New-RandomTeamNumber {
    return "$(Get-Random -Minimum 100 -Maximum 1000)"
}

if (-not (Test-AgainstTeamValue -Value $AgainstTeam)) {
    throw "AgainstTeam must be one of: none, random, or a numeric team id."
}

if (-not $TeamNum) {
    $TeamNum = New-RandomTeamNumber
}

if (-not $LeaderName) {
    $LeaderName = New-RandomMemberTag
}

if (-not $FollowerName) {
    $FollowerName = New-RandomMemberTag -Exclude @($LeaderName)
}

if (-not $NameTeamPrefix) {
    $NameTeamPrefix = $TeamNum
}

$leaderUsername = "CTF-$NameTeamPrefix-$LeaderName"
$followerUsername = "CTF-$NameTeamPrefix-$FollowerName"

$botSpecs = @(
    @{
        Label = "root-attacker-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$FollowerName",
            "--username", "$followerUsername",
            "--against", "$AgainstTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "root-attacker-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$LeaderName",
            "--username", "$leaderUsername",
            "--against", "$AgainstTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = $LeaderStartupDelaySeconds
    }
)

foreach ($bot in $botSpecs) {
    $commandPreview = "python " + (($bot.Arguments | ForEach-Object {
        if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
    }) -join " ")
    Write-Host ("[{0}] {1}" -f $bot.Label, $commandPreview)
}

Write-Host ("Using random team number: {0}" -f $TeamNum)
Write-Host ("Using bot names: leader={0}, follower={1}" -f $leaderUsername, $followerUsername)
Write-Host ("Intent chat: with {0} {1} {2}" -f $AgainstTeam, $PerTeamPlayer, $MapMode)

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
        -WorkingDirectory $scriptDir `
        -PassThru
}

if (-not $Wait) {
    Write-Host "Started random 2-player team. Use -Wait to block until the bots exit."
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