# PyInstaller runtime hook — ensure freeze_support early
import multiprocessing
multiprocessing.freeze_support()
