

set -ex



pip check
pytest -vv -ra -m "not network" -o xfail_strict=False --timeout 300
exit 0
