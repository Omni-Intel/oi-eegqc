"""Public entry point for the single supported QML desktop application."""


def main():
    from .quick import main as quick_main
    return quick_main()


if __name__ == "__main__":
    raise SystemExit(main())
