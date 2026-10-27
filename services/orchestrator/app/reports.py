from __future__ import annotations

from typing import Any

EVENT_ROLE = {
    "INCIDENT_OPENED": "opened",
    "INCIDENT_CLOSED": "closed",
    "FAILURE_INJECTED": "injected",
    "SYMPTOM_DETECTED": "detected",
    "MITIGATION_APPLIED": "mitigated",
    "LOAD_STARTED": "load",
    "LOAD_STOPPED": "load",
}


def _fmt_payload(payload: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in payload.items())


def generate_report(incident_id: str, events: list[dict]) -> dict:
    """Post-incident report derived from event-store rows ONLY (PRD §10,
    ARCHITECTURE §5.3 replayability): root cause, blast radius, timeline,
    MTTR. Nothing outside `events` is consulted.
    """
    opened = next((e for e in events if e["event_type"] == "INCIDENT_OPENED"), None)
    closed = next((e for e in events if e["event_type"] == "INCIDENT_CLOSED"), None)
    title = (opened or {}).get("payload", {}).get(
        "title"
    ) or f"incident {incident_id[:8]}"
    status = "closed" if closed else "open"
    opened_at = (opened or {}).get("ts")
    closed_at = (closed or {}).get("ts")
    mttr_s = (closed or {}).get("payload", {}).get("mttr_s")

    injected = next(
        (e["payload"] for e in events if e["event_type"] == "FAILURE_INJECTED"), None
    )
    symptom = next(
        (e["payload"] for e in events if e["event_type"] == "SYMPTOM_DETECTED"), None
    )
    if injected:
        root_cause: dict | None = {
            "service": injected.get("service"),
            "failure_type": injected.get("failure_type"),
            "intensity": injected.get("intensity"),
        }
    elif symptom:
        # Threshold-opened incident: the first symptom is the root cause.
        root_cause = {
            "service": symptom.get("service"),
            "failure_type": f"threshold:{symptom.get('metric')}",
            "intensity": symptom.get("observed"),
        }
    else:
        root_cause = None

    blast_radius: list[str] = []
    for event in events:
        service = event["payload"].get("service")
        if service and service not in blast_radius:
            blast_radius.append(service)

    timeline_summary = [e["event_type"] for e in events]
    markdown = _render_markdown(
        incident_id=incident_id,
        title=title,
        status=status,
        opened_at=opened_at,
        closed_at=closed_at,
        mttr_s=mttr_s,
        root_cause=root_cause,
        blast_radius=blast_radius,
        events=events,
    )
    return {
        "incident_id": incident_id,
        "title": title,
        "root_cause": root_cause,
        "blast_radius": blast_radius,
        "mttr_s": mttr_s,
        "timeline_summary": timeline_summary,
        "markdown": markdown,
    }


def _render_markdown(
    *,
    incident_id: str,
    title: str,
    status: str,
    opened_at: str | None,
    closed_at: str | None,
    mttr_s: float | None,
    root_cause: dict | None,
    blast_radius: list[str],
    events: list[dict],
) -> str:
    lines = [f"# Incident report: {title}", ""]
    lines.append(f"- **Incident:** `{incident_id}`")
    lines.append(f"- **Status:** {status}")
    lines.append(
        f"- **Opened:** {opened_at or '—'} → **Closed:** {closed_at or '— (still open)'}"
    )
    lines.append(f"- **MTTR:** {f'{mttr_s:g}s' if mttr_s is not None else '—'}")
    lines += ["", "## Root cause", ""]
    if root_cause:
        lines.append(f"- **Service:** `{root_cause['service']}`")
        lines.append(
            f"- **Failure:** {root_cause['failure_type']} (intensity {root_cause['intensity']})"
        )
    else:
        lines.append("- No injection or symptom recorded in the event store.")
    lines += ["", "## Blast radius", ""]
    if blast_radius:
        lines += [f"- `{service}`" for service in blast_radius]
    else:
        lines.append("- none recorded")
    lines += ["", "## Timeline", ""]
    for event in events:
        role = EVENT_ROLE.get(event["event_type"], "event")
        lines.append(
            f"- `#{event['seq']}` `{event['ts']}` **{event['event_type']}** "
            f"({role}) — {_fmt_payload(event['payload']) or '—'}"
        )
    lines.append("")
    return "\n".join(lines)
