"""Views: thin request/response layer over ``services`` (which delegates to ``src``)."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from src.metrics import new_run_id
from src.reasons import PipelineError

from . import services
from .forms import CompareForm, LoadEntitiesForm, LoadReferenceForm, RunForm

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _page(request) -> int:
    try:
        return max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        return 1


def _save_upload(upload, directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    name = SAFE_NAME_RE.sub("_", Path(upload.name).name) or "upload.csv"
    path = directory / name
    with open(path, "wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    return str(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def dashboard(request):
    return render(request, "ui/dashboard.html", {"data": services.dashboard_data(), "nav": "dashboard"})


def run_list(request):
    status = request.GET.get("status", "")
    version = request.GET.get("version", "")
    q = request.GET.get("q", "").strip()
    page = services.list_runs(status=status, version=version, q=q, page=_page(request))
    return render(request, "ui/runs/list.html", {
        "page_obj": page, "status": status, "version": version, "q": q,
        "versions": services.version_choices(), "nav": "runs",
        "statuses": ["SUCCESS", "FAILED", "RUNNING"],
    })


def run_new(request):
    form = RunForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        if cd["check_entities"] and not cd["entities_file"] and services.entity_count() == 0:
            form.add_error("check_entities", "No entity ids are loaded yet — upload an entities CSV first.")
        else:
            run_id = cd["run_id"] or new_run_id()
            updir = Path(settings.PIPELINE_UPLOAD_DIR) / run_id
            input_path = _save_upload(cd["input_file"], updir)
            version = cd["reference_version"]
            try:
                if cd["new_reference_file"]:
                    ref_path = _save_upload(cd["new_reference_file"], updir)
                    state = services.load_reference_file(cd["new_reference_version"], ref_path)
                    version = cd["new_reference_version"]
                    messages.info(request, f"Reference version {version} {'loaded' if state == 'loaded' else 'already loaded'}.")
                check_entities = cd["check_entities"]
                if cd["entities_file"]:
                    ent_path = _save_upload(cd["entities_file"], updir)
                    n = services.load_entities_file(ent_path)
                    check_entities = True
                    messages.info(request, f"Loaded {n} entity ids.")
            except PipelineError as exc:
                form.add_error(None, f"{exc.code}: {exc.message}")
            else:
                services.start_run(
                    input_path=input_path, reference_version=version,
                    check_entities=check_entities,
                    deprecated_policy=cd["deprecated_policy"],
                    strict_columns=cd["strict_columns"],
                    event_from=cd["event_from"].isoformat() if cd["event_from"] else None,
                    event_to=cd["event_to"].isoformat() if cd["event_to"] else None,
                    run_id=run_id,
                )
                messages.success(request, f"Run {run_id} started against {version}.")
                return redirect(reverse("ui:run_detail", args=[run_id]) + "?started=1")
    return render(request, "ui/runs/new.html", {"form": form, "nav": "new_run"})


@require_POST
def run_sample(request):
    """One-click demo: the bundled 48-row sample against its own reference snapshot.

    The sample CSV embeds a reference_version column ("v1"); the loader only keeps
    rows matching the requested version, so we store a copy labelled "sample_v1".
    That also avoids colliding with a user-loaded "v1" (versions are immutable).
    """
    import csv

    sample = Path(settings.BASE_DIR) / "data" / "sample"
    updir = Path(settings.PIPELINE_UPLOAD_DIR) / f"sample_{new_run_id()}"
    updir.mkdir(parents=True, exist_ok=True)
    ref_copy = updir / "reference_codes_sample_v1.csv"
    try:
        with open(sample / "reference_codes_v1.csv", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            row["reference_version"] = "sample_v1"
        with open(ref_copy, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        services.load_reference_file("sample_v1", str(ref_copy))
        services.load_entities_file(str(sample / "entities.csv"))
    except (PipelineError, OSError) as exc:
        messages.error(request, f"Could not prepare the sample: {exc}")
        return redirect("ui:run_new")
    run_id = services.start_run(
        input_path=str(sample / "incoming_records.csv"),
        reference_version="sample_v1", check_entities=True,
    )
    messages.success(request, "Sample run started (48 rows against sample_v1).")
    return redirect(reverse("ui:run_detail", args=[run_id]) + "?started=1")


def run_detail(request, run_id: str):
    run = services.get_run(run_id)
    started = request.GET.get("started") == "1"
    if run is None:
        if not started:
            raise Http404("Unknown run id")
        run = {"run_id": run_id, "status": "STARTING", "reference_version": "", "source_file": ""}
    summary = _read_json(Path(services.get_cfg().output_dir) / run_id / "run_summary.json") or {}
    checks = summary.get("reconciliation") or []
    tab = request.GET.get("tab", "overview")
    if tab not in ("overview", "rejects", "outcomes", "reconciliation"):
        tab = "overview"
    reason = request.GET.get("reason", "")
    q = request.GET.get("q", "").strip()
    outcome = request.GET.get("outcome", "")
    rejects = services.rejects_for_run(run_id, reason=reason, q=q, page=_page(request)) if tab == "rejects" else None
    outcomes = services.outcomes_for_run(run_id, outcome=outcome, page=_page(request)) if tab == "outcomes" else None
    config = _read_json_str(run.get("config_json"))
    warnings = _read_json_str(run.get("warnings")) or []
    run_reasons = services.reasons_for_run(run_id)
    return render(request, "ui/runs/detail.html", {
        "run": run, "summary": summary, "checks": checks, "tab": tab,
        "reasons": run_reasons, "reason": reason, "q": q,
        "outcome": outcome, "rejects": rejects, "outcomes": outcomes,
        "config": config, "warnings": warnings, "started": started, "nav": "runs",
        "reason_choices": [r["reason"] for r in run_reasons],
    })


def _read_json_str(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def run_status(request, run_id: str):
    return JsonResponse(services.run_status(run_id))


def run_download(request, run_id: str, filename: str):
    if services.get_run(run_id) is None:
        raise Http404("Unknown run id")
    path = services.report_path(run_id, filename)
    if path is None:
        raise Http404("Report not available (the run may have failed before publishing)")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=f"{run_id}_{filename}")


def sample_download(request, filename: str):
    allowed = {
        "incoming_records.csv": Path(settings.BASE_DIR) / "data" / "sample" / "incoming_records.csv",
        "reference_codes_v1.csv": Path(settings.BASE_DIR) / "data" / "sample" / "reference_codes_v1.csv",
        "reference_codes_v2.csv": Path(settings.BASE_DIR) / "data" / "sample" / "reference_codes_v2.csv",
        "entities.csv": Path(settings.BASE_DIR) / "data" / "sample" / "entities.csv",
        "test_incoming_records.csv": Path(settings.BASE_DIR) / "data" / "test_sample" / "test_incoming_records.csv",
        "test_reference_v1.csv": Path(settings.BASE_DIR) / "data" / "test_sample" / "test_reference_v1.csv",
        "test_entities.csv": Path(settings.BASE_DIR) / "data" / "test_sample" / "test_entities.csv",
    }
    path = allowed.get(filename)
    if not path or not path.exists():
        raise Http404("Sample file not found")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)


def compare_view(request):
    form = CompareForm(request.GET or None)
    result = error = None
    if request.GET.get("run_a") and request.GET.get("run_b"):
        if form.is_valid():
            try:
                result = services.compare(form.cleaned_data["run_a"], form.cleaned_data["run_b"])
            except PipelineError as exc:
                error = exc.message
        else:
            error = " ".join(e for errs in form.errors.values() for e in errs)
    return render(request, "ui/compare.html", {"form": form, "result": result, "error": error, "nav": "compare"})


def reference_list(request):
    return render(request, "ui/reference/list.html", {
        "versions": services.reference_versions(), "nav": "reference",
    })


def reference_detail(request, version: str):
    meta = services.get_version(version)
    if meta is None:
        raise Http404("Unknown reference version")
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    codes = services.reference_codes(version, status=status, q=q, page=_page(request))
    return render(request, "ui/reference/detail.html", {
        "meta": meta, "page_obj": codes, "status": status, "q": q, "nav": "reference",
        "statuses": ["ACTIVE", "DEPRECATED", "INACTIVE"],
    })


def reference_download(request, version: str):
    if services.get_version(version) is None:
        raise Http404("Unknown reference version")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="reference_codes_{version}.csv"'
    writer = csv.writer(response)
    writer.writerow(["reference_version", "product_code", "description", "status",
                     "effective_from", "effective_to"])
    for r in services.reference_export_rows(version):
        writer.writerow([version, r["product_code"], r["description"], r["code_status"],
                         r["effective_from"],
                         "" if r["effective_to"] == "9999-12-31" else r["effective_to"]])
    return response


def reference_load(request):
    form = LoadReferenceForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        updir = Path(settings.PIPELINE_UPLOAD_DIR) / f"ref_{cd['reference_version']}_{new_run_id()}"
        path = _save_upload(cd["reference_file"], updir)
        try:
            state = services.load_reference_file(cd["reference_version"], path)
        except PipelineError as exc:
            form.add_error(None, f"{exc.code}: {exc.message}")
        else:
            messages.success(request, f"Reference version {cd['reference_version']} {state}.")
            return redirect("ui:reference_detail", version=cd["reference_version"])
    return render(request, "ui/reference/load.html", {"form": form, "nav": "reference"})


def entities_view(request):
    form = LoadEntitiesForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        updir = Path(settings.PIPELINE_UPLOAD_DIR) / f"entities_{new_run_id()}"
        path = _save_upload(form.cleaned_data["entities_file"], updir)
        try:
            n = services.load_entities_file(path)
        except PipelineError as exc:
            form.add_error(None, f"{exc.code}: {exc.message}")
        else:
            messages.success(request, f"Loaded {n} entity ids.")
            return redirect("ui:entities")
    q = request.GET.get("q", "").strip()
    return render(request, "ui/entities.html", {
        "form": form, "page_obj": services.entities_list(q=q, page=_page(request)),
        "q": q, "count": services.entity_count(), "nav": "entities",
    })


def validated_view(request):
    q = request.GET.get("q", "").strip()
    version = request.GET.get("version", "")
    deprecated_only = request.GET.get("deprecated") == "1"
    page = services.validated_records(q=q, version=version, deprecated_only=deprecated_only, page=_page(request))
    return render(request, "ui/data/validated.html", {
        "page_obj": page, "q": q, "version": version, "deprecated_only": deprecated_only,
        "versions": services.version_choices(), "nav": "validated",
    })


def quarantine_view(request):
    q = request.GET.get("q", "").strip()
    reason = request.GET.get("reason", "")
    run_id = request.GET.get("run_id", "").strip()
    page = services.quarantine(q=q, reason=reason, run_id=run_id, page=_page(request))
    return render(request, "ui/data/quarantine.html", {
        "page_obj": page, "q": q, "reason": reason, "run_id": run_id, "nav": "quarantine",
    })


def handler404(request, exception):
    return render(request, "ui/404.html", status=404)