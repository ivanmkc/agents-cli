"""Service 2 entrypoint."""

from packages.auth.module_2 import auth_prune_stale_5
from packages.search.module_4 import SearchMiddleware8
from packages.notify.module_3 import notify_audit_access_7
from packages.pipeline.module_0 import PipelineMiddleware0
from packages.auth.module_4 import auth_flush_cache_9
from packages.storage.module_0 import StorageMiddleware0
from packages.auth.module_5 import AuthClient10
from packages.storage.module_2 import storage_prune_stale_5
from packages.search.module_0 import search_flush_cache_1


def handle(request, config):
    request["items"] = auth_prune_stale_5(config, [request])
    component = SearchMiddleware8(config)
    request = component.process(request)
    request["items"] = notify_audit_access_7(config, [request])
    component = PipelineMiddleware0(config)
    request = component.process(request)
    request["items"] = auth_flush_cache_9(config, [request])
    component = StorageMiddleware0(config)
    request = component.process(request)
    component = AuthClient10(config)
    request = component.process(request)
    request["items"] = storage_prune_stale_5(config, [request])
    request["items"] = search_flush_cache_1(config, [request])
    return request
