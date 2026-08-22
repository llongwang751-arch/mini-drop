import gc, json, os, resource, sys, time, weakref
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, PeriodicExportingMetricReader
class Exporter(MetricExporter):
    def export(self, metrics_data, timeout_millis=10000, **kwargs): return MetricExportResult.SUCCESS
    def shutdown(self, timeout_millis=30000, **kwargs): return None
    def force_flush(self, timeout_millis=10000): return True

def rss_kb(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
refs=[]
before=rss_kb()
for _ in range(250):
    exporter=Exporter(); reader=PeriodicExportingMetricReader(exporter, export_interval_millis=3600000)
    provider=MeterProvider(metric_readers=[reader]); refs.append((weakref.ref(exporter),weakref.ref(reader),weakref.ref(provider)))
    provider.shutdown(); del provider,reader,exporter
gc.collect(); time.sleep(.05); gc.collect()
alive={"exporter":sum(1 for a,_,__ in refs if a() is not None),"reader":sum(1 for _,b,__ in refs if b() is not None),"provider":sum(1 for _,__,c in refs if c() is not None)}
print(json.dumps({"iterations":250,"rss_before_kb":before,"rss_peak_kb":rss_kb(),"alive":alive,"all_collected":sum(alive.values())==0},sort_keys=True))
