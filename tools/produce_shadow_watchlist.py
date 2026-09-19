"""Offline explicit M4-to-Shadow publication. Run with python -m tools.produce_shadow_watchlist."""
import argparse
import json
from pathlib import Path

from app.paper.shadow_producer import StrategyShadowRequest, produce_strategy_watchlist
from app.ui.shadow_input import _decode, _read_document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path, help='complete canonical StrategyShadowRequest JSON')
    parser.add_argument('directory', type=Path, help='new absolute isolated collection directory')
    args = parser.parse_args(argv)
    try:
        if any(p.is_symlink() for p in args.request.absolute().parents):
            raise ValueError('linked input parent')
        request = _decode(StrategyShadowRequest, _read_document(args.request))
        result = produce_strategy_watchlist(args.directory, request)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # Avoid echoing arbitrary evidence, filesystem details or source contents.
        parser.exit(1, 'Shadow collection rejected; existing records were not replaced.\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
