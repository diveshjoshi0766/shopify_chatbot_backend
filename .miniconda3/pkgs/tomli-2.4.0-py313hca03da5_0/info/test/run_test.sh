

set -ex



pip check
python -c "from importlib.metadata import version; assert(version('tomli')=='2.4.0')"
pytest -v tests
exit 0
