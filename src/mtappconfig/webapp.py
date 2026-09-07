"""The Flask application every sample shares.

Only the source differs between the samples, so the routes, the tenant
validation, and the caching all live here exactly once.
"""

from __future__ import annotations

from flask import Flask, abort, jsonify, render_template_string

from .cache import TenantConfigCache
from .observability import get_logger
from .source import ConfigStoreUnavailableError, TenantConfigSource
from .tenants import TenantRegistry, UnknownTenantError

_AUTHZ_DISCLAIMER = (
    "This app is a demo that lets anyone browse every tenant's sample "
    "settings without signing in. It does not implement user "
    "authentication or tenant-membership authorization; a real "
    "application must determine the caller's tenant from an "
    "authenticated context before serving its configuration."
)

_INDEX_TEMPLATE = """
<!doctype html>
<title>Multitenant App Configuration sample</title>
<h1>Multitenant App Configuration sample</h1>
<p>Pattern: <strong>{{ pattern }}</strong></p>
<p><small>{{ authz_disclaimer }}</small></p>
<h2>Tenants</h2>
<ul>
{% for tenant in tenants %}
  <li><a href="/t/{{ tenant.tenant_id }}/">{{ tenant.display_name }}</a>
      <code>{{ tenant.tenant_id }}</code></li>
{% endfor %}
</ul>
<p><a href="/_diagnostics/cache">Cache diagnostics</a></p>
"""

_TENANT_TEMPLATE = """
<!doctype html>
<title>{{ tenant.display_name }}</title>
<h1>{{ tenant.display_name }}</h1>
<p>Pattern: <strong>{{ pattern }}</strong> &middot;
   Tenant: <code>{{ tenant.tenant_id }}</code></p>
<p><small>{{ authz_disclaimer }}</small></p>
<table border="1" cellpadding="6">
  <tr><th>Key</th><th>Value</th></tr>
{% for key, value in values.items() %}
  <tr><td><code>{{ key }}</code></td><td>{{ value }}</td></tr>
{% endfor %}
</table>
<p><a href="/t/{{ tenant.tenant_id }}/api/config">JSON</a> &middot;
   <a href="/">All tenants</a></p>
"""


_STORE_UNAVAILABLE_DETAIL = "the configuration store is temporarily unavailable"
_STORE_UNAVAILABLE_RETRY_AFTER_SECONDS = "30"


def create_app(
    *,
    source: TenantConfigSource,
    registry: TenantRegistry,
    pattern_name: str,
    cache: TenantConfigCache | None = None,
) -> Flask:
    app = Flask(__name__)
    config_cache = cache if cache is not None else TenantConfigCache(source)
    logger = get_logger(__name__)

    def _store_unavailable_response(error: ConfigStoreUnavailableError):
        """503, not the caller's exception text.

        The real error can name the endpoint and, via DefaultAzureCredential,
        enumerate every credential in the chain with tenant ids, client ids
        and token endpoints. That is fine in a log line; it must never reach
        an unauthenticated HTTP response. The detail is logged here, once,
        and the body stays generic.
        """
        logger.warning(
            "configuration store unavailable",
            extra={"event": "config.store.unavailable", "pattern": pattern_name},
            exc_info=error,
        )
        response = jsonify({"status": "unavailable", "detail": _STORE_UNAVAILABLE_DETAIL})
        response.status_code = 503
        response.headers["Retry-After"] = _STORE_UNAVAILABLE_RETRY_AFTER_SECONDS
        return response

    @app.errorhandler(ConfigStoreUnavailableError)
    def _handle_store_unavailable(error: ConfigStoreUnavailableError):
        # Catches a cold cache miss on an unreachable store: TenantConfigCache
        # only swallows failures on a *refresh* of an already-cached entry, so
        # a miss (first request, or one that just aged out of the TTL) lets
        # this propagate out of the view. Without this handler that becomes a
        # bare 500 — and under `flask run --debug`, the Werkzeug interactive
        # debugger, on an endpoint reachable pre-authentication.
        return _store_unavailable_response(error)

    def _resolve(tenant_id: str):
        """Validate before anything reaches the configuration store."""
        try:
            return registry.resolve(tenant_id)
        except UnknownTenantError:
            logger.warning(
                "rejected tenant id",
                extra={"event": "tenant.rejected", "pattern": pattern_name},
            )
            abort(404)

    @app.get("/")
    def index():
        return render_template_string(
            _INDEX_TEMPLATE,
            tenants=list(registry),
            pattern=pattern_name,
            authz_disclaimer=_AUTHZ_DISCLAIMER,
        )

    @app.get("/t/<tenant_id>/")
    def tenant_page(tenant_id: str):
        tenant = _resolve(tenant_id)
        config = config_cache.get(tenant.tenant_id)
        return render_template_string(
            _TENANT_TEMPLATE,
            tenant=tenant,
            values=dict(config.values),
            pattern=pattern_name,
            authz_disclaimer=_AUTHZ_DISCLAIMER,
        )

    @app.get("/t/<tenant_id>/api/config")
    def tenant_config(tenant_id: str):
        tenant = _resolve(tenant_id)
        config = config_cache.get(tenant.tenant_id)
        logger.info(
            "served tenant config",
            extra={
                "tenant_id": tenant.tenant_id,
                "event": "config.served",
                "pattern": pattern_name,
            },
        )
        return jsonify(
            {
                "tenant_id": tenant.tenant_id,
                "display_name": tenant.display_name,
                "pattern": pattern_name,
                "values": dict(config.values),
            }
        )

    @app.get("/healthz")
    def healthz():
        """Liveness. Deliberately does not touch the configuration store."""
        return jsonify({"status": "ok"})

    @app.get("/readyz")
    def readyz():
        try:
            source.ping()
        except ConfigStoreUnavailableError as error:
            return _store_unavailable_response(error)
        return jsonify({"status": "ready"})

    @app.get("/_diagnostics/cache")
    def cache_diagnostics():
        return jsonify(config_cache.snapshot())

    return app
