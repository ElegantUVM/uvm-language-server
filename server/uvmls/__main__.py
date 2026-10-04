import sys

if len(sys.argv) > 1 and sys.argv[1] in ("graph", "topology", "configdb", "lint", "stats"):
    from .cli import main
else:
    from .server import main

main()
