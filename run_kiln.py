"""Entry point for Kiln.

    python run_kiln.py [path-inside-a-repository]

This is also the script PyInstaller builds from (see kiln.spec). Keeping it as
a real file at the project root means the development command and the packaged
application start the same way.
"""

from __future__ import annotations

import sys

from kiln.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
