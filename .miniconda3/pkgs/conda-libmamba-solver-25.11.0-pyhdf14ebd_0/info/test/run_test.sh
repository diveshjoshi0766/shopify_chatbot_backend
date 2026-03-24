

set -ex



python -c "from importlib.metadata import version; assert(version('conda-libmamba-solver')=='25.11.0')"
conda create --solver libmamba -n test --dry-run scipy
exit 0
