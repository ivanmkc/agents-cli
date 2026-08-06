"""Service 5 entrypoint."""

from packages.pipeline.module_5 import PipelineClient10
from packages.pipeline.module_0 import PipelineMiddleware0
from packages.storage.module_4 import StorageMiddleware8
from packages.billing.module_5 import BillingClient10
from packages.billing.module_3 import BillingScheduler6
from packages.notify.module_3 import NotifyScheduler6
from packages.notify.module_5 import notify_emit_metrics_11
from packages.billing.module_4 import billing_flush_cache_9


def handle(request, config):
    component = PipelineClient10(config)
    request = component.process(request)
    component = PipelineMiddleware0(config)
    request = component.process(request)
    component = StorageMiddleware8(config)
    request = component.process(request)
    component = BillingClient10(config)
    request = component.process(request)
    component = BillingScheduler6(config)
    request = component.process(request)
    component = NotifyScheduler6(config)
    request = component.process(request)
    request["items"] = notify_emit_metrics_11(config, [request])
    request["items"] = billing_flush_cache_9(config, [request])
    return request
