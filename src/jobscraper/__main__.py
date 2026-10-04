"""Allow `python -m jobscraper`."""

import sys

from jobscraper.cli import main

if __name__ == "__main__":
    sys.exit(main())
