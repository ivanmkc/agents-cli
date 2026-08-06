"""Service 0 entrypoint."""

from packages.billing.module_0 import billing_flush_cache_1
from packages.auth.module_2 import auth_prune_stale_5
from packages.search.module_3 import search_audit_access_7
from packages.search.module_2 import search_prune_stale_5
from packages.storage.module_3 import storage_audit_access_7
from packages.storage.module_2 import storage_prune_stale_5
from packages.billing.module_4 import billing_flush_cache_9


def handle(request, config):
    request["items"] = billing_flush_cache_1(config, [request])
    request["items"] = auth_prune_stale_5(config, [request])
    request["items"] = search_audit_access_7(config, [request])
    request["items"] = search_prune_stale_5(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    request["items"] = storage_prune_stale_5(config, [request])
    request["items"] = billing_flush_cache_9(config, [request])
    return request
