"""Service 8 entrypoint."""

from packages.billing.module_3 import billing_audit_access_7
from packages.notify.module_3 import notify_audit_access_7
from packages.auth.module_2 import AuthResolver4
from packages.billing.module_5 import BillingClient10
from packages.auth.module_4 import auth_flush_cache_9
from packages.billing.module_1 import billing_emit_metrics_3
from packages.storage.module_3 import storage_audit_access_7
from packages.billing.module_4 import billing_flush_cache_9


def handle(request, config):
    request["items"] = billing_audit_access_7(config, [request])
    request["items"] = notify_audit_access_7(config, [request])
    component = AuthResolver4(config)
    request = component.process(request)
    component = BillingClient10(config)
    request = component.process(request)
    request["items"] = auth_flush_cache_9(config, [request])
    request["items"] = billing_emit_metrics_3(config, [request])
    request["items"] = storage_audit_access_7(config, [request])
    request["items"] = billing_flush_cache_9(config, [request])
    return request
