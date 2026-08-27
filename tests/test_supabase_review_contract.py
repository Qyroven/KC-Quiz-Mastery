from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_hardening_migration_routes_writes_through_validated_rpc() -> None:
    migration = (
        REPOSITORY_ROOT
        / "supabase/migrations/202608270002_harden_shared_review.sql"
    ).read_text(encoding="utf-8")

    assert "create table public.review_targets" in migration
    assert "review_events_registered_target_fk" in migration
    assert "revoke select on public.review_events from anon, authenticated" in migration
    assert "revoke insert on public.review_events from authenticated" in migration
    assert "create function public.append_review_event" in migration
    assert "for update of registry" in migration
    assert "stale revision" in migration
    assert "review_payload_is_valid" in migration
    assert "revision payload exceeds 256 KiB" in migration
    assert "too many review actions" in migration
    assert "create function public.get_review_target_events" in migration


def test_day1_registry_contains_every_reviewable_target() -> None:
    registry = (REPOSITORY_ROOT / "supabase/day01-review-targets.sql").read_text(
        encoding="utf-8"
    )

    assert registry.count("'extraction', 'page'") == 78
    assert registry.count("'kc', 'leaf_kc'") == 41
    assert registry.count("'kc', 'page_audit'") == 20
    assert registry.count("'quiz', 'question'") == 82
    assert registry.count("('day01-llm-foundation-agent-v1',") == 221
