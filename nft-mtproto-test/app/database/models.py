"""Ten tables from §5 T1.4 of the master prompt, plus the §20 owner_lookup_status and queue priority."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.types import Money, UTCDateTime, utcnow

ACTIVE_QUEUE = "status IN ('pending', 'processing', 'retry')"


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    filters: Mapped[list[UserFilter]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Gift(Timestamps, Base):
    """One NFT: collection + number. Telegram IDs exceed 2**53, hence BIGINT."""
    __tablename__ = "gifts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_unique_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)  # StarGiftUnique.id
    gift_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)  # StarGiftUnique.gift_id
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    collection: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    model: Mapped[str | None] = mapped_column(String(128), index=True)
    backdrop: Mapped[str | None] = mapped_column(String(128), index=True)
    pattern: Mapped[str | None] = mapped_column(String(128), index=True)
    model_rarity_permille: Mapped[int | None] = mapped_column(Integer)
    backdrop_rarity_permille: Mapped[int | None] = mapped_column(Integer)
    pattern_rarity_permille: Mapped[int | None] = mapped_column(Integer)
    availability_issued: Mapped[int | None] = mapped_column(Integer)
    availability_total: Mapped[int | None] = mapped_column(Integer)
    burned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crafted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    listings: Mapped[list[Listing]] = relationship(back_populates="gift")
    __table_args__ = (UniqueConstraint("collection", "number", name="uq_gifts_collection_number"),)


class Listing(Timestamps, Base):
    """One sale episode of a gift. listing_identity = '{slug}:{episode}' (MTProto has no listing ID)."""
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gift_id: Mapped[int] = mapped_column(ForeignKey("gifts.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_identity: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Money)  # in the seller's currency
    currency: Mapped[str | None] = mapped_column(String(8))  # XTR | TON
    price_stars: Mapped[Decimal | None] = mapped_column(Money)
    price_ton: Mapped[Decimal | None] = mapped_column(Money)
    seller_name: Mapped[str | None] = mapped_column(String(256))  # StarGiftUnique.owner_name: display text only
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")  # ACTIVE | INACTIVE | UNKNOWN
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    listing_date: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # = first detection, see audit
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # §20: NOT_NEEDED | PENDING | DONE | DEFERRED | FAILED
    owner_lookup_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    gift: Mapped[Gift] = relationship(back_populates="listings")
    __table_args__ = (
        # At most one active listing per gift.
        Index("uq_listings_one_active", "gift_id", unique=True,
              sqlite_where=text("is_active = 1"), postgresql_where=text("is_active")),
    )


class Owner(Base):
    """A public owner identity. username can change; telegram_id is the stable key when present."""
    __tablename__ = "owners"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class OwnerLookupCache(Base):
    """Latest owner lookup per gift. Expired entries trigger a new paid request."""
    __tablename__ = "owner_lookup_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gift_id: Mapped[int] = mapped_column(ForeignKey("gifts.id", ondelete="CASCADE"), unique=True, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # FOUND|HIDDEN|NOT_FOUND|UNAVAILABLE|ERROR
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    last_checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    cost_ton: Mapped[Decimal | None] = mapped_column(Money)
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    owner: Mapped[Owner | None] = relationship()


class ApiUsage(Base):
    """Per provider per UTC day. Average cost = spent_ton / paid_requests."""
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    requests_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_success: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests_error: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paid_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spent_ton: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    last_free_remaining: Mapped[int | None] = mapped_column(Integer)
    last_balance_ton: Mapped[Decimal | None] = mapped_column(Money)
    budget_warning_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_stopped_reason: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("provider", "day", name="uq_api_usage_provider_day"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)  # one event = one notification per user
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("user_id", "event_key", name="uq_notifications_user_event"),)


class OwnerLogPost(Base):
    """An owner card in OWNER_LOG_GROUP_ID. PENDING -> SENT (message posted) -> CLAIMED (sent to claimer's DM)."""
    __tablename__ = "owner_log_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)  # Telegram id of the claimer
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ScanState(Base):
    """Scanner progress per gift type; survives restarts."""
    __tablename__ = "scan_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gift_type_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_full_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_count: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessingTask(Timestamps, Base):
    """DB-backed queue. Only one pending/processing/retry task per dedup_key."""
    __tablename__ = "processing_queue"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # higher first
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    gift_id: Mapped[int | None] = mapped_column(ForeignKey("gifts.id", ondelete="CASCADE"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("uq_queue_active_dedup", "dedup_key", unique=True,
              sqlite_where=text(ACTIVE_QUEUE), postgresql_where=text(ACTIVE_QUEUE)),
        Index("ix_queue_pick", "status", "priority", "next_attempt_at"),
    )


class UserFilter(Timestamps, Base):
    """Stage 1 (listing) criteria are kept apart from stage 2 (owner) criteria, see §20."""
    __tablename__ = "user_filters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_new_listing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_price_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_owner_found: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Stage 1: listing characteristics.
    collections: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    models: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    backdrops: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    patterns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    price_currency: Mapped[str] = mapped_column(String(8), default="XTR", nullable=False)
    price_min: Mapped[Decimal | None] = mapped_column(Money)
    price_max: Mapped[Decimal | None] = mapped_column(Money)
    # Stage 2: owner criteria, checked against the owner of each NFT found by the scanner.
    # They never select NFTs: the app does not look up gifts by username or Telegram ID.
    require_username: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_telegram_id: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner_usernames: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False, server_default="[]")
    owner_telegram_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False, server_default="[]")
    user: Mapped[User] = relationship(back_populates="filters")
