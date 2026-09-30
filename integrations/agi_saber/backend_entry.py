"""ASGI entry for the original application's complete route/dependency graph.

Deploy beside the original main.py. Business implementation remains in that
repository; this entry binds Uvicorn privately and installs the documented
observation and bounded query-extraction cache adapters.
"""
from main import build_deps

from application_metrics import ApplicationMetricsMiddleware
from request_observations import ExerciseScope, OfficeObservationStore, install
from query_extraction_cache import install as install_query_cache

store = OfficeObservationStore("/var/lib/agi-office/mini-drop-observations/requests.json")
install_query_cache()
install(store)

dependencies = build_deps()
dependencies.app.add_event_handler("shutdown", dependencies.inf.close)
app = ExerciseScope(ApplicationMetricsMiddleware(dependencies.app)).with_store(store)
