if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    import sys
    if '--quality-request' in sys.argv:
        from oi_eegqc.segment_service import main
        raise SystemExit(main())
    if '--capture-complete-request' in sys.argv:
        from oi_eegqc.capture_service import main
        raise SystemExit(main())
    try:
        from oi_eegqc.desktop import main
        raise SystemExit(main())
    except Exception:
        import os
        import traceback
        from pathlib import Path
        log=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Omni-Intelligence'/'EEGQC'/'logs'/'startup-error.log'
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(traceback.format_exc(),encoding='utf-8')
        raise
