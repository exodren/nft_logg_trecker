"""Admin commands, only for ADMIN_TELEGRAM_IDS. No arbitrary commands or code execution."""
from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from app.bot import formatters as fmt
from app.database.types import utcnow
from app.services import queries



class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, deps) -> bool:
        return message.from_user is not None and message.from_user.id in deps.settings.admin_telegram_ids



def _ts(value) -> str:
    return f"{value:%d.%m %H:%M:%S} UTC" if value else "—"


async def status_text(deps) -> str:
    lines = ["🛠 <b>Админ-панель</b>", ""]
    sc = deps.scanner
    if sc is None:
        lines.append("MTProto: не настроен")
    else:
        st = sc.status
        lines += [f"MTProto: {'подключён' if deps.mtproto_connected() else 'нет соединения'}",
                  f"Сканер: {'работает' if st.running else 'остановлен'}, циклов {st.cycles}",
                  f"Последний цикл: {_ts(st.last_cycle_at)}, успешный: {_ts(st.last_success_at)}",
                  f"Последняя ошибка: {escape(st.last_error or '—')}"]
    p = deps.provider
    lines.append("Kartoshka API: " + ("не настроен (нет токена)" if p is None else
                                      "готов" if p.ready else "не готов (схема ответа не подтверждена или 401)"))
    async with deps.sessions() as s:
        counts = await queries.admin_counts(s)
        stats = await queries.stats(s, utcnow())
    q = await deps.queue.counts()
    lines += ["", f"Очередь: {', '.join(f'{k} {v}' for k, v in sorted(q.items())) or 'пусто'}",
              f"Пользователи: {counts['users']} (активных {counts['active_users']})",
              f"Уведомления: ожидают {counts['notifications_pending']}, ошибок {counts['notifications_failed']}",
              f"Ошибок обработки: {stats.processing_errors}"]
    if deps.usage is not None:
        lines += ["", fmt.usage_text(await deps.usage.report())]
    if deps.supervisor is not None:
        lines += ["", "Компоненты: " + ", ".join(f"{n} {'✅' if ok else '⛔'}" for n, ok in deps.supervisor.state().items())]
    lines += ["", "/errors /retry_failed /retry_deferred /resume_api /restart &lt;компонент&gt;"]
    return "\n".join(lines)


async def admin_cmd(message: Message, deps) -> None:
    await message.answer(await status_text(deps))


async def errors_cmd(message: Message, deps) -> None:
    async with deps.sessions() as s:
        errors = await queries.recent_errors(s)
    await message.answer("\n".join(escape(e[:300]) for e in errors) or "Ошибок нет.")


async def retry_failed_cmd(message: Message, deps) -> None:
    await message.answer(f"Возвращено в очередь: {await deps.queue.retry_failed()}")


async def retry_deferred_cmd(message: Message, deps) -> None:
    await message.answer(f"Отложенных задач разбужено: {await deps.queue.wake_deferred()}")


async def resume_api_cmd(message: Message, deps) -> None:
    """After a top-up or a budget change: allow paid requests again and wake deferred lookups."""
    if deps.usage is None:
        await message.answer("Kartoshka API не настроен.")
        return
    await deps.usage.resume()
    woken = await deps.queue.wake_deferred()
    await message.answer(f"Платные запросы разрешены (в пределах бюджета). Разбужено задач: {woken}")


async def restart_cmd(message: Message, command: CommandObject, deps) -> None:
    name = (command.args or "").strip()
    if deps.supervisor is None or name not in deps.supervisor.names:
        names = ", ".join(deps.supervisor.names) if deps.supervisor else "—"
        await message.answer(f"Использование: /restart &lt;{escape(names)}&gt;")
        return
    await deps.supervisor.restart(name)
    await message.answer(f"Компонент {escape(name)} перезапущен.")


def build_router() -> Router:
    """A fresh router per dispatcher: aiogram attaches a router to one parent only (bot restarts)."""
    r = Router(name="admin")
    r.message.filter(IsAdmin())
    r.message.register(admin_cmd, Command("admin"))
    r.message.register(errors_cmd, Command("errors"))
    r.message.register(retry_failed_cmd, Command("retry_failed"))
    r.message.register(retry_deferred_cmd, Command("retry_deferred"))
    r.message.register(resume_api_cmd, Command("resume_api"))
    r.message.register(restart_cmd, Command("restart"))
    return r
