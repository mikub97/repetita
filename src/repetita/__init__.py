"""Repetita -- an open engine for learning a language from scratch."""

#: The one place the version is written. `pyproject.toml` reads it from here
#: (`[tool.hatch.version]`), because it lived in both and a release bumped one
#: of them -- so `repetita --version` and the version stamped into a feedback
#: file disagreed with the tag that was supposed to have set them.
__version__ = "0.2.0"

__all__ = ["__version__"]
