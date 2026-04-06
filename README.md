# CTF Bot Launchers

This repo now has three maintained strategy launcher families and one archive folder for older PowerShell scripts.

## Folders

- [old scripts](old%20scripts): archived PowerShell launchers that were moved out of the repo root.
- [hybrid_strategy_scripts](hybrid_strategy_scripts): launchers for `hybrid_strategy.HybridStrategy`.
- [pressure_strategy_scripts](pressure_strategy_scripts): launchers for `pressure_strategy.PressureStrategy`.
- [safe_strategy_scripts](safe_strategy_scripts): launchers for `safe_strategy.SafeStrategy`.

## Strategies

- [hybrid_strategy.py](hybrid_strategy.py): direct, simple, immediate-movement strategy.
- [pressure_strategy.py](pressure_strategy.py): utility-scored strategy that evaluates multiple candidate objectives every tick and picks the highest-value move.
- [safe_strategy.py](safe_strategy.py): conservative strategy that plays defense-first and only commits to low-risk captures.

## Script Sets

Each maintained strategy has the same three entry points in both PowerShell and Bash:

- `*_randomMap`: random opponent, random map.
- `*_fixedMap`: fixed map, prompts for or accepts a specific opponent team.
- `*_2v2`: launches a 2v2 match between team `26` and team `27`.

## Examples

PowerShell:

```powershell
./hybrid_strategy_scripts/hybrid_strategy_randomMap.ps1 -Wait
./hybrid_strategy_scripts/hybrid_strategy_fixedMap.ps1 -AgainstTeam 27 -Wait
./pressure_strategy_scripts/pressure_strategy_2v2.ps1 -MapMode fixed -Wait
./safe_strategy_scripts/safe_strategy_randomMap.ps1 -Wait
```

Bash:

```bash
./hybrid_strategy_scripts/hybrid_strategy_randomMap.sh --wait
./hybrid_strategy_scripts/hybrid_strategy_fixedMap.sh --against-team 27 --wait
./pressure_strategy_scripts/pressure_strategy_2v2.sh --map-mode fixed --wait
./safe_strategy_scripts/safe_strategy_fixedMap.sh --against-team 27 --wait
```

## Notes

- All maintained launchers run from the repo root and call `main.py` directly.
- The `.sh` files were written to mirror the launcher intent of the `.ps1` files, not to be a byte-for-byte flag clone of PowerShell syntax.
- PowerShell launchers were not syntax-checked locally because this machine does not currently have `pwsh` installed.