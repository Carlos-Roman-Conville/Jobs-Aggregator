"""Back-compat forwarder — use make_application.py."""
from make_application import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
