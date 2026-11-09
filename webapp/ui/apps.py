import logging
import sys

from django.apps import AppConfig

log = logging.getLogger("refpipe.web")


class UiConfig(AppConfig):
    name = "webapp.ui"
    verbose_name = "Pipeline Web UI"

    def ready(self):
        # A restart means any RUNNING row belongs to a dead process; close it so a retry is unambiguous.
        if "runserver" not in sys.argv:
            return
        from . import services
        try:
            recovered = services.recover_stale_runs()
        except Exception:
            log.exception("stale-run recovery skipped")
            return
        if recovered:
            log.warning("Recovered stale RUNNING runs as FAILED: %s", ", ".join(recovered))