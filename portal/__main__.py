"""Allows `python -m portal <stage>`, the invocation form used throughout §5."""

import sys

from portal.cli import main

sys.exit(main())
