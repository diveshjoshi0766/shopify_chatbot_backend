

set -ex



pip check
tqdm --help
tqdm -v | rg 4.67.3
pytest -k "not tests_perf" tests/ -W ignore::FutureWarning  --deselect=tests/tests_pandas.py::test_pandas_leave
exit 0
