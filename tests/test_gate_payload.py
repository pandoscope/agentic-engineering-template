"""The `payload` subcommand: the one item an issue or comment event just
published, scanned minutes after it went public (#208; not a gate).
"""

import json

from tests.gate_support import (
    ROOT,
    gate_payload,
)


def test_payload_surfaces_read_only_the_item_the_event_carries():
    """#208: the event-driven net scans just the one thing that
    changed — an issue's title and body, or a comment's body — labeled
    with its link so a hit says where without walking the repo."""
    issue_event = {
        "action": "edited",
        "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
    }
    assert gate_payload.payload_surfaces(issue_event) == [
        ("issue https://x/issues/1 title", "t"),
        ("issue https://x/issues/1 body", "b"),
    ]
    comment_event = {
        "action": "created",
        "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
        "comment": {"body": "c", "html_url": "https://x/issues/1#issuecomment-5"},
    }
    assert gate_payload.payload_surfaces(comment_event) == [
        ("comment https://x/issues/1#issuecomment-5", "c"),
    ]
    review_comment_event = {
        "action": "edited",
        "pull_request": {"title": "t", "body": "b", "html_url": "https://x/pull/2"},
        "comment": {
            "body": "rc",
            "path": "a.py",
            "html_url": "https://x/pull/2#discussion_r7",
        },
    }
    assert gate_payload.payload_surfaces(review_comment_event) == [
        ("comment https://x/pull/2#discussion_r7 on a.py", "rc"),
    ]


def test_run_payload_is_red_on_a_hit_value_silent_and_green_when_clean(
    tmp_path, monkeypatch, capsys
):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "action": "created",
                "issue": {"title": "t", "body": "b", "html_url": "https://x/issues/1"},
                "comment": {
                    "body": "ping sekritvalue9 please",
                    "html_url": "https://x/issues/1#issuecomment-5",
                },
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("PUSH_BLOCKLIST", "sekritvalue9=pb:old-pw|other")
    assert gate_payload.run_payload() == 1
    out = capsys.readouterr().out
    assert "::error" in out
    assert "pb:old-pw" in out
    assert "https://x/issues/1#issuecomment-5" in out
    assert "sekritvalue9" not in out

    monkeypatch.setenv("PUSH_BLOCKLIST", "other")
    assert gate_payload.run_payload() == 0
    assert "::error" not in capsys.readouterr().out


def test_run_payload_warns_loudly_on_an_empty_blocklist(monkeypatch, capsys):
    monkeypatch.setenv("PUSH_BLOCKLIST", "")
    assert gate_payload.run_payload() == 0
    assert "::warning" in capsys.readouterr().out


def test_payload_scan_workflow_fires_on_every_comment_shaped_event():
    """The instant-detection piece (#208): issues, issue comments (PR
    conversation included) and review comments, on create AND edit —
    a comment scrubbed by edit is also a comment re-leaked by edit.
    Free on public repos; private repos are not exposed and skip via
    the event's own repository flag, no API call needed."""
    text = (
        ROOT
        / "template"
        / "{% if agentic_forge == 'github' %}.github{% endif %}"
        / "workflows"
        / "payload-scan.yml.jinja"
    ).read_text()
    for trigger in ("issues:", "issue_comment:", "pull_request_review_comment:"):
        assert trigger in text
    assert text.count("- created") == 2
    assert text.count("- edited") == 3
    assert "- opened" in text
    assert "check_gate.py payload" in text
    assert "secrets.PUSH_BLOCKLIST" in text
    assert "github.event.repository.private" in text
    assert "runs-on: {{ agentic_actions_runner }}" in text


def test_root_stamp_carries_the_payload_scan_workflow():
    text = (ROOT / ".github" / "workflows" / "payload-scan.yml").read_text()
    assert "{%" not in text
    assert "runs-on: ubuntu-latest" in text
