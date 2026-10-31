from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py
ROOT=Path(__file__).resolve().parent
import sys
sys.path.insert(0,str(ROOT))
from build_sources import sources
class Build(build_py):
    def run(self):
        self.force=True
        for name,path in sources().items():
            target=Path(self.build_lib)/name
            target.parent.mkdir(parents=True,exist_ok=True)
            self.copy_file(str(path),str(target))
setup(name="tide-runtime-devkit",version="0.1.0a2",python_requires=">=3.11",
    description="Developer host and package scaffolding for the TIDE Runtime",
    license="AGPL-3.0-only",packages=["tide_dev"],cmdclass={"build_py":Build},
    entry_points={"console_scripts":["tide-dev=tide_dev.cli:main"]})
