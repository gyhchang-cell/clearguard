# Contributing

Use Python 3.11+ with Tk. Keep virtual environments and caches inside this repository. Run `python -m unittest discover -s tests -v` and `python tests/gui_smoke.py` before proposing a change. CI must pass on macOS and Windows.

Every change to cleanup must include a meaningful negative safety test. No permanent deletion fallback, automatic consent, default selection, whole-group deletion, system cleanup, or upload of personal paths. Use synthetic fixtures only. Explain platform-specific limitations and avoid claiming that file age proves disuse.

Open small issues/PRs with expected behavior, synthetic reproduction and validation results. The MIT license applies to contributions.
