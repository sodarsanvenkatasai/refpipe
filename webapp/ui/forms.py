"""Web forms. Validation mirrors the CLI flags; pipeline rules stay in ``src``."""
from __future__ import annotations

import csv
import re

from django import forms

from src.reasons import DEPRECATED_POLICIES, REFERENCE_COLUMNS, REQUIRED_COLUMNS

from . import services

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")

REFERENCE_FILE_HELP = (
    "The CSV must contain these columns only (exact lowercase names): "
    "product_code, description, status, effective_from, effective_to "
    "(reference_version is optional). A file missing any of them is rejected."
)


def _header_cols(upload) -> set[str]:
    upload.seek(0)
    line = upload.readline().decode("utf-8-sig", errors="replace")
    upload.seek(0)
    try:
        return {c.strip() for c in next(csv.reader([line]))}
    except (StopIteration, csv.Error):
        return set()


class RunForm(forms.Form):
    input_file = forms.FileField(
        label="Incoming records CSV",
        help_text="Required columns: record_id, entity_id, product_code, event_date, amount.",
    )
    reference_version = forms.ChoiceField(
        label="Reference version", required=False, choices=(),
        help_text="Pick an already-loaded version, or load a new one below. A run uses exactly one version.",
    )
    new_reference_version = forms.CharField(
        label="New version name", required=False, max_length=32,
        help_text="e.g. v3 — immutable once loaded. If the CSV has a reference_version column, the name must match it.",
        widget=forms.TextInput(attrs={"placeholder": "v3"}),
    )
    new_reference_file = forms.FileField(
        label="New reference CSV", required=False,
        help_text=REFERENCE_FILE_HELP,
    )
    entities_file = forms.FileField(
        label="Entities CSV", required=False,
        help_text="Optional. Loading it enables the ORPHAN_ENTITY check (column: entity_id).",
    )
    check_entities = forms.BooleanField(
        label="Check entities (use already-loaded entity ids)", required=False,
    )
    deprecated_policy = forms.ChoiceField(
        label="Deprecated-code policy",
        choices=[("allow_historical", "Allow historical (accept inside the code's window, flag it)"),
                 ("reject", "Reject all deprecated codes")],
        initial="allow_historical",
    )
    strict_columns = forms.BooleanField(
        label="Strict columns (unexpected extra columns fail the batch)", required=False,
    )
    event_from = forms.DateField(
        label="Event from", required=False,
        help_text="Backfill: only process rows with event_date on/after this date.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    event_to = forms.DateField(
        label="Event to", required=False,
        help_text="Backfill: only process rows with event_date on/before this date.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    run_id = forms.CharField(
        label="Run id", required=False, max_length=64,
        help_text="Optional. Leave blank to auto-generate. Letters, digits, _ . - only.",
        widget=forms.TextInput(attrs={"placeholder": "auto-generated"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference_version"].choices = [("", "— choose a loaded version —"), *services.version_choices()]

    def clean_run_id(self):
        run_id = self.cleaned_data.get("run_id", "").strip()
        if not run_id:
            return ""
        if not RUN_ID_RE.match(run_id):
            raise forms.ValidationError("Use letters, digits, underscores, dots or dashes (max 64).")
        if services.run_exists(run_id):
            raise forms.ValidationError("This run id already exists — choose another or leave blank.")
        return run_id

    def clean_new_reference_version(self):
        version = self.cleaned_data.get("new_reference_version", "").strip()
        if version and not VERSION_RE.match(version):
            raise forms.ValidationError("Use letters, digits, underscores, dots or dashes (max 32).")
        return version

    def clean(self):
        cleaned = super().clean()
        version = cleaned.get("reference_version")
        new_version = cleaned.get("new_reference_version")
        new_file = cleaned.get("new_reference_file")
        if new_file and not new_version:
            self.add_error("new_reference_version", "Give the new version a name.")
        if new_version and not new_file:
            self.add_error("new_reference_file", "Attach the reference CSV for the new version.")
        if not version and not (new_version and new_file):
            self.add_error("reference_version",
                           "Choose a loaded version or upload a new reference CSV with a version name.")
        ef, et = cleaned.get("event_from"), cleaned.get("event_to")
        if ef and et and ef > et:
            self.add_error("event_to", "Event to must be on or after event from.")
        input_file = cleaned.get("input_file")
        if input_file:
            cols = _header_cols(input_file)
            if set(REFERENCE_COLUMNS) <= cols and not set(REQUIRED_COLUMNS) <= cols:
                self.add_error("input_file",
                               "This file looks like a reference snapshot, not incoming records. "
                               "Reference files go in the reference slot below (or the Versions page). "
                               "The incoming records file needs: record_id, entity_id, product_code, "
                               "event_date, amount.")
        new_file = cleaned.get("new_reference_file")
        if new_file:
            cols = _header_cols(new_file)
            if set(REQUIRED_COLUMNS) <= cols and not set(REFERENCE_COLUMNS) <= cols:
                self.add_error("new_reference_file",
                               "This file looks like an incoming records batch, not a reference "
                               "snapshot. Upload it in the Incoming records slot above; the reference "
                               "CSV needs: product_code, description, status, effective_from, effective_to.")
        return cleaned


class LoadReferenceForm(forms.Form):
    reference_version = forms.CharField(
        label="Version name", max_length=32,
        help_text="Immutable once loaded. If the CSV has a reference_version column, the name must match it.",
        widget=forms.TextInput(attrs={"placeholder": "v1"}),
    )
    reference_file = forms.FileField(
        label="Reference CSV",
        help_text=REFERENCE_FILE_HELP,
    )

    def clean_reference_version(self):
        version = self.cleaned_data["reference_version"].strip()
        if not VERSION_RE.match(version):
            raise forms.ValidationError("Use letters, digits, underscores, dots or dashes (max 32).")
        return version


class LoadEntitiesForm(forms.Form):
    entities_file = forms.FileField(
        label="Entities CSV",
        help_text="One column: entity_id. Ids are merged with previously loaded ones.",
    )


class CompareForm(forms.Form):
    run_a = forms.ChoiceField(label="Run A (baseline)", choices=())
    run_b = forms.ChoiceField(label="Run B (new)", choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = services.run_choices()
        self.fields["run_a"].choices = choices
        self.fields["run_b"].choices = choices

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("run_a") and cleaned.get("run_a") == cleaned.get("run_b"):
            raise forms.ValidationError("Choose two different runs to compare.")
        return cleaned