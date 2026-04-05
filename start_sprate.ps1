param(
    [string]$TeamNum = "7891114514",
    [ValidateSet("none", "random")]
    [string]$AgainstTeam = "random",
    [int]$PerTeamPlayer = 2,
    [ValidateSet("fixed", "random")]
    [string]$MapMode = "random",
    [string]$NameTeamPrefix = "",
    [string]$LeaderName = "",
    [string]$FollowerName = "",
    [int]$LeaderStartupDelaySeconds = 5,
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
        Label = "partner-defender"
        WorkingDirectory = $partnerDir
        Arguments = @(
            "main.py",
            "--my-team", "$TeamNum",
            "--my-no", "$FollowerName",
            "--username", "$followerUsername",
            "--against", "$AgainstTeam",
            "--per-team-player", "$PerTeamPlayer",
            "--map", "$MapMode",
            "--strategy", "ctf_strategy.DefenderStrategy",
            "--verbose"
        )
        DelayBeforeStartSeconds = 0
    },
    @{
        Label = "root-attacker"
        WorkingDirectory = $rootDir
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

Write-Host ("Using bot names: leader={0}, follower={1}" -f $leaderUsername, $followerUsername)
Write-Host ("Using visible team prefix: {0}" -f $NameTeamPrefix)

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
    Write-Host "Started separate test bots. Use -Wait to block until they exit."
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