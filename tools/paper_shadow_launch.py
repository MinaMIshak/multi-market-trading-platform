"""Explicit local one-shot CLI. Status/verify/publish never construct a provider."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from app.data.daily_refresh_job import DailyRefreshJobError
from app.data.daily_refresh_admission import DailyRefreshAdmissionError
from app.data.quota import VerifiedQuotaCost
from app.paper.swing_launch import LaunchBlocked, SwingLaunchInput, refresh_once, run_signal
from app.storage import Database
from app.storage.database import SCHEMA_VERSION
from app.ui.shadow_input import _decode, _read_document


ROOTS = (Path('/home/egx-agent/research-data'),
         Path('/home/egx-agent/work/egx-trading-platform-us'), Path('/tmp'))
VARIABLES = ('EGX_DB_PATH', 'EGX_DATA_ROOT', 'EGX_SHADOW_DIRECTORY', 'EODHD_API_TOKEN_FILE')


def local_path(value):
    path = Path(value)
    # Reject out-of-bound paths lexically BEFORE any filesystem inspection.
    if (not path.is_absolute() or '..' in path.parts
            or not any(path.is_relative_to(root) and path != root for root in ROOTS)):
        raise LaunchBlocked('CONFIG_MISSING', 'explicit isolated local path required')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise LaunchBlocked('CONFIG_MISSING', 'unlinked local path required')
    return path


def configuration_status():
    result = {name: 'SET' if os.environ.get(name, '').strip() else 'MISSING' for name in VARIABLES}
    result.update(database_initialized='UNKNOWN', security_master='UNKNOWN',
                  canonical_history='UNKNOWN', calendar_session_evidence='UNKNOWN')
    for name in VARIABLES:
        if result[name] != 'SET':
            continue
        try:
            path = local_path(os.environ[name])
            if name == 'EGX_DB_PATH':
                if not path.is_file():
                    result[name] = 'MISSING'
                    continue
                with closing(sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True)) as con:
                    version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
                    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    required = {'daily_canonical_artifacts', 'daily_canonical_sources', 'data_ingestions',
                                'reference_artifacts', 'automatic_quota', 'canonical_instruments', 'instrument_aliases'}
                    result['database_initialized'] = 'SET' if version and int(version[0]) == SCHEMA_VERSION and required <= tables else 'MISSING'
                    for label, table in [('security_master', 'canonical_instruments'),
                                         ('canonical_history', 'daily_canonical_artifacts')]:
                        result[label] = 'SET' if con.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() else 'MISSING'
            elif name == 'EGX_DATA_ROOT':
                if not path.is_dir():
                    result[name] = 'MISSING'
            elif name == 'EGX_SHADOW_DIRECTORY':
                if not path.parent.is_dir():
                    result[name] = 'MISSING'
            else:
                info = path.stat()  # Presence/permissions only; never read a token in status.
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not os.access(path, os.R_OK):
                    result[name] = 'MISSING'
        except (OSError, ValueError, sqlite3.Error):
            result[name] = 'MISSING'
    return result


def require_configuration(*, refresh=False):
    state = configuration_status()
    required = ['EGX_DB_PATH', 'EGX_DATA_ROOT', 'EGX_SHADOW_DIRECTORY', 'database_initialized']
    if refresh:
        required.append('EODHD_API_TOKEN_FILE')
    if any(state[key] != 'SET' for key in required):
        raise LaunchBlocked('CONFIG_MISSING', 'required local configuration unavailable; run status')
    root = local_path(os.environ['EGX_DATA_ROOT'])
    local_path(str(root / 'raw'))
    local_path(str(root / 'canonical'))
    return Database(local_path(os.environ['EGX_DB_PATH'])), root


def _operator_token():
    # Only called by an explicitly authorized refresh command, never by status/tests.
    path = local_path(os.environ['EODHD_API_TOKEN_FILE'])
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 0 < info.st_size <= 4096:
            raise LaunchBlocked('CONFIG_MISSING', 'private bounded token file required')
        token = stream.read(4097).decode().strip()
    if not token or len(token) > 4096:
        raise LaunchBlocked('CONFIG_MISSING', 'token unavailable')
    return token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('status', 'schema', 'import-reference', 'refresh', 'verify', 'publish', 'ui'))
    parser.add_argument('--input', type=Path, help='reviewed SwingLaunchInput JSON; no Python objects needed')
    parser.add_argument('--authorize-provider-call', action='store_true')
    parser.add_argument('--quota-units', type=int)
    parser.add_argument('--quota-evidence', help='nonsecret verified per-invocation quota-cost reference')
    parser.add_argument('--provider', help='actual reference evidence provider identity')
    parser.add_argument('--provider-symbol', help='explicit registered EODHD alias for refresh only; no ticker inference')
    parser.add_argument('--source-uri', help='nonsecret original reference evidence locator')
    args = parser.parse_args(argv)
    try:
        if args.provider_symbol is not None and args.operation != 'refresh':
            raise LaunchBlocked('CONFIG_MISSING', '--provider-symbol is only valid for refresh')
        if args.operation == 'schema':
            print(json.dumps(SwingLaunchInput.model_json_schema(), sort_keys=True))
            return 0
        if args.operation == 'status':
            state = configuration_status()
            required = ('EGX_DB_PATH', 'EGX_DATA_ROOT', 'EGX_SHADOW_DIRECTORY', 'database_initialized')
            result = {'status': 'CONFIG_MISSING' if any(state[k] != 'SET' for k in required) else 'NOT_RUN',
                      'configuration': state, 'market_data': 'UNKNOWN', 'live': 'DISABLED',
                      'mode': 'SHADOW', 'empirical_status': 'NOT VALIDATED'}
        elif args.operation == 'ui':
            from app.ui.shadow_input import read_shadow_input
            from app.ui.shadow import load_shadow_watchlist
            if not os.environ.get('EGX_SHADOW_DIRECTORY'):
                raise LaunchBlocked('CONFIG_MISSING', 'EGX_SHADOW_DIRECTORY required')
            directory = local_path(os.environ['EGX_SHADOW_DIRECTORY'])
            state = load_shadow_watchlist(directory, read_shadow_input(directory))
            if not state['available']:
                raise LaunchBlocked('EVIDENCE_BLOCKED', 'existing UI collection audit failed')
            result = {'status': 'NOT_RUN', 'operation': 'UI_COLLECTION_AUDITED', 'available': True,
                      'execution_status': 'NO EXECUTION INFERENCE'}
        else:
            database, root = require_configuration(refresh=args.operation == 'refresh')
            if args.input is None:
                raise LaunchBlocked('CONFIG_MISSING', '--input required')
            input_path = local_path(str(args.input))
            if not input_path.is_file():
                raise LaunchBlocked('CONFIG_MISSING', 'reviewed launch input file missing')
            if args.operation == 'import-reference':
                from app.data.raw_store import ImmutableRawStore
                from app.storage.reference_repository import ReferenceRepository, strict_json
                from app.ui.shadow_input import MAX_INPUT_BYTES
                if not args.provider or not args.source_uri:
                    raise LaunchBlocked('CONFIG_MISSING', '--provider and --source-uri required')
                descriptor = os.open(input_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError('regular reference file required')
                    payload = stream.read(MAX_INPUT_BYTES + 1)
                if len(payload) > MAX_INPUT_BYTES:
                    raise ValueError('bounded reference file required')
                document = strict_json(payload)
                if not isinstance(document, dict) or 'contract' not in document:
                    raise ValueError('canonical reference contract required')
                manifest = ReferenceRepository(database, ImmutableRawStore(root / 'raw')).ingest(
                    provider=args.provider, payload=payload, source_uri=args.source_uri,
                    contract=document['contract'])
                result = {'status': 'NOT_RUN', 'operation': 'REFERENCE_IMPORTED_SIGNAL_NOT_RUN',
                          'ingestion_id': str(manifest.ingestion_id), 'sha256': manifest.sha256}
                print(json.dumps(result, sort_keys=True))
                return 0
            source = _decode(SwingLaunchInput, _read_document(input_path))
            if args.operation == 'refresh':
                if not args.authorize_provider_call:
                    raise LaunchBlocked('CONFIG_MISSING', 'explicit --authorize-provider-call required')
                if not args.quota_units or args.quota_units < 1 or not args.quota_evidence:
                    raise LaunchBlocked('EVIDENCE_BLOCKED', 'verified --quota-units and --quota-evidence required')
                # Calendar/identity preflight before token access; refresh repeats at execution.
                from app.paper.swing_launch import admit_calendar, _identity, _now
                admit_calendar(source, _now())
                _identity(database, source)
                from app.egx_refresh_mapping import configured_refresh_mapping
                from app.storage.security_master_repository import SecurityMasterRepository
                mapping = configured_refresh_mapping(
                    SecurityMasterRepository(database), provider_symbol=args.provider_symbol,
                    provider_name='eodhd', symbol=source.symbol, instrument_id=source.instrument_id,
                )
                from app.data.daily_refresh_job import DailyRefreshTarget
                target = DailyRefreshTarget(**mapping) if mapping is not None else None
                result = refresh_once(database, root, source,
                                      cost=VerifiedQuotaCost(args.quota_units, args.quota_evidence),
                                      api_token=_operator_token(), target=target)
            else:
                result = run_signal(database, root, source,
                                    local_path(os.environ['EGX_SHADOW_DIRECTORY']),
                                    publish=args.operation == 'publish')
        print(json.dumps(result, sort_keys=True))
        return 2 if result['status'] == 'CONFIG_MISSING' else 0
    except LaunchBlocked as exc:
        result = {'status': exc.status, 'reason': exc.reason}
    except DailyRefreshJobError as exc:
        cause = exc.__cause__
        if isinstance(cause, DailyRefreshAdmissionError):
            message = str(cause)
            status = ('DATA_STALE' if 'stale' in message else 'DATA_INSUFFICIENT'
                      if 'history' in message else 'EVIDENCE_BLOCKED')
        else:
            status = 'EVIDENCE_BLOCKED'
        result = {'status': status, 'reason': 'refresh rejected; no signal published'}
    except (ValueError, FileNotFoundError, FileExistsError, PermissionError):
        result = {'status': 'EVIDENCE_BLOCKED', 'reason': 'canonical input or storage admission rejected'}
    except Exception:
        # Never print arbitrary provider/configuration exception values or tracebacks.
        result = {'status': 'SOFTWARE_ERROR', 'reason': 'unexpected implementation failure'}
    print(json.dumps(result, sort_keys=True))
    return 2 if result['status'] != 'SOFTWARE_ERROR' else 1


if __name__ == '__main__':
    raise SystemExit(main())
