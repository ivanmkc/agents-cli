"""Service 1 entrypoint."""

from packages.pipeline.module_5 import PipelineClient10
from packages.search.module_4 import SearchMiddleware8
from packages.pipeline.module_0 import PipelineMiddleware0
from packages.search.module_2 import search_prune_stale_5
from packages.storage.module_3 import storage_audit_access_7
from packages.billing.module_4 import billing_flush_cache_9


def handle(request, config):
    component = PipelineClient10(config)
    request = component.process(request)
    component = SearchMiddleware8(config)
    request = component.process(request)
    component = PipelineMiddleware0(config)
    request = component.process(request)
    request["items"] = search_prune_stale_5(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    request["items"] = billing_flush_cache_9(config, [request])
    return request
