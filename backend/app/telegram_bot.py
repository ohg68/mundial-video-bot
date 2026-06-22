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
from app.database import SessionLocal, Project

log = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(WAITING_TITLE, WAITING_TOPIC, WAITING_SOURCE,
 WAITING_LAYER_ACTION, WAITING_AUDIO_VOICE, WAITING_AUDIO_SPEED,
 WAITING_VIDEO_SOURCE, WAITING_FILE_UPLOAD,
 WAITING_SCRIPT_EDIT) = range(9)

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
            f"📋 Proyecto creado\n\n"
            f"*ID: `{project_id}`* ← copia esto\n\n"
            f"✍️ *Generando guión con IA...*",
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

        # 7. Instrucciones post-entrega
        await bot.send_message(
            chat_id,
            f"✅ ¡Video listo!\n\n"
            f"*Próximos pasos:*\n"
            f"• `/capas {project_id}` — editar capas\n"
            f"• `/guion {project_id}` — editar guión\n"
            f"• `/ultimos` — ver últimos 10 proyectos",
            parse_mode="Markdown",
        )

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
        "*Crear y gestionar:*\n"
        "• /nuevo — Crear nuevo video\n"
        "• /ultimos — Ver últimos proyectos (IDs copiables)\n"
        "• /descargar `ID` — Recibir render\n"
        "• /estado `ID` — Ver estado detallado\n\n"
        "*Edición:*\n"
        "• /capas `ID` — Editar capas (video/audio/subs)\n"
        "• /guion `ID` — Editar guión\n\n"
        "Tip: Copia el ID que aparece al crear un video",
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


