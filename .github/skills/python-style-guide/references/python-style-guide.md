---
layout: default
title: Python Style Guide
---

# Python Style Guide

This page outlines the style guide / coding conventions for Python projects within the Defra AICE team.

> [!NOTE]
> This style guide is written with the assumption that you are developing a Python project that will eventually deployed into a production environment. All code expected to be deployed into production should adhere to this style guide.
>
> However, the AICE team also uses Python for data science type work, where it is expected that the opinions in this style guide may not always be applicable. As a general rule of thumb, all our Python code should adhere to this style guide unless there is a very good reason not to.

## Table of Contents

- [1 Python Project Rules](#1-python-project-rules)
  - [1.1 Linting / Formatting](#11-linting--formatting)
  - [1.2 Project Management](#12-project-management)
    - [1.2.1 pyproject.toml](#121-pyprojecttoml)
    - [1.2.2 Packaging](#122-packaging)
    - [1.2.3 Project Structure](#123-project-structure)
  - [1.3 Testing](#13-testing)
  - [1.4 Dependency Management](#14-dependency-management)
- [2 Python Style Guide](#2-python-style-guide)
  - [2.1 Modules](#21-modules)
    - [2.1.1 Usage of `__init__.py`](#211-usage-of-__init__py)
    - [2.1.2 Imports](#212-imports)
    - [2.1.2 Relative Imports](#212-relative-imports)
  - [2.2 Naming Conventions](#22-naming-conventions)
  - [2.3 Type hints](#23-type-hints)
    - [2.3.1 MyPy Configuration](#231-mypy-configuration)
    - [2.3.2 Type Annotation Guidelines](#232-type-annotation-guidelines)
    - [2.3.3 Type Imports](#233-type-imports)
    - [2.3.4 Third-party Library Integration](#234-third-party-library-integration)
- [Contributions](#contributions)

## 1 Python Project Rules

### 1.1 Linting / Formatting
All source files must be linted and formatted using [Ruff](https://docs.astral.sh/ruff/).

### 1.2 Project Management
We use [uv](https://docs.astral.sh/uv/) to manage our Python projects. All new Python projects should be created using `uv` and should follow the standard project structure created by `uv`.

#### 1.2.1 pyproject.toml
All Python projects must use a `pyproject.toml` file to manage project dependencies and settings. `uv` will automatically create this file when you create a new project using `uv init`.

#### 1.2.2 Packaging
All Python projects must be packaged as an installable package even if the project is not intended to be published to a package index like PyPI. This includes setting up the necessary metadata in the `pyproject.toml` file. See [uv packaging](https://docs.astral.sh/uv/concepts/projects/config/#build-systems) for more information.

#### 1.2.3 Project Structure
A standard AICE Python project should have the following basic structure:
```
my_project/
├── app/
│   └── entrypoints/
│       └── fastapi.py
├── tests/
│   └── test_example.py
├── pyproject.toml
├── README.md
└── .ruff.toml
```

Where:
- `app/`: This directory contains the main application code. This is what will be packaged and installed when the project is installed as a package.
- `app/entrypoints/`: This subdirectory contains the entry points for the application, such as FastAPI app definitions or CLI commands. Each of these entrypoints should be exposed via the script section of the `pyproject.toml`.
- `tests/`: This directory contains all the test files for the project structure.

### 1.3 Testing
We use [pytest](https://docs.pytest.org/en/stable/) as our testing framework for Python projects. All tests should be placed in a dedicated `tests` folder at the root of the project. Each test file should be prefixed with `test_` and each test function within those files should also be prefixed with `test_`.

Test files should not be placed in the same directories as the source code files (`src/` or equivalent).

### 1.4 Dependency Management
All project dependencies must be managed using the `pyproject.toml` file. Use `uv` commands to add, update, or remove dependencies to ensure that the `pyproject.toml` file is kept up to date.

Ensure that you pin dependencies to specific versions to avoid unexpected issues due to version changes. Use exact version specifiers (`==`) rather than range specifiers (`>=`, `<=`, etc.) unless there is a specific reason to allow for version ranges.

```toml
# Do this
name = "my_project"
version = "0.1.0"
dependencies = [
    "requests==2.25.1",
    "fastapi==0.70.0",
]

# Not this
name = "my_project"
version = "0.1.0"
dependencies = [
    "requests>=2.25.1",
    "fastapi>=0.70.0",
]
```

You must also routinely scan and update dependencies to ensure that they are up to date and do not contain any known security vulnerabilities.

#### 1.4.1 Dependency Updates
Do not update to the latest cutting edge version of a dependency unless absolutely necessary (e.g. to fix a critical security vulnerability). Always prefer updating to a stable version that has been available for a reasonable amount of time.

All projects must use the `exclude-newer` option in `uv` to prevent newly released versions from being automatically considered. This must be set in `pyproject.toml` to at least 1 week:

```toml
[tool.uv]
exclude-newer = "1 week"
```

## 2 Python Style Guide

### 2.1 Modules

#### 2.1.1 Usage of `__init__.py`
By default, our python code should use **namespace packages** which means we do not include `__init__.py` files in our package directories.

#### 2.1.2 Imports
`import` statements should only be used for importing modules or packages. Avoid using `import` statements to import individual classes, functions, or variables from modules.

By default, you should use either `import my_package.module` or `from my_package import module` to import modules.

You can use aliasing for module imports where appropriate to avoid name clashes or improve code readability.

Do this:
```python
import my_package.router
from my_package import router as another_router
```

Not this:
```python
from my_package.router import MyRouter
from my_package.another_router import MyRouter as AnotherRouter
```

#### 2.1.2 Relative Imports
Do not use relative names in imports. Always use absolute imports using the full package name.

```python
# Do this
from my_package.subpackage import my_module

# Not this
from .subpackage import my_module
```

### 2.2 Naming Conventions

All names should be descriptive and follow the conventions outlined in [PEP 8](https://peps.python.org/pep-0008/#naming-conventions).

See the following guidelines:

| Type | Public | Private |
|------|--------|---------|
|Packages|`snake_case`|N/A|
|Modules|`snake_case`|N/A|
|Classes|`PascalCase`|N/A|
|Functions / class methods|`snake_case`|`_snake_case`|
|Constants|`UPPER_SNAKE_CASE`|N/A|
|Local / instance variables|`snake_case`|`_snake_case`|


### 2.3 Type hints

All production Python code must use type hints and be verified with MyPy.
Type hints improve code readability, help catch bugs early, and enable better IDE support.

#### 2.3.1 MyPy Configuration

Configure MyPy in your `pyproject.toml` file with strict settings:

```toml
[tool.mypy]
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
explicit_package_bases = true
```

#### 2.3.2 Type Annotation Guidelines

**Use Modern Union Syntax**: Use the `|` operator for unions (Python 3.10+):
```python
# Do this
username: str | None = None
config: dict[str, Any] | None = None

# Not this
username: Optional[str] = None
config: Optional[Dict[str, Any]] = None
```

**Function Type Annotations**: All functions must have parameter and return type annotations:
```python
def process_data(data: str, timeout: int = 30) -> dict[str, Any]:
    return {"result": data, "timeout": timeout}

async def fetch_user(user_id: str) -> dict[str, str]:
    return {"id": user_id, "name": "John"}
```

**Use Built-in Generics**: Use built-in generic types instead of importing from `typing` (Python 3.9+):
```python
# Do this
user_cache: dict[str, Any] = {}
active_sessions: list[str] = []

# Not this
from typing import Dict, List, Any
user_cache: Dict[str, Any] = {}
active_sessions: List[str] = []
```

**Variable Type Annotations**: Add type annotations for module-level variables and class attributes:
```python
client: AsyncMongoClient | None = None
ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id")
```

#### 2.3.3 Type Imports

Import types from their modern locations:
```python
# Prefer collections.abc for abstract base classes
from collections.abc import AsyncGenerator, Callable, Awaitable

# Use typing only when necessary
from typing import Any
```

#### 2.3.4 Third-party Library Integration

For libraries without type stubs, use MyPy overrides rather than inline ignore comments:
```toml
[[tool.mypy.overrides]]
module = "pymongo.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "boto3.*"
ignore_missing_imports = true
```

## Contributions
If you would like to contribute to this style guide, please open a pull request on the Defra AICE Team GitHub (https://github.com/DEFRA/aice-team) repository.

For anything that is not covered by this style guide, we recommend following the [GDS Python style guide](https://gds-way.digital.cabinet-office.gov.uk/manuals/programming-languages/python/python.html) and staying consistent with the existing codebase. If alignment across AICE is required, please raise an issue in [Defra AICE Team GitHub](https://github.com/DEFRA/aice-team/issues).
