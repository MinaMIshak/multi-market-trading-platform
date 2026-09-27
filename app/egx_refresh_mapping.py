"""Explicit provider aliases; registration is not source or licensing review."""
from app.egx_scope import require_equity_identity


def select_refresh_targets(*, provider_name, targets, eodhd_defaults):
    """EODHD codes are defaults only for EODHD, never a generic universe.

    Explicit targets still require alias and evidence admission at execution.
    Selecting them does not certify source rights or dated membership.
    """
    if targets is not None:
        return targets
    if provider_name != 'eodhd':
        raise ValueError('explicit refresh targets required for non-EODHD provider')
    return eodhd_defaults


def configured_refresh_mapping(resolver, *, provider_symbol, provider_name, symbol, instrument_id):
    """Decode an optional explicit alias without guessing or normalizing identity."""
    if provider_symbol is None:
        return None
    require_refresh_mapping(resolver, canonical_symbol=symbol,
                            provider_symbol=provider_symbol, provider_name=provider_name,
                            symbol=symbol, instrument_id=instrument_id)
    return {'canonical_symbol': symbol, 'provider_symbol': provider_symbol}


def require_refresh_mapping(resolver, *, canonical_symbol, provider_symbol, provider_name, symbol, instrument_id):
    """Bind an explicit refresh target to the requested equity before any fetch."""
    if (canonical_symbol != symbol
            or not isinstance(provider_symbol, str) or not provider_symbol
            or provider_symbol != provider_symbol.strip().upper()
            or not isinstance(provider_name, str) or not provider_name.strip()
            or provider_name != provider_name.strip().lower()
            or provider_name == 'canonical'):
        raise ValueError('explicit provider refresh mapping required')
    try:
        resolved = resolver.resolve(provider_symbol, provider=provider_name)
    except (KeyError, ValueError) as exc:
        raise ValueError('provider refresh alias unavailable or ambiguous') from exc
    require_equity_identity(resolved, symbol=symbol, instrument_id=instrument_id)
    if (resolved.get('matched_provider') != provider_name
            or resolved.get('matched_alias_value') != provider_symbol):
        raise ValueError('provider refresh alias mismatch')


def require_refresh_targets(resolver, *, provider_name, targets):
    """Preflight the whole scope before any fetch; aliases do not prove rights."""
    for target in targets:
        try:
            identity = resolver.resolve(target.canonical_symbol, provider='canonical')
        except (KeyError, ValueError) as exc:
            raise ValueError('canonical refresh identity unavailable or ambiguous') from exc
        instrument_id = identity.get('instrument_id') if isinstance(identity, dict) else None
        require_equity_identity(identity, symbol=target.canonical_symbol,
                               instrument_id=instrument_id)
        require_refresh_mapping(
            resolver, canonical_symbol=target.canonical_symbol,
            provider_symbol=target.provider_symbol, provider_name=provider_name,
            symbol=target.canonical_symbol, instrument_id=instrument_id,
        )
