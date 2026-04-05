$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$TEAM_NUM = 7891114514
$AGAINST_TEAM = 1
$PER_TEAM_PLAYER = 2
$MAP_MODE = "fixed"

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
        -WorkingDirectory $PSScriptRoot `
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
