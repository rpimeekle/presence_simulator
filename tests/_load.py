"""Load the HA-free modules without executing the package __init__ (which imports HA)."""
import importlib
import pathlib
import sys
import types

_DIR = pathlib.Path(__file__).parents[1] / "custom_components" / "presence_simulator"
_pkg = types.ModuleType("ps")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("ps", _pkg)

learner = importlib.import_module("ps.learner")
generator = importlib.import_module("ps.generator")
