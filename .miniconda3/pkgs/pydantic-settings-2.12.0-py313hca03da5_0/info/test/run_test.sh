

set -ex



pip check
pytest -v tests --ignore=tests/test_docs.py --ignore=tests/test_source_cli.py
exit 0
