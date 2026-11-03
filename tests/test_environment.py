import sys


def test_python_version():
    assert sys.version_info >= (3, 12)


def test_core_dependencies_import():
    import fastapi  # noqa: F401
    import jsonschema  # noqa: F401
    import pydantic  # noqa: F401
    import uvicorn  # noqa: F401
    import yaml  # noqa: F401