async def cmd_ultimos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostrar últimos 10 proyectos con IDs copiables."""
    projects = project_service.list_projects()
    if not projects:
        await update.message.reply_text(
            "No hay proyectos aún. Usa /nuevo para crear uno."
        )
        return

    lines = ["*Últimos proyectos (copia el ID):*\n"]
    for p in projects[:10]:
        layers = p.get("layers", {})
        video = layers.get("video", "empty")
        audio = layers.get("audio", "empty")
        icons = ("✅" if video == "ready" else "⏳") + ("🔊" if audio == "ready" else "")

        # Formato: ID copiable + título
        lines.append(f"{icons} `{p['id']}`\n   _{p['title']}_")

    lines.append("\n*Usa:*\n")
    lines.append("• `/capas ID` — editar capas\n")
    lines.append("• `/guion ID` — editar guión\n")
    lines.append("• `/estado ID` — ver estado")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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


async def cmd_guion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ver y editar el guión de un proyecto."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/guion ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    meta = project_service.get_project(project_id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{project_id}` no encontrado.", parse_mode="Markdown")
        return

    config = meta.get("config", {})
    # Script puede ser None o una cadena vacía
    script = config.get("script") if isinstance(config, dict) else None

    if not script:
        script = "(sin guión aún — genera uno con 🤖)"

    # Mostrar guión con opción de editar
    preview = script[:800] if len(script) > 800 else script
    truncated = "..." if len(script) > 800 else ""

    keyboard = [
        [InlineKeyboardButton("✏️ Editar guión", callback_data=f"edit_script_{project_id}")],
        [InlineKeyboardButton("🤖 Regenerar con IA", callback_data=f"regen_script_{project_id}")],
    ]

    try:
        await update.message.reply_text(
            f"📝 *Guión: {meta['title']}*\n\n```\n{preview}{truncated}\n```",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"Error en cmd_guion: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Error: {str(e)[:100]}",
            parse_mode="Markdown",
        )


async def on_edit_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Iniciar edición de guión."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    context.user_data["edit_script_project"] = project_id

    await query.edit_message_text(
        "✏️ *Editar guión*\n\n"
        "Envía el nuevo guión (máx 2000 caracteres). "
        "Cuando termines, envía /listo para guardar.",
        parse_mode="Markdown",
    )
    return WAITING_SCRIPT_EDIT


async def on_script_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibir y guardar nuevo guión."""
    script = update.message.text.strip()
    project_id = context.user_data.get("edit_script_project")

    if not project_id:
        await update.message.reply_text("❌ Error: proyecto no encontrado.")
        return ConversationHandler.END

    if len(script) > 2000:
        await update.message.reply_text("❌ El guión es muy largo (máx 2000 caracteres).")
        return WAITING_SCRIPT_EDIT

    # Guardar nuevo guión
    meta = project_service.get_project(project_id)
    if not meta:
        await update.message.reply_text("❌ Proyecto no encontrado.")
        return ConversationHandler.END

    config_dict = meta.get("config", {})
    config_dict["script"] = script

    # Actualizar en DB
    import json
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.config = json.dumps(config_dict, ensure_ascii=False)
            db.commit()
    finally:
        db.close()

    await update.message.reply_text(
        f"✅ Guión guardado ({len(script)} caracteres)\n\n"
        f"💡 Ahora puedes:\n"
        f"• `/capas {project_id}` — editar capas\n"
        f"• `/render {project_id}` — renderizar con nuevo guión",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def on_regen_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerar guión con IA."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    chat_id = query.message.chat_id

    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    config = ProjectConfig(**meta.get("config", {}))
    await query.edit_message_text("🤖 Regenerando guión con IA...")

    asyncio.create_task(_regen_script(context.application.bot, chat_id, project_id, config))


async def _regen_script(bot, chat_id: int, project_id: str, config):
    """Regenerar script y notificar."""
    try:
        script = await layer_service.generate_script(project_id, config)
        preview = script[:200].replace("\n", " ")
        await bot.send_message(
            chat_id,
            f"✅ Guión regenerado:\n\n_{preview}..._\n\n"
            f"`/capas {project_id}` para editar más capas",
            parse_mode="Markdown",
        )
    except Exception as e:
        await bot.send_message(
            chat_id,
            f"❌ Error regenerando guión: {str(e)[:150]}",
            parse_mode="Markdown",
        )


async def cmd_capas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostrar y editar capas individuales de un proyecto."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/capas ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    meta = project_service.get_project(project_id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{project_id}` no encontrado.", parse_mode="Markdown")
        return

    layers = meta.get("layers", {})
    icons = {"ready": "✅", "pending": "⏳", "error": "❌", "empty": "⬜"}

    keyboard = []
    for layer in ("video", "audio", "subtitles", "music", "overlay"):
        st = layers.get(layer, "empty")
        icon = icons.get(st, "?")
        keyboard.append([InlineKeyboardButton(f"{icon} {layer.capitalize()}", callback_data=f"layer_{layer}_{project_id}")])

    await update.message.reply_text(
        f"🎬 *Capas de {meta['title']}*\n`{project_id}`\n\nSelecciona una capa para ver opciones:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_layer_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostrar opciones para una capa seleccionada."""
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    layer = data_parts[1]
    project_id = "_".join(data_parts[2:])

    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    layers = meta.get("layers", {})
    info = meta.get("layer_info", {})
    st = layers.get(layer, "empty")
    li = info.get(layer, {})

    # Descripción de la capa
    desc = f"*{layer.upper()}* — Estado: **{st}**"
    if layer == "audio" and li.get("voice"):
        desc += f"\nVoz: _{li.get('voice')}_"
    elif layer == "video" and li.get("clips"):
        desc += f"\nClips: {li.get('clips')}, Fuente: _{li.get('source')}_"
    elif st == "error":
        desc += f"\n⚠️ {li.get('error', '')[:80]}"

    # Botones de acción
    keyboard = []
    if st != "empty":
        keyboard.append([InlineKeyboardButton(f"📥 Descargar {layer}", callback_data=f"dl_{layer}_{project_id}")])

    if layer == "audio":
        keyboard.append([InlineKeyboardButton("🎙️ Editar voz/velocidad", callback_data=f"edit_audio_{project_id}")])
        keyboard.append([InlineKeyboardButton("🔄 Regenerar audio", callback_data=f"regen_audio_{project_id}")])
    elif layer == "video":
        keyboard.append([InlineKeyboardButton("🎬 Cambiar fuente", callback_data=f"edit_video_{project_id}")])
        keyboard.append([InlineKeyboardButton("🔄 Regenerar video", callback_data=f"regen_video_{project_id}")])
    elif layer == "subtitles":
        keyboard.append([InlineKeyboardButton("✏️ Regenerar subtítulos", callback_data=f"regen_subs_{project_id}")])
    else:
        keyboard.append([InlineKeyboardButton("📤 Subir archivo", callback_data=f"upload_{layer}_{project_id}")])

    keyboard.append([InlineKeyboardButton("◀️ Volver", callback_data=f"back_capas_{project_id}")])

    await query.edit_message_text(
        desc,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_layer_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Descargar una capa específica."""
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    layer = data_parts[1]
    project_id = "_".join(data_parts[2:])

    LAYER_FILES = {
        "video": "video.mp4",
        "audio": "narration.mp3",
        "music": "music.mp3",
        "subtitles": "subtitles.srt",
        "overlay": "overlay.png",
    }

    path = Path("projects") / project_id / layer / LAYER_FILES.get(layer, "")
    if not path.exists():
        await query.edit_message_text(f"❌ Archivo de {layer} no encontrado.")
        return

    await query.edit_message_text(f"📤 Enviando {layer}...")
    chat_id = query.message.chat_id
    try:
        with open(path, "rb") as f:
            await context.application.bot.send_document(
                chat_id, f,
                filename=path.name,
                caption=f"`{layer}` de proyecto `{project_id}`",
                parse_mode="Markdown",
                read_timeout=120,
                write_timeout=120,
            )
    except Exception as e:
        await context.application.bot.send_message(
            chat_id,
            f"❌ Error al enviar {layer}: {str(e)[:100]}",
            parse_mode="Markdown",
        )


async def on_edit_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu para editar audio (voz, velocidad)."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    context.user_data["edit_project"] = project_id

    keyboard = [
        [InlineKeyboardButton("🎙️ Cambiar voz", callback_data=f"voice_menu_{project_id}")],
        [InlineKeyboardButton("⏱️ Cambiar velocidad (1.0 = normal)", callback_data=f"speed_input_{project_id}")],
        [InlineKeyboardButton("◀️ Volver", callback_data=f"back_capas_{project_id}")],
    ]
    await query.edit_message_text(
        "🎙️ *Editar audio*\n\n¿Qué quieres cambiar?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_voice_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Selector de voces."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    context.user_data["edit_project"] = project_id

    voices = [
        ("es-ES-AlvaroNeural", "🇪🇸 Alvaro (español)"),
        ("es-ES-ElviraNeural", "🇪🇸 Elvira (español)"),
        ("pt-PT-DuarteNeural", "🇵🇹 Duarte (portugués)"),
        ("pt-PT-InesNeural", "🇵🇹 Inés (portugués)"),
    ]

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"setvoice_{voice_id}_{project_id}")]
        for voice_id, label in voices
    ]
    keyboard.append([InlineKeyboardButton("◀️ Volver", callback_data=f"edit_audio_{project_id}")])

    await query.edit_message_text(
        "🎙️ Elige una voz:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def on_regen_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerar audio con configuración actual."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    chat_id = query.message.chat_id

    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    config = ProjectConfig(**meta.get("config", {}))
    await query.edit_message_text("🎙️ Regenerando audio...")

    try:
        asyncio.create_task(_regen_layer(context.application.bot, chat_id, project_id, "audio", config))
    except Exception as e:
        await context.application.bot.send_message(
            chat_id,
            f"❌ Error: {str(e)[:100]}",
            parse_mode="Markdown",
        )


async def on_regen_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerar video."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    chat_id = query.message.chat_id

    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    config = ProjectConfig(**meta.get("config", {}))
    await query.edit_message_text("🎬 Regenerando video...")

    try:
        asyncio.create_task(_regen_layer(context.application.bot, chat_id, project_id, "video", config))
    except Exception as e:
        await context.application.bot.send_message(
            chat_id,
            f"❌ Error: {str(e)[:100]}",
            parse_mode="Markdown",
        )


async def on_regen_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Regenerar subtítulos."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    chat_id = query.message.chat_id

    await query.edit_message_text("📝 Regenerando subtítulos...")

    try:
        asyncio.create_task(_regen_layer(context.application.bot, chat_id, project_id, "subtitles", None))
    except Exception as e:
        await context.application.bot.send_message(
            chat_id,
            f"❌ Error: {str(e)[:100]}",
            parse_mode="Markdown",
        )


async def on_setvoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cambiar voz de audio y regenerar."""
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    voice_id = data_parts[1]
    project_id = "_".join(data_parts[2:])
    chat_id = query.message.chat_id

    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    # Actualizar config con nueva voz
    import json
    config_dict = json.loads(meta.get("config_json", "{}")) if isinstance(meta.get("config_json"), str) else meta.get("config", {})
    if "audio" in config_dict:
        from app.models.project import VoiceModel
        config_dict["audio"]["voice"] = voice_id

    config = ProjectConfig(**config_dict)

    await query.edit_message_text(f"🎙️ Cambiando voz a `{voice_id}`...", parse_mode="Markdown")
    asyncio.create_task(_regen_layer(context.application.bot, chat_id, project_id, "audio", config))


async def _regen_layer(bot, chat_id: int, project_id: str, layer: str, config):
    """Regenerar una capa y notificar."""
    try:
        if layer == "audio":
            await layer_service.generate_audio(project_id, config)
            await bot.send_message(chat_id, f"✅ Audio regenerado", parse_mode="Markdown")
        elif layer == "video":
            await layer_service.assemble_video_layer(project_id, config)
            await bot.send_message(chat_id, f"✅ Video regenerado", parse_mode="Markdown")
        elif layer == "subtitles":
            await layer_service.generate_subtitles(project_id)
            await bot.send_message(chat_id, f"✅ Subtítulos regenerados", parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(
            chat_id,
            f"❌ Error regenerando {layer}: {str(e)[:200]}",
            parse_mode="Markdown",
        )


async def on_back_capas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Volver al menú de capas."""
    query = update.callback_query
    await query.answer()

    project_id = query.data.split("_")[-1]
    meta = project_service.get_project(project_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado.")
        return

    layers = meta.get("layers", {})
    icons = {"ready": "✅", "pending": "⏳", "error": "❌", "empty": "⬜"}

    keyboard = []
    for layer in ("video", "audio", "subtitles", "music", "overlay"):
        st = layers.get(layer, "empty")
        icon = icons.get(st, "?")
        keyboard.append([InlineKeyboardButton(f"{icon} {layer.capitalize()}", callback_data=f"layer_{layer}_{project_id}")])

    await query.edit_message_text(
        f"🎬 *Capas de {meta['title']}*\n`{project_id}`\n\nSelecciona una capa:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


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
    application.add_handler(CommandHandler("ultimos", cmd_ultimos))
    application.add_handler(CommandHandler("estado", cmd_estado))
    application.add_handler(CommandHandler("descargar", cmd_descargar))
    application.add_handler(CommandHandler("capas", cmd_capas))
    application.add_handler(CommandHandler("guion", cmd_guion))

    # Capas management
    application.add_handler(CallbackQueryHandler(on_layer_selected, pattern="^layer_"))
    application.add_handler(CallbackQueryHandler(on_layer_download, pattern="^dl_"))
    application.add_handler(CallbackQueryHandler(on_edit_audio, pattern="^edit_audio_"))
    application.add_handler(CallbackQueryHandler(on_voice_menu, pattern="^voice_menu_"))
    application.add_handler(CallbackQueryHandler(on_regen_audio, pattern="^regen_audio_"))
    application.add_handler(CallbackQueryHandler(on_regen_video, pattern="^regen_video_"))
    application.add_handler(CallbackQueryHandler(on_regen_subs, pattern="^regen_subs_"))
    application.add_handler(CallbackQueryHandler(on_setvoice, pattern="^setvoice_"))
    application.add_handler(CallbackQueryHandler(on_back_capas, pattern="^back_capas_"))

    # Script editing conversation (debe ir ANTES de los handlers individuales)
    script_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_edit_script, pattern="^edit_script_")],
        states={
            WAITING_SCRIPT_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_script_text),
                CommandHandler("listo", cmd_cancelar),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )
    application.add_handler(script_conv)

    # Script regeneration (callback separado)
    application.add_handler(CallbackQueryHandler(on_regen_script, pattern="^regen_script_"))

    application.add_handler(conv)

    return application


async def start_polling(token: str):
    """Inicia el bot en background (no bloquea uvicorn)."""
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("✅ Telegram bot polling iniciado")
