import sys

if __name__ == '__main__':
    if '--self-test' in sys.argv:
        import argparse
        from pathlib import Path
        from clearguard.selftest import run
        parser = argparse.ArgumentParser()
        parser.add_argument('--self-test', action='store_true')
        parser.add_argument('--qa-root', required=True, type=Path)
        parser.add_argument('--report', required=True, type=Path)
        parser.add_argument('--qa-ready', type=Path)
        parser.add_argument('--qa-proceed', type=Path)
        args = parser.parse_args()
        try:
            run(args.qa_root, args.report, args.qa_ready, args.qa_proceed)
        except Exception:
            import json
            import traceback
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
            raise SystemExit(1)
    else:
        from clearguard.app import main
        main(smoke='--smoke-test' in sys.argv)
