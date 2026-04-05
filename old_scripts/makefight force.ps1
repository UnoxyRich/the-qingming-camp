param(
    [string]$TeamNum = "",
    [string]$AgainstTeam = "",
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [double]$ActionTickSeconds = 0.05,
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

function New-RandomMemberTag {
    param([string[]]$Exclude = @())

    $letters = @("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")
    $choices = $letters | Where-Object { $_ -notin $Exclude }
    return Get-Random -InputObject $choices
}

function New-RandomTeamNumber {
    return "$(Get-Random -Minimum 100 -Maximum 1000)"
}

function Test-TeamNumber {
    param([string]$Value)

    return $Value -match '^[0-9]+$'
}

if (-not $AgainstTeam) {
    $AgainstTeam = Read-Host "Enter enemy team number"
}

if (-not (Test-TeamNumber -Value $AgainstTeam)) {
    throw "Enemy team number must be numeric."
}

if (-not $TeamNum) {
    do {
        $TeamNum = New-RandomTeamNumber
    } while ($TeamNum -eq $AgainstTeam)
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

$strategyName = "collect_only_strategy.CollectOnlyStrategy"

$botSpecs = @(
    @{
        Label = "collect-2"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$FollowerName",
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
        Label = "collect-1"
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$LeaderName",
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

foreach ($bot in $botSpecs) {
    $commandPreview = "python " + (($bot.Arguments | ForEach-Object {
        if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
    }) -join " ")
    Write-Host ("[{0}] {1}" -f $bot.Label, $commandPreview)
}

Write-Host ("Using team number: {0}" -f $TeamNum)
Write-Host ("Using bot names: leader={0}, follower={1}" -f $leaderUsername, $followerUsername)
Write-Host ("Intent chat: with {0} {1} {2}" -f $AgainstTeam, $PerTeamPlayer, $MapMode)
Write-Host ("Path update tick: {0}s" -f $ActionTickSeconds)
Write-Host "Strategy mode: collect only"

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
    Write-Host "Started collect-only fight launcher. Use -Wait to block until the bots exit."
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