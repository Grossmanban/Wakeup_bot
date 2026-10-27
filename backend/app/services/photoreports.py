"""Фотоотчёты в Telegram (ТЗ 17). Фото и данные — в системе; Telegram — дополнительная копия."""

import logging
from datetime import date, timedelta
from html import escape
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, timeutil
from app.errors import AppError, Conflict, NotFound
from app.models import (
    DayPost,
    PhotoDelivery,
    PhotoKind,
    PhotoRoute,
    Product,
    Task,
    TaskPhoto,
    TaskStatus,
    TelegramOutbox,
    TgGroup,
    User,
)
from app.services import audit, notify, outbox
from app.services.access import Actor
from app.services.audit import Category

log = logging.getLogger(__name__)

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
STATUS_RU = {
    TaskStatus.ACTIVE: "Выполняется",
    TaskStatus.MANUAL_REVIEW: "Ожидает ручного решения",
    TaskStatus.MODERATION: "На модерации",
    TaskStatus.APPROVED: "Допущено к оплате",
    TaskStatus.REJECTED: "Отклонено",
    TaskStatus.PAID: "Оплачено",
    TaskStatus.CANCELLED: "Отменено",
}


def day_title(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} года"


# --- маршруты -----------------------------------------------------------------------


def route_out(r: PhotoRoute) -> dict[str, Any]:
    return {
        k: getattr(r, k)
        for k in ("id", "kind", "product_id", "group_id", "is_active", "created_at")
    }


async def save_route(
    session: AsyncSession,
    actor: Actor,
    *,
    kind: str,
    product_id: int | None,
    group_id: int,
    is_active: bool,
) -> PhotoRoute:
    actor.require_owner()
    if kind not in ("product", "general", "summary", "extra"):
        raise AppError("bad_kind", "Неизвестный вид маршрута")
    if kind == "product" and product_id is None:
        raise AppError("product_required", "Для маршрута продукта выберите продукт")
    if kind in ("general", "summary"):
        product_id = None
    if product_id is not None and await session.get(Product, product_id) is None:
        raise NotFound("Продукт не найден")
    if await session.get(TgGroup, group_id) is None:
        raise NotFound("Группа не найдена")
    if kind == "product":
        # У продукта одна основная группа: прежний маршрут продукта заменяется.
        existing = await session.scalar(
            select(PhotoRoute).where(
                PhotoRoute.kind == "product", PhotoRoute.product_id == product_id
            )
        )
    else:
        existing = await session.scalar(
            select(PhotoRoute).where(
                PhotoRoute.kind == kind,
                PhotoRoute.group_id == group_id,
                PhotoRoute.product_id.is_(None)
                if product_id is None
                else PhotoRoute.product_id == product_id,
            )
        )
    old = route_out(existing) if existing else None
    if existing:
        existing.group_id, existing.is_active = group_id, is_active
        route = existing
    else:
        route = PhotoRoute(kind=kind, product_id=product_id, group_id=group_id, is_active=is_active)
        session.add(route)
    await session.flush()
    audit.record(
        session,
        category=Category.SETTINGS,
        action="photo_route.saved",
        actor_id=actor.id,
        object_type="photo_route",
        object_id=route.id,
        old=old,
        new=route_out(route),
    )
    await session.commit()
    return route


async def delete_route(session: AsyncSession, actor: Actor, route_id: int) -> None:
    actor.require_owner()
    r = await session.get(PhotoRoute, route_id)
    if r is None:
        raise NotFound("Маршрут не найден")
    audit.record(
        session,
        category=Category.SETTINGS,
        action="photo_route.deleted",
        actor_id=actor.id,
        object_type="photo_route",
        object_id=r.id,
        old=route_out(r),
    )
    await session.delete(r)
    await session.commit()


