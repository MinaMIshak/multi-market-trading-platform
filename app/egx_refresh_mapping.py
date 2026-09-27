"""Explicit provider aliases; registration is not source or licensing review."""
from app.egx_scope import require_equity_identity


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
            or str(resolved.get('matched_alias_value', '')).strip().upper()
            != provider_symbol):
        raise ValueError('provider refresh alias mismatch')
