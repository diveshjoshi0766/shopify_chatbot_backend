

set -ex



pip check
python -c "from importlib.metadata import version; assert(version('tomlkit')=='0.13.3')"
pytest -ra -v --tb=short tests
exit 0
