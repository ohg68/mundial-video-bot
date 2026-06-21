"""
LayerCut Telegram Bot
Permite crear, gestionar y recibir videos directamente por Telegram.
"""
import asyncio
import logging
import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.models.project import (
    AudioLayerConfig,
    MusicLayerConfig,
    OverlayLayerConfig,
    ProjectConfig,
    SubtitleLayerConfig,
    VideoLayerConfig,
    VideoSource,
)
from app.services import layer_service, project_service, render_service

log = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
WAITING_TITLE, WAITING_TOPIC, WAITING_SOURCE = range(3)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_config(title: str, topic: str, source: str = "pexels") -> ProjectConfig:
    return ProjectConfig(
        title=title,
        topic=topic,
        aspect="9:16",
        video=VideoLayerConfig(source=VideoSource(source), clip_duration=4),
        audio=AudioLayerConfig(speed=1.1, volume=0.9),
        music=MusicLayerConfig(volume=0.25, fade_in=2, fade_out=3),
        subtitles=SubtitleLayerConfig(font_size=48, color="white", outline=True, position="bottom"),
        overlay=OverlayLayerConfig(),
    )


async def _send_video(bot, chat_id: int, path: Path, caption: str = ""):
    size_mb = path.stat().st_size / 1024 / 1024
    with open(path, "rb") as f:
        if size_mb <= 50:
            await bot.send_video(
                chat_id, f,
                caption=caption,
                parse_mode="Markdown",
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )
        else:
            await bot.send_document(
                chat_id, f,
                caption=f"{caption}\n_(archivo {size_mb:.0f}MB enviado como documento)_",
                parse_mode="Markdown",
                read_timeout=120,
                write_timeout=120,
            )


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _run_pipeline(bot, chat_id: int, title: str, topic: str, source: str):
    """Genera el video completo y lo entrega por Telegram."""
    project_id = None
    try:
        # 1. Crear proyecto
        config = _build_config(title, topic, source)
        meta = project_service.create_project(config, owner_id=None)
        project_id = meta["id"]

        await bot.send_message(
            chat_id,
            f"📋 Proyecto `{project_id}` creado\n\n✍️ *Generando guión con IA...*",
            parse_mode="Markdown",
        )

        # 2. Guión
        script = await layer_service.generate_script(project_id, config)
        config.script = script
        preview = script[:120].replace("\n", " ")
        await bot.send_message(
            chat_id,
            f"✅ Guión listo ({len(script)} caracteres)\n_{preview}..._\n\n"
            f"🎙️ *Generando audio TTS...*",
            parse_mode="Markdown",
        )

        # 3. Audio
        await layer_service.generate_audio(project_id, config)
        await bot.send_message(
            chat_id,
            "✅ Audio generado\n\n🎬 *Descargando y ensamblando video...*",
            parse_mode="Markdown",
        )

        # 4. Capa de video
        await layer_service.assemble_video_layer(project_id, config)
        await bot.send_message(
            chat_id,
            "✅ Video ensamblado\n\n⚡ *Renderizando (1080p)...*",
            parse_mode="Markdown",
        )

        # 5. Render final
        output = await render_service.render_final(project_id, quality="full")
        size_mb = output.stat().st_size / 1024 / 1024

        await bot.send_message(
            chat_id,
            f"✅ Render listo ({size_mb:.1f}MB)\n\n📤 *Enviando video...*",
            parse_mode="Markdown",
        )

        # 6. Entregar
        caption = f"🎬 *{title}*\n_{topic}_\n\n`ID: {project_id}`"
        await _send_video(bot, chat_id, output, caption=caption)

    except Exception as e:
        log.error(f"Pipeline error [chat={chat_id}]: {e}", exc_info=True)
        pid_info = f" (proyecto `{project_id}`)" if project_id else ""
        await bot.send_message(
            chat_id,
            f"❌ Error en el pipeline{pid_info}:\n```\n{str(e)[:400]}\n```",
            parse_mode="Markdown",
        )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *LayerCut Bot — Mundial 2026*\n\n"
        "Genera videos automáticamente sobre el Mundial.\n\n"
        "*Comandos:*\n"
        "• /nuevo — Crear y recibir un video\n"
        "• /listar — Ver tus proyectos\n"
        "• /descargar `ID` — Recibir render de un proyecto\n"
        "• /estado `ID` — Ver estado de un proyecto\n"
        "• /cancelar — Cancelar operación actual",
        parse_mode="Markdown",
    )


async def cmd_nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *Nuevo video*\n\n¿Cuál es el *título* del video?",
        parse_mode="Markdown",
    )
    return WAITING_TITLE


async def on_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Título: *{context.user_data['title']}*\n\n"
        "¿Cuál es el *tema o descripción* del video?\n"
        "_Ejemplo: Resumen del partido Argentina vs Francia, final del Mundial 2026_",
        parse_mode="Markdown",
    )
    return WAITING_TOPIC


