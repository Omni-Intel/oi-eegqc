if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from oi_eegqc.desktop import main
    raise SystemExit(main())
