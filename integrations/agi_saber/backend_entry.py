"""ASGI entry for the original application's complete route/dependency graph.

Deploy beside the original main.py. Business implementation remains in that
repository; this entry only lets Uvicorn bind a private interface under systemd.
"""
from main import build_deps

from application_metrics import ApplicationMetricsMiddleware
from request_observations import ExerciseScope, OfficeObservationStore, install

store = OfficeObservationStore("/var/lib/agi-office/mini-drop-observations/requests.json")
install(store)

dependencies = build_deps()
dependencies.app.add_event_handler("shutdown", dependencies.inf.close)
app = ExerciseScope(ApplicationMetricsMiddleware(dependencies.app)).with_store(store)
