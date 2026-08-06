"""Service 9 entrypoint."""

from packages.pipeline.module_5 import PipelineClient10
from packages.search.module_3 import search_audit_access_7
from packages.storage.module_4 import StorageMiddleware8
from packages.billing.module_5 import BillingClient10
from packages.notify.module_3 import NotifyScheduler6
from packages.search.module_2 import search_prune_stale_5
from packages.storage.module_3 import storage_audit_access_7
from packages.storage.module_0 import StorageMiddleware0
from packages.notify.module_5 import notify_emit_metrics_11


def handle(request, config):
    component = PipelineClient10(config)
    request = component.process(request)
    request["items"] = search_audit_access_7(config, [request])
    component = StorageMiddleware8(config)
    request = component.process(request)
    component = BillingClient10(config)
    request = component.process(request)
    component = NotifyScheduler6(config)
    request = component.process(request)
    request["items"] = search_prune_stale_5(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    component = StorageMiddleware0(config)
    request = component.process(request)
    request["items"] = notify_emit_metrics_11(config, [request])
    return request
