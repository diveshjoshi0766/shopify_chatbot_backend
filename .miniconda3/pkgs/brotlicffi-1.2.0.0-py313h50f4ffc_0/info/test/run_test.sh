

set -ex



pip check
python -c "from importlib.metadata import version; assert(version('brotlicffi')=='1.2.0.0')"
exit 0
