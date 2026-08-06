"""Service 6 entrypoint."""

from packages.pipeline.module_5 import PipelineClient10
from packages.billing.module_3 import billing_audit_access_7
from packages.notify.module_3 import notify_audit_access_7
from packages.auth.module_1 import auth_emit_metrics_3
from packages.auth.module_2 import AuthResolver4
from packages.search.module_3 import search_audit_access_7
from packages.storage.module_4 import StorageMiddleware8
from packages.billing.module_3 import BillingScheduler6
from packages.billing.module_1 import billing_emit_metrics_3
from packages.notify.module_5 import notify_emit_metrics_11


def handle(request, config):
    component = PipelineClient10(config)
    request = component.process(request)
    request["items"] = billing_audit_access_7(config, [request])
    request["items"] = notify_audit_access_7(config, [request])
    request["items"] = auth_emit_metrics_3(config, [request])
    component = AuthResolver4(config)
    request = component.process(request)
    request["items"] = search_audit_access_7(config, [request])
    component = StorageMiddleware8(config)
    request = component.process(request)
    component = BillingScheduler6(config)
    request = component.process(request)
    request["items"] = billing_emit_metrics_3(config, [request])
    request["items"] = notify_emit_metrics_11(config, [request])
    return request
