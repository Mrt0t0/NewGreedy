"""Test package: points every module at a throw-away data folder before any of them loads.

Run with `python3 -m unittest discover -s tests -t .` or `python3 -m pytest tests`.
"""
import os
import pathlib
import shutil
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = pathlib.Path(tempfile.mkdtemp(prefix="newgreedy-tests-"))
os.environ["NEWGREEDY_DATA_DIR"] = str(DATA)
for var in ("NEWGREEDY_LISTEN_HOST", "NEWGREEDY_WEB_HOST", "NEWGREEDY_DOCKER"):
    os.environ.pop(var, None)


def reset_data(**settings):
    """Empty the data folder and put back the shipped config.ini, with optional
    overrides given as section__key=value."""
    for p in DATA.iterdir():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    shutil.copy(ROOT / "config.ini", DATA / "config.ini")
    if settings:
        import config_schema as cs
        for name, value in settings.items():
            section, key = name.split("__", 1)
            cs.set_value(DATA / "config.ini", section, key, value)


reset_data()
