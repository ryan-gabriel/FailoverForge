from __future__ import annotations

import uuid

import asyncpg

from app.state import to_iso


async def create_pool(database_url: str) -> asyncpg.Pool:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)


async def insert_order(
    pool: asyncpg.Pool,
    order_id: uuid.UUID,
    sku: str,
    qty: int,
    customer_ref: str | None,
) -> str:
    row = await pool.fetchrow(
        "INSERT INTO orders (order_id, sku, qty, customer_ref, status)"
        " VALUES ($1, $2, $3, $4, 'pending') RETURNING created_at",
        order_id,
        sku,
        qty,
        customer_ref,
    )
    return to_iso(row["created_at"])


async def get_order(pool: asyncpg.Pool, order_id: uuid.UUID) -> dict | None:
    row = await pool.fetchrow(
        "SELECT order_id, status, sku, qty, created_at, fulfilled_at"
        " FROM orders WHERE order_id = $1",
        order_id,
    )
    if row is None:
        return None
    return {
        "order_id": str(row["order_id"]),
        "status": row["status"],
        "sku": row["sku"],
        "qty": row["qty"],
        "created_at": to_iso(row["created_at"]),
        "fulfilled_at": to_iso(row["fulfilled_at"]),
    }
