"""python -m tko.gui"""

import multiprocessing

from tko.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
