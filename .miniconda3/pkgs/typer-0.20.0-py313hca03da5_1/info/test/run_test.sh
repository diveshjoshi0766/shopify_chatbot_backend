

set -ex



pip check
typer --version
typer --help
cd typer && coverage run --source=typer --branch -m pytest -vv --color=yes --tb=long -k "not ((multiple_values and main) or completion or invalid_score)"
exit 0
