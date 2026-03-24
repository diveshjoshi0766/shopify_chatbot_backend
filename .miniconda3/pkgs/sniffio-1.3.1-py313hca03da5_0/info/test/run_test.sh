

set -ex



pip check
python -c "from importlib.metadata import version; assert(version('sniffio')=='1.3.1')"
pytest --pyargs sniffio._tests
exit 0
