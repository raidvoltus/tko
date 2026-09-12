import multiprocessing
"""python -m tko.gui"""

from tko.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
