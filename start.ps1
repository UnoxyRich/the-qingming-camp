$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$TEAM_NUM = if ($env:TEAM_NUM) { $env:TEAM_NUM } else { 7891114514 }
$AGAINST_TEAM = if ($env:AGAINST_TEAM) { $env:AGAINST_TEAM } else { "none" }
$PER_TEAM_PLAYER = if ($env:PER_TEAM_PLAYER) { $env:PER_TEAM_PLAYER } else { 2 }
$MAP_MODE = if ($env:MAP_MODE) { $env:MAP_MODE } else { "fixed" }

$bots = @(
    @{ Name = "UnoxyRich"; No = 1 },
    @{ Name = "TennisBall"; No = 2 }
)

$processes = foreach ($bot in $bots) {
    Start-Process `
        -FilePath "python" `
        -ArgumentList @(
            "main.py",
            "--my-team", "$TEAM_NUM",
            "--my-no", "$($bot.No)",
            "--username", "$($bot.Name)",
            "--against", "$AGAINST_TEAM",
            "--per-team-player", "$PER_TEAM_PLAYER",
            "--map", "$MAP_MODE",
            "--strategy", "ctf_strategy.AttackerStrategy",
            "--verbose"
        ) `
        -WorkingDirectory $scriptDir `
        -NoNewWindow `
        -PassThru
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
