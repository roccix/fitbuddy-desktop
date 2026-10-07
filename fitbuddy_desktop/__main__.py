import sys


def main() -> int:
    if sys.argv[1:2] == ["serve"]:
        from .server import serve
        serve()
        return 0
    from .app import main as gui
    return gui()


if __name__ == "__main__":
    sys.exit(main())
