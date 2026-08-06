"""Service 4 entrypoint."""

from packages.auth.module_2 import auth_prune_stale_5
from packages.search.module_4 import SearchMiddleware8
from packages.billing.module_3 import BillingScheduler6
from packages.auth.module_4 import auth_flush_cache_9
from packages.storage.module_3 import storage_audit_access_7


def handle(request, config):
    request["items"] = auth_prune_stale_5(config, [request])
    component = SearchMiddleware8(config)
    request = component.process(request)
    component = BillingScheduler6(config)
    request = component.process(request)
    request["items"] = auth_flush_cache_9(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    return request
