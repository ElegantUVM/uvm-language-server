import sys

if len(sys.argv) > 1 and sys.argv[1] == "elab-worker":
    from .elab import worker as main
elif len(sys.argv) > 1 and sys.argv[1] in ("graph", "topology", "configdb", "lint", "stats", "elab"):
    from .cli import main
else:
    from .server import main

main()
