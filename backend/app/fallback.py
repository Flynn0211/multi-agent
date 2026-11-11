"""Quota failover policy, using only models exposed by the project catalog."""


def model_ref(selector):
    provider, name = selector.split('/', 1)
    name, separator, variant = name.partition('#')
    if not provider or not name or (separator and not variant):
        raise ValueError('Expected provider/model or provider/model#variant')
    return {'providerID': provider, 'id': name, **({'variant': variant} if variant else {})}


def model_id(ref):
    if not ref or not ref.get('providerID') or not ref.get('id'):
        return ''
    return f"{ref['providerID']}/{ref['id']}" + (f"#{ref['variant']}" if ref.get('variant') else '')


def quota_error(error):
    return error.get('status') in (402, 429) or error.get('type') in (
        'provider.quota', 'provider.rate_limit', 'provider.rate-limit')


def candidates(catalog, configured, attempted, failed, failure):
    available = {f"{m['providerID']}/{m['id']}": m for m in catalog
                 if m.get('enabled') and m.get('type', 'chat') == 'chat'
                 and m.get('capabilities', {}).get('tools')}
    selectors = list(dict.fromkeys(s.strip() for s in configured.split(',') if s.strip()))
    if not selectors:
        # Different billing pools first, then genuinely free models in the failed pool.
        selectors = sorted(available, key=lambda s: (s.split('/', 1)[0] == failed.get('providerID'), s))
    for selector in selectors:
        model = available.get(selector.split('#', 1)[0])
        if not model or selector in attempted or selector == model_id(failed):
            continue
        ref = model_ref(selector)
        if ref.get('variant') and ref['variant'] not in {v['id'] for v in model.get('variants', [])}:
            continue
        prices = model.get('cost', [])
        free = bool(prices) and all(p.get('input') == 0 and p.get('output') == 0 for p in prices)
        if failure.get('status') == 402 and ref['providerID'] == failed.get('providerID') and not free:
            continue
        yield ref
