"""Allow ``python -m csv_safe_restore``."""

import sys

from .cli import main

sys.exit(main())
