"""Service 11 entrypoint."""

from packages.pipeline.module_5 import PipelineClient10
from packages.billing.module_3 import billing_audit_access_7
from packages.pipeline.module_0 import PipelineMiddleware0
from packages.auth.module_2 import AuthResolver4
from packages.storage.module_4 import StorageMiddleware8
from packages.notify.module_3 import NotifyScheduler6
from packages.billing.module_1 import billing_emit_metrics_3
from packages.storage.module_3 import storage_audit_access_7
from packages.auth.module_5 import AuthClient10
from packages.storage.module_2 import storage_prune_stale_5
from packages.search.module_0 import search_flush_cache_1


def handle(request, config):
    component = PipelineClient10(config)
    request = component.process(request)
    request["items"] = billing_audit_access_7(config, [request])
    component = PipelineMiddleware0(config)
    request = component.process(request)
    component = AuthResolver4(config)
    request = component.process(request)
    component = StorageMiddleware8(config)
    request = component.process(request)
    component = NotifyScheduler6(config)
    request = component.process(request)
    request["items"] = billing_emit_metrics_3(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    component = AuthClient10(config)
    request = component.process(request)
    request["items"] = storage_prune_stale_5(config, [request])
    request["items"] = search_flush_cache_1(config, [request])
    return request
