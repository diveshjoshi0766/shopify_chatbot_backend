

set -ex



pip check
pytest -v tests -k "not test_echo_via_pager"
exit 0
