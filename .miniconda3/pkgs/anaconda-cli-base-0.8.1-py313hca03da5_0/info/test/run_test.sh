

set -ex



pip check
anaconda -h
anaconda -V
python -c "from anaconda_cli_base import __version__; assert __version__ == \"0.8.1\""
python -c "from importlib.metadata import version; assert(version('anaconda-cli-base')=='0.8.1')"
pytest -v tests
exit 0
