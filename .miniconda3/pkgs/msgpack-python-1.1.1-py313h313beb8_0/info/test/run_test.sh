

set -ex



pip check
python -c "from importlib.metadata import version; assert(version('msgpack')=='1.1.1')"
pytest -v test
exit 0
