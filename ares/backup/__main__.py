"""``python -m ares.backup`` gestisce gli snapshot locali."""

import sys

from ares.backup.snapshots import main

sys.exit(main())
