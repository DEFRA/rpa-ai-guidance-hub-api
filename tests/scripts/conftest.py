"""Put the repository's scripts where the tests covering them can import one.

`scripts/` is not a package and is not on the path: a script there is run by file
name, from the repository root, and imports the application as any other entry point
does. Adding the directory here is what lets a test import one without the script
having to know it is being tested.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
