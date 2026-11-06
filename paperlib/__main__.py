"""python -m paperlib 入口。"""
from __future__ import annotations

import sys

from paperlib.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
