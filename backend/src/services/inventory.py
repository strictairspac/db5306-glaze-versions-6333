"""称料组分批盘点单：开单固定水位，按页幂等确认，未确认页为回收预检留存版次。"""

from fastapi import HTTPException
from sqlalchemy import select

from src.config.settings import engine
from src.constants.workflow import Inventory
from src.models.tables import cases, inventory_orders, inventory_pages
from src.services.glaze_history import inventory_plan, parse_ledger
from src.services.workflow import found, log
from src.utils.time import utc_time

MAX_PAGE_SIZE = 50


def open_order(case_id, title, page_size, actor):
    title = title.strip()
    if not title:
        raise HTTPException(422, '盘点单名称不能为空')
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise HTTPException(422, f'每页配方数须为 1 至 {MAX_PAGE_SIZE}')
    with engine.begin() as conn:
        archive = found(conn, cases, case_id)
        try:
            ledger = parse_ledger(archive['payload'])
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        pages = inventory_plan(ledger, page_size)
        if not pages:
            raise HTTPException(422, f'该档案在水位 {ledger.head_seq} 下没有可盘点的配方')
        order_id = conn.execute(inventory_orders.insert().values(
            case_id=case_id, title=title, watermark=ledger.head_seq, page_size=page_size,
            page_count=len(pages), status=Inventory.ACTIVE, owner=actor,
            created_at=utc_time())).inserted_primary_key[0]
        for page_index, items in enumerate(pages):
            conn.execute(inventory_pages.insert().values(
                order_id=order_id, page_index=page_index, items=items))
        log(conn, actor, 'inventory_open', order_id)
        return detail(conn, order_id)


def detail(conn, order_id):
    order = found(conn, inventory_orders, order_id)
    pages = [dict(row) for row in conn.execute(
        select(inventory_pages).where(inventory_pages.c.order_id == order_id)
        .order_by(inventory_pages.c.page_index)).mappings()]
    order['pages'] = pages
    order['confirmed_pages'] = sum(1 for page in pages if page['confirmed_at'])
    # 中断恢复点：第一个未确认页；全部确认后为 None。
    order['next_page'] = next((page['page_index'] for page in pages if not page['confirmed_at']), None)
    return order


def list_orders():
    with engine.connect() as conn:
        ids = conn.execute(select(inventory_orders.c.id)
                           .order_by(inventory_orders.c.id.desc()).limit(100)).scalars().all()
        return [detail(conn, order_id) for order_id in ids]


def get_order(order_id):
    with engine.connect() as conn:
        return detail(conn, order_id)


def confirm(order_id, page_index, actor):
    with engine.begin() as conn:
        found(conn, inventory_orders, order_id)
        pages = conn.execute(select(inventory_pages)
                             .where(inventory_pages.c.order_id == order_id)
                             .order_by(inventory_pages.c.page_index)).mappings().all()
        page = next((row for row in pages if row['page_index'] == page_index), None)
        if page is None:
            raise HTTPException(404, '找不到该盘点页')
        if page['confirmed_at']:
            # 幂等重试：已确认页直接返回现状，不重复推进。
            return detail(conn, order_id)
        if any(row['confirmed_at'] is None for row in pages if row['page_index'] < page_index):
            raise HTTPException(409, '还有未确认的前置页，请按页序确认')
        updated = conn.execute(inventory_pages.update()
                               .where(inventory_pages.c.id == page['id'],
                                      inventory_pages.c.confirmed_at.is_(None))
                               .values(confirmed_by=actor, confirmed_at=utc_time())).rowcount
        if updated:
            log(conn, actor, 'inventory_confirm', f'{order_id}-{page_index}')
            if all(row['confirmed_at'] for row in pages if row['page_index'] != page_index):
                conn.execute(inventory_orders.update()
                             .where(inventory_orders.c.id == order_id)
                             .values(status=Inventory.COMPLETE, completed_at=utc_time()))
                log(conn, actor, 'inventory_complete', order_id)
        return detail(conn, order_id)


def active_pins(engine):
    """在办盘点单未确认页命中的配方版次，回收预检须为其留存。

    已确认页与已完成单不再留存；多个班的在办单各自留存，取并集。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            select(inventory_orders.c.id, inventory_orders.c.title,
                   inventory_pages.c.page_index, inventory_pages.c.items)
            .select_from(inventory_pages.join(
                inventory_orders, inventory_pages.c.order_id == inventory_orders.c.id))
            .where(inventory_orders.c.status == Inventory.ACTIVE,
                   inventory_pages.c.confirmed_at.is_(None))
            .order_by(inventory_orders.c.id, inventory_pages.c.page_index)).mappings().all()
    pins = {}
    holds = []
    for row in rows:
        for item in row['items']:
            pins.setdefault(item['formula_id'], set()).add(item['commit_seq'])
            holds.append({
                '盘点单': f"#{row['id']} {row['title']}",
                '未确认页': row['page_index'] + 1,
                '配方编号': item['formula_id'],
                '提交序号': item['commit_seq'],
            })
    return pins, holds
