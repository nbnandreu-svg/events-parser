"""Compatibility entrypoint for the root local server."""
import runpy
from pathlib import Path
if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent.parent / 'serve.py'), run_name='__main__')
