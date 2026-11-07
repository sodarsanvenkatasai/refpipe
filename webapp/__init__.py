"""Django web UI for the reference-code validation pipeline.

The UI is a thin layer over the existing ``src`` package: all business logic
(validation, publishing, reconciliation, comparison) stays in the pipeline
modules, exactly like ``src/cli.py`` is a thin layer for the terminal.
"""