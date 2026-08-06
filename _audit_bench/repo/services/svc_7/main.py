"""Service 7 entrypoint."""

from packages.billing.module_0 import billing_flush_cache_1
from packages.pipeline.module_5 import PipelineClient10
from packages.billing.module_3 import billing_audit_access_7
from packages.auth.module_2 import auth_prune_stale_5
from packages.auth.module_1 import auth_emit_metrics_3
from packages.search.module_3 import search_audit_access_7
from packages.auth.module_4 import auth_flush_cache_9
from packages.billing.module_1 import billing_emit_metrics_3
from packages.auth.module_5 import AuthClient10
from packages.notify.module_5 import notify_emit_metrics_11
from packages.search.module_0 import search_flush_cache_1
from packages.billing.module_4 import billing_flush_cache_9


def handle(request, config):
    request["items"] = billing_flush_cache_1(config, [request])
    component = PipelineClient10(config)
    request = component.process(request)
    request["items"] = billing_audit_access_7(config, [request])
    request["items"] = auth_prune_stale_5(config, [request])
    request["items"] = auth_emit_metrics_3(config, [request])
    request["items"] = search_audit_access_7(config, [request])
    request["items"] = auth_flush_cache_9(config, [request])
    request["items"] = billing_emit_metrics_3(config, [request])
    component = AuthClient10(config)
    request = component.process(request)
    request["items"] = notify_emit_metrics_11(config, [request])
    request["items"] = search_flush_cache_1(config, [request])
    request["items"] = billing_flush_cache_9(config, [request])
    return request
