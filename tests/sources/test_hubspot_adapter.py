from datetime import UTC, datetime
from typing import Any

from app.sources.exceptions import ProviderRateLimitError
from app.sources.hubspot import HubSpotContactsAdapter
from app.sources.retry import RetryPolicy
from app.sources.types import ProviderPage


def contact(contact_id: str, modified_at: str, **properties: Any) -> dict[str, Any]:
    return {
        "id": contact_id,
        "properties": {
            "lastmodifieddate": modified_at,
            "email": properties.get("email", f"{contact_id}@example.com"),
            "firstname": properties.get("firstname", "First"),
            "lastname": properties.get("lastname", "Last"),
            "phone": properties.get("phone"),
        },
    }


def test_hubspot_full_fetch_paginates_normalizes_rejects_and_checkpoints() -> None:
    class FakeHubSpotClient:
        def __init__(self) -> None:
            self.calls: list[str | None] = []

        def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
            self.calls.append(after)
            if after is None:
                return ProviderPage(
                    items=[
                        contact("contact-1", "2026-06-17T12:00:00Z"),
                        {"id": "bad-contact", "properties": {"email": "bad@example.com"}},
                        "not-a-contact",
                    ],
                    next_cursor="cursor-2",
                )
            return ProviderPage(items=[contact("contact-2", "2026-06-17T12:05:00Z")])

        def search_contacts_modified_since_page(
            self,
            *,
            modified_since: datetime,
            after: str | None,
            limit: int,
        ) -> ProviderPage:
            raise AssertionError("full fetch should not use search")

    client = FakeHubSpotClient()
    adapter = HubSpotContactsAdapter(
        client=client,
        now=lambda: datetime(2026, 6, 17, 12, 10, tzinfo=UTC),
    )

    result = adapter.fetch_full()

    assert client.calls == [None, "cursor-2"]
    assert result.pages_fetched == 2
    assert result.records_fetched == 4
    assert result.rejected_records == 2
    assert [record.external_id for record in result.records] == ["contact-1", "contact-2"]
    assert result.records[0].canonical_data["email"] == "contact-1@example.com"
    assert result.records[0].raw_payload["id"] == "contact-1"
    assert result.next_checkpoint_data == {
        "watermark": "2026-06-17T12:05:00Z",
        "overlap_seconds": 120,
    }


def test_hubspot_incremental_uses_watermark_overlap_not_page_cursor_as_checkpoint() -> None:
    class FakeHubSpotClient:
        def __init__(self) -> None:
            self.modified_since_values: list[datetime] = []
            self.after_values: list[str | None] = []

        def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
            raise AssertionError("incremental fetch should use search")

        def search_contacts_modified_since_page(
            self,
            *,
            modified_since: datetime,
            after: str | None,
            limit: int,
        ) -> ProviderPage:
            self.modified_since_values.append(modified_since)
            self.after_values.append(after)
            return ProviderPage(items=[contact("contact-3", "2026-06-17T12:11:00Z")])

    client = FakeHubSpotClient()
    adapter = HubSpotContactsAdapter(
        client=client,
        now=lambda: datetime(2026, 6, 17, 12, 12, tzinfo=UTC),
    )

    result = adapter.fetch_incremental(
        {"watermark": "2026-06-17T12:10:00Z", "overlap_seconds": 120}
    )

    assert client.modified_since_values == [datetime(2026, 6, 17, 12, 8, tzinfo=UTC)]
    assert client.after_values == [None]
    assert result.effective_mode == "incremental"
    assert result.next_checkpoint_data == {
        "watermark": "2026-06-17T12:11:00Z",
        "overlap_seconds": 120,
    }
    assert "after" not in result.next_checkpoint_data


def test_hubspot_fetch_retries_rate_limits_with_bound() -> None:
    class FlakyHubSpotClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_contacts_page(self, *, after: str | None, limit: int) -> ProviderPage:
            self.calls += 1
            if self.calls < 3:
                raise ProviderRateLimitError("rate limited")
            return ProviderPage(items=[contact("contact-1", "2026-06-17T12:00:00Z")])

        def search_contacts_modified_since_page(
            self,
            *,
            modified_since: datetime,
            after: str | None,
            limit: int,
        ) -> ProviderPage:
            raise AssertionError("full fetch should not use search")

    client = FlakyHubSpotClient()
    adapter = HubSpotContactsAdapter(
        client=client,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )

    result = adapter.fetch_full()

    assert client.calls == 3
    assert len(result.records) == 1