async def _target_groups(session: AsyncSession, product_id: int) -> tuple[list[TgGroup], bool]:
    """Группы для фото продукта и признак, настроен ли маршрут самого продукта."""
    routes = (
        (
            await session.execute(
                select(PhotoRoute).where(
                    PhotoRoute.is_active.is_(True),
                    or_(
                        (PhotoRoute.kind == "product") & (PhotoRoute.product_id == product_id),
                        PhotoRoute.kind == "general",
                        (PhotoRoute.kind == "extra")
                        & (PhotoRoute.product_id.is_(None) | (PhotoRoute.product_id == product_id)),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    has_product = any(r.kind == "product" for r in routes)
    ids = {r.group_id for r in routes}
    groups = (
        (await session.execute(select(TgGroup).where(TgGroup.id.in_(ids or {0})))).scalars().all()
    )
    return list(groups), has_product


# --- фото ---------------------------------------------------------------------------


async def caption(session: AsyncSession, photo: TaskPhoto) -> str:
    task = await session.get(Task, photo.task_id)
    product = await session.get(Product, photo.product_id)
    user = await session.get(User, photo.user_id)
    if photo.kind == PhotoKind.FINAL:
        stage = "Финальный фотоотчёт"
    else:
        flow = product.flow or []
        title = (
            flow[photo.stage].get("title")
            if photo.stage is not None and photo.stage < len(flow)
            else None
        )
        stage = f"Этап {(photo.stage or 0) + 1}" + (f": {title}" if title else "")
    return (
        f"<b>Фотоотчёт — {escape(product.name)}</b>\n"
        f"Дата и время: {timeutil.format_msk(photo.uploaded_at)}\n"
        f"Сотрудник: {escape(user.full_name)}\n"
        f"ID сотрудника: {user.id}\n"
        f"ID задания: {escape(task.external_id)}\n"
        f"Единиц: {task.units}\n"
        f"Этап: {escape(stage)}\n"
        f"Статус: {STATUS_RU.get(task.status, task.status.value)}"
    )


async def enqueue_photo(session: AsyncSession, photo: TaskPhoto) -> int:
    """Поставить отправку фото в группы маршрутов. Без маршрута — ошибка конфигурации владельцу."""
    day = timeutil.msk_date(photo.uploaded_at)
    groups, has_product = await _target_groups(session, photo.product_id)
    if not has_product:
        product = await session.get(Product, photo.product_id)
        await notify.notify_holders(
            session,
            None,
            dedup_key=f"photo_route_missing:{product.id}:{day.isoformat()}",
            text=(
                "<b>Ошибка конфигурации фотоотчётов</b>\n"
                f"Для продукта «{escape(product.name)}» не назначена группа. "
                "Фото сохранены в системе, в группу продукта они не отправляются."
            ),
            button="Настроить",
        )
        audit.record(
            session,
            category=Category.SYSTEM,
            action="photo_route.missing",
            actor_id=None,
            object_type="product",
            object_id=product.id,
            reason="Не настроен маршрут фотоотчётов продукта",
            result="error",
        )
    text = await caption(session, photo) if groups else ""
    queued = 0
    for g in groups:
        if g.status != "connected":
            continue
        exists = await session.scalar(
            select(PhotoDelivery.id).where(
                PhotoDelivery.photo_id == photo.id, PhotoDelivery.group_id == g.id
            )
        )
        if exists:
            continue
        d = PhotoDelivery(photo_id=photo.id, group_id=g.id, day=day)
        session.add(d)
        await session.flush()
        await _enqueue_delivery(session, d, photo, g, text, attempt=1)
        queued += 1
    return queued


async def _enqueue_delivery(
    session: AsyncSession, d: PhotoDelivery, photo: TaskPhoto, g: TgGroup, text: str, attempt: int
) -> None:
    header = await session.scalar(
        select(DayPost.tg_message_id).where(
            DayPost.group_id == g.id,
            DayPost.day == d.day,
            DayPost.kind == "header",
            DayPost.tg_message_id.is_not(None),
        )
    )
    payload: dict[str, Any] = {
        "photo_path": str(storage.photo_path(photo.path)),
        "caption": text,
        "parse_mode": "HTML",
    }
    if header:
        # Связь с заголовком дня ответом на сообщение (ТЗ 17.3).
        payload["reply_to_message_id"] = header
    await outbox.enqueue_raw(
        session,
        dedup_key=f"photo_delivery:{d.id}:{attempt}",
        chat_id=g.chat_id,
        method="sendPhoto",
        payload=payload,
        ref_type="photo_delivery",
        ref_id=d.id,
    )


async def on_delivery_result(
    session: AsyncSession, delivery_id: int, ok: bool, message_id, error
) -> None:
    d = await session.get(PhotoDelivery, delivery_id)
    if d is None:
        return
    if ok:
        d.status, d.tg_message_id, d.error, d.sent_at = "sent", message_id, None, timeutil.now_utc()
        return
    d.status, d.error = "failed", error
    g = await session.get(TgGroup, d.group_id)
    # Окончательные ошибки передаются владельцу (ТЗ 17.6).
    await notify.notify_holders(
        session,
        None,
        dedup_key=f"photo_delivery_failed:{d.id}:{d.error[:20] if d.error else ''}",
        text=(
            f"<b>Фотоотчёт не отправлен</b> в «{escape(g.title)}»: {escape(error or '')}\n"
            "Фото сохранено в системе, его можно отправить повторно."
        ),
        button="Открыть",
    )


async def retry_delivery(session: AsyncSession, actor: Actor, delivery_id: int) -> PhotoDelivery:
    actor.require_owner()
    d = await session.get(PhotoDelivery, delivery_id, with_for_update=True)
    if d is None:
        raise NotFound("Отправка не найдена")
    if d.status != "failed":
        raise Conflict("not_failed", "Повторить можно только неудачную отправку")
    photo = await session.get(TaskPhoto, d.photo_id)
    g = await session.get(TgGroup, d.group_id)
    attempt = 1 + int(
        await session.scalar(
            select(func.count()).where(
                TelegramOutbox.ref_type == "photo_delivery", TelegramOutbox.ref_id == d.id
            )
        )
    )
    d.status, d.error = "queued", None
    await _enqueue_delivery(session, d, photo, g, await caption(session, photo), attempt)
    audit.record(
        session,
        category=Category.PHOTOS,
        action="photo_delivery.retry",
        actor_id=actor.id,
        object_type="photo_delivery",
        object_id=d.id,
    )
    await session.commit()
    return d


# --- заголовок и сводка дня ---------------------------------------------------------


async def _post_groups(session: AsyncSession, kind: str) -> list[TgGroup]:
    if kind == "summary":
        q = select(PhotoRoute.group_id).where(
            PhotoRoute.kind == "summary", PhotoRoute.is_active.is_(True)
        )
    else:
        q = select(PhotoRoute.group_id).where(
            PhotoRoute.kind.in_(["product", "general", "extra"]), PhotoRoute.is_active.is_(True)
        )
    ids = set((await session.execute(q)).scalars())
    rows = await session.execute(
        select(TgGroup).where(TgGroup.id.in_(ids or {0}), TgGroup.status == "connected")
    )
    return list(rows.scalars())


async def post_headers(session: AsyncSession, day: date) -> int:
    """Один заголовок «Фотоотчёты за …» на группу в день; повторный запуск не дублирует."""
    n = 0
    for g in await _post_groups(session, "header"):
        exists = await session.scalar(
            select(DayPost.id).where(
                DayPost.group_id == g.id, DayPost.day == day, DayPost.kind == "header"
            )
        )
        if exists:
            continue
        post = DayPost(group_id=g.id, day=day, kind="header", version=1)
        session.add(post)
        await session.flush()
        await outbox.enqueue_raw(
            session,
            dedup_key=f"day_post:{post.id}",
            chat_id=g.chat_id,
            method="sendMessage",
            payload={"text": f"<b>Фотоотчёты за {day_title(day)}</b>", "parse_mode": "HTML"},
            ref_type="day_post",
            ref_id=post.id,
        )
        n += 1
    await session.commit()
    return n


async def day_stats(session: AsyncSession, day: date) -> dict[str, Any]:
    """Итоги дня — из базы данных, не из сообщений Telegram (ТЗ 17.4)."""
    lo, hi = timeutil.msk_day_bounds(day, day)
    photos = (
        (
            await session.execute(
                select(TaskPhoto).where(TaskPhoto.uploaded_at >= lo, TaskPhoto.uploaded_at < hi)
            )
        )
        .scalars()
        .all()
    )
    tasks = (
        (await session.execute(select(Task).where(Task.completed_at >= lo, Task.completed_at < hi)))
        .scalars()
        .all()
    )
    failed = await session.scalar(
        select(func.count()).where(PhotoDelivery.day == day, PhotoDelivery.status == "failed")
    )
    names = dict((await session.execute(select(Product.id, Product.name))).all())

    def units(statuses) -> int:
        return sum(t.units for t in tasks if t.status in statuses)

    by_product: dict[str, dict[str, int]] = {}
    for p in photos:
        by_product.setdefault(names.get(p.product_id, "?"), {"photos": 0, "units": 0})[
            "photos"
        ] += 1
    for t in tasks:
        if t.status in (
            TaskStatus.MODERATION,
            TaskStatus.APPROVED,
            TaskStatus.PAID,
            TaskStatus.REJECTED,
        ):
            by_product.setdefault(names.get(t.product_id, "?"), {"photos": 0, "units": 0})[
                "units"
            ] += t.units
    return {
        "photos": len(photos),
        "employees": len({p.user_id for p in photos}),
        "products": len({p.product_id for p in photos}),
        "unique_tasks": len({p.task_id for p in photos}),
        "units": units(
            (TaskStatus.MODERATION, TaskStatus.APPROVED, TaskStatus.PAID, TaskStatus.REJECTED)
        ),
        "pending": units((TaskStatus.MODERATION,)),
        "approved": units((TaskStatus.APPROVED, TaskStatus.PAID)),
        "rejected": units((TaskStatus.REJECTED,)),
        "delivery_errors": int(failed or 0),
        "by_product": dict(sorted(by_product.items())),
    }


LABELS = (
    ("photos", "Фотоотчётов"),
    ("employees", "Сотрудников"),
    ("products", "Продуктов"),
    ("unique_tasks", "Уникальных заданий"),
    ("units", "Выполненных единиц"),
    ("pending", "Ожидают модерации"),
    ("approved", "Допущено к оплате"),
    ("rejected", "Отклонено"),
    ("delivery_errors", "Ошибок отправки"),
)


def summary_text(day: date, s: dict[str, Any], correction: str | None = None) -> str:
    head = (
        f"Корректировка итогов фотоотчётов — {day_title(day)}"
        if correction
        else f"Итоги фотоотчётов — {day_title(day)}"
    )
    lines = [f"<b>{head}</b>"]
    if correction:
        lines.append(f"Причина: {escape(correction)}")
    lines += [f"• {title}: {s[key]}" for key, title in LABELS]
    if s["by_product"]:
        lines.append("")
        lines += [
            f"{escape(name)}: фото {v['photos']}, единиц {v['units']}"
            for name, v in s["by_product"].items()
        ]
    return "\n".join(lines)


async def post_summaries(session: AsyncSession, day: date) -> int:
    """Сводка публикуется и при нулевых значениях (ТЗ 17.4)."""
    stats = await day_stats(session, day)
    n = 0
    for g in await _post_groups(session, "summary"):
        exists = await session.scalar(
            select(DayPost.id).where(
                DayPost.group_id == g.id, DayPost.day == day, DayPost.kind == "summary"
            )
        )
        if exists:
            continue
        post = DayPost(group_id=g.id, day=day, kind="summary", version=1, content=stats)
        session.add(post)
        await session.flush()
        await _enqueue_post(session, post, g, summary_text(day, stats))
        n += 1
    await session.commit()
    return n


async def _enqueue_post(session: AsyncSession, post: DayPost, g: TgGroup, text: str) -> None:
    await outbox.enqueue_raw(
        session,
        dedup_key=f"day_post:{post.id}",
        chat_id=g.chat_id,
        method="sendMessage",
        payload={"text": text, "parse_mode": "HTML"},
        ref_type="day_post",
        ref_id=post.id,
    )


def _diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    parts = [
        f"{title}: {old.get(key)} → {new[key]}" for key, title in LABELS if old.get(key) != new[key]
    ]
    return "; ".join(parts) or "изменилась разбивка по продуктам"


async def post_corrections(session: AsyncSession, today: date, days_back: int = 7) -> int:
    """Если итоги прошлых дней изменились (модерация, поздние отчёты), отправить корректировку,
    сохранив прежнюю версию (ТЗ 17.5)."""
    n = 0
    for i in range(1, days_back + 1):
        day = today - timedelta(days=i)
        latest_by_group: dict[int, DayPost] = {}
        for p in (
            await session.execute(
                select(DayPost)
                .where(DayPost.day == day, DayPost.kind == "summary")
                .order_by(DayPost.version)
            )
        ).scalars():
            latest_by_group[p.group_id] = p
        if not latest_by_group:
            continue
        stats = await day_stats(session, day)
        for gid, last in latest_by_group.items():
            if last.content == stats:
                continue
            g = await session.get(TgGroup, gid)
            reason = _diff(last.content, stats)
            post = DayPost(
                group_id=gid,
                day=day,
                kind="summary",
                version=last.version + 1,
                content=stats,
                reason=reason,
            )
            session.add(post)
            await session.flush()
            await _enqueue_post(session, post, g, summary_text(day, stats, reason))
            audit.record(
                session,
                category=Category.PHOTOS,
                action="daily_summary.corrected",
                actor_id=None,
                object_type="day_post",
                object_id=post.id,
                old=last.content,
                new=stats,
                reason=reason,
            )
            n += 1
    await session.commit()
    return n


async def on_post_result(session: AsyncSession, post_id: int, ok: bool, message_id, error) -> None:
    post = await session.get(DayPost, post_id)
    if post is not None:
        post.status = "sent" if ok else "failed"
        post.tg_message_id = message_id if ok else None


async def overview(session: AsyncSession, actor: Actor, day: date) -> dict[str, Any]:
    actor.require_owner()
    routes = (await session.execute(select(PhotoRoute).order_by(PhotoRoute.id))).scalars().all()
    deliveries = (
        await session.execute(
            select(PhotoDelivery, TgGroup.title)
            .join(TgGroup, TgGroup.id == PhotoDelivery.group_id)
            .where(PhotoDelivery.day == day)
            .order_by(PhotoDelivery.id.desc())
            .limit(300)
        )
    ).all()
    posts = (
        await session.execute(
            select(DayPost, TgGroup.title)
            .join(TgGroup, TgGroup.id == DayPost.group_id)
            .where(DayPost.day == day)
        )
    ).all()
    return {
        "routes": [route_out(r) for r in routes],
        "stats": await day_stats(session, day),
        "deliveries": [
            {
                "id": d.id,
                "photo_id": d.photo_id,
                "group": t,
                "status": d.status,
                "error": d.error,
                "sent_at": d.sent_at,
            }
            for d, t in deliveries
        ],
        "posts": [
            {
                "id": p.id,
                "group": t,
                "kind": p.kind,
                "version": p.version,
                "status": p.status,
                "reason": p.reason,
            }
            for p, t in posts
        ],
    }