async def on_tema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["topic"] = update.message.text.strip()

    keyboard = [
        [InlineKeyboardButton("📷 Fotos de Internet (Ken Burns)", callback_data="src_photos")],
        [InlineKeyboardButton("🎬 Video Clips (Pexels)", callback_data="src_pexels")],
        [InlineKeyboardButton("🔀 Mix Fotos + Video", callback_data="src_mixed_photos")],
    ]
    await update.message.reply_text(
        f"✅ Tema: *{context.user_data['topic']}*\n\n"
        "¿Qué tipo de *fuente de video* usamos?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WAITING_SOURCE


async def on_fuente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    src_map = {
        "src_photos": ("photos", "📷 Fotos de Internet"),
        "src_pexels": ("pexels", "🎬 Video Clips Pexels"),
        "src_mixed_photos": ("mixed_photos", "🔀 Mix Fotos + Video"),
    }
    source, label = src_map.get(query.data, ("pexels", "🎬 Video Clips Pexels"))

    title = context.user_data.get("title", "Video Mundial 2026")
    topic = context.user_data.get("topic", "Mundial 2026")
    chat_id = query.message.chat_id

    await query.edit_message_text(
        f"✅ Fuente: *{label}*\n\n"
        f"🚀 Iniciando pipeline...\n"
        f"Título: _{title}_\n"
        f"Tema: _{topic}_",
        parse_mode="Markdown",
    )

    asyncio.create_task(_run_pipeline(context.application.bot, chat_id, title, topic, source))
    return ConversationHandler.END


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END


async def cmd_listar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    projects = project_service.list_projects()
    if not projects:
        await update.message.reply_text(
            "No hay proyectos aún. Usa /nuevo para crear uno."
        )
        return

    lines = ["*Proyectos recientes:*\n"]
    for p in projects[:10]:
        layers = p.get("layers", {})
        video = layers.get("video", "empty")
        audio = layers.get("audio", "empty")
        icons = ("✅" if video == "ready" else "⏳") + ("🔊" if audio == "ready" else "")
        lines.append(f"{icons} `{p['id']}` — _{p['title']}_")

    lines.append("\n_Usa /descargar ID para recibir el render_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/estado ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    meta = project_service.get_project(project_id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{project_id}` no encontrado.", parse_mode="Markdown")
        return

    layers = meta.get("layers", {})
    info = meta.get("layer_info", {})
    icons = {"ready": "✅", "pending": "⏳", "error": "❌", "empty": "⬜"}

    lines = [f"*Proyecto: {meta['title']}*\n`{project_id}`\n"]
    for layer in ("video", "audio", "subtitles", "music", "overlay"):
        st = layers.get(layer, "empty")
        extra = ""
        li = info.get(layer, {})
        if layer == "video" and li.get("clips"):
            extra = f" ({li['clips']} clips, {li.get('source','?')})"
        elif layer == "audio" and li.get("voice"):
            extra = f" ({li.get('voice','')})"
        elif st == "error":
            extra = f"\n  └ {li.get('error','')[:80]}"
        lines.append(f"{icons.get(st,'?')} {layer}{extra}")

    output = Path("projects") / project_id / "output" / "final.mp4"
    preview = Path("projects") / project_id / "output" / "preview.mp4"
    if output.exists():
        size = output.stat().st_size / 1024 / 1024
        lines.append(f"\n🎬 Render listo: {size:.1f}MB")
    elif preview.exists():
        size = preview.stat().st_size / 1024 / 1024
        lines.append(f"\n🎬 Preview listo: {size:.1f}MB")
    else:
        lines.append("\n⬜ Sin render aún")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_descargar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/descargar ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    output = Path("projects") / project_id / "output" / "final.mp4"
    if not output.exists():
        output = Path("projects") / project_id / "output" / "preview.mp4"

    if not output.exists():
        await update.message.reply_text(
            f"❌ No hay render para `{project_id}`.\n"
            "Verifica con /estado que el video esté listo.",
            parse_mode="Markdown",
        )
        return

    meta = project_service.get_project(project_id)
    title = meta.get("title", project_id) if meta else project_id
    topic = meta.get("topic", "") if meta else ""

    msg = await update.message.reply_text("📤 Enviando video, espera...")
    caption = f"🎬 *{title}*\n_{topic}_\n\n`ID: {project_id}`"
    await _send_video(context.application.bot, update.effective_chat.id, output, caption)
    await msg.delete()


# ── App setup ─────────────────────────────────────────────────────────────────

def build_app(token: str) -> Application:
    application = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("nuevo", cmd_nuevo)],
        states={
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_titulo)],
            WAITING_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_tema)],
            WAITING_SOURCE: [CallbackQueryHandler(on_fuente, pattern="^src_")],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("listar", cmd_listar))
    application.add_handler(CommandHandler("estado", cmd_estado))
    application.add_handler(CommandHandler("descargar", cmd_descargar))
    application.add_handler(conv)

    return application


async def start_polling(token: str):
    """Inicia el bot en background (no bloquea uvicorn)."""
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("✅ Telegram bot polling iniciado")
