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
from app.services import layer_service, project_service, render_service, voice_service

log = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
(WAITING_TITLE, WAITING_TOPIC, WAITING_SOURCE,
 WAITING_LAYER_ACTION, WAITING_AUDIO_VOICE, WAITING_AUDIO_SPEED,
 WAITING_VIDEO_SOURCE, WAITING_FILE_UPLOAD,
 WAITING_SCRIPT_EDIT) = range(9)

# ── Seguridad ─────────────────────────────────────────────────────────────────

def _allowed_chats() -> set:
    """Chat IDs autorizados desde TELEGRAM_ALLOWED_CHATS (coma-separados).
    Si la variable está vacía, el bot es abierto (comportamiento legacy)."""
    raw = os.getenv("TELEGRAM_ALLOWED_CHATS", "").strip()
    if not raw:
        return set()
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


async def _guard(update: Update) -> bool:
    """Devuelve True si el chat está autorizado; si no, responde y devuelve False."""
    allowed = _allowed_chats()
    if not allowed:
        return True  # bot abierto
    chat = update.effective_chat
    if chat and chat.id in allowed:
        return True
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"🔒 No estás autorizado para usar este bot.\n"
            f"Tu chat ID es `{chat.id if chat else '?'}` — pídele al admin que lo agregue.",
            parse_mode="Markdown",
        )
    return False


def _owns(project_id: str, chat_id: int):
    """Devuelve el proyecto si pertenece al chat_id; None si no existe o no es suyo.
    Cuando el bot es abierto (sin whitelist), no se aplica filtro de propiedad."""
    meta = project_service.get_project(project_id)
    if not meta:
        return None
    # Bot abierto: sin aislamiento por usuario
    if not _allowed_chats():
        return meta
    owner = meta.get("owner_id")
    # Proyectos antiguos sin owner (owner_id None) quedan accesibles
    if owner is None or owner == chat_id:
        return meta
    return None


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
        # 1. Crear proyecto (owner = chat_id de Telegram para aislamiento por usuario)
        config = _build_config(title, topic, source)
        meta = project_service.create_project(config, owner_id=chat_id)
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
            "✅ Audio generado\n\n📝 *Generando subtítulos...*",
            parse_mode="Markdown",
        )

        # 4. Subtítulos (sincronizados con el guión real)
        try:
            await layer_service.generate_subtitles(project_id, config)
        except Exception as sub_err:
            log.warning(f"Subtítulos fallaron, continuando sin ellos: {sub_err}")
        await bot.send_message(
            chat_id,
            "✅ Subtítulos listos\n\n🎬 *Descargando y ensamblando video...*",
            parse_mode="Markdown",
        )

        # 5. Capa de video
        await layer_service.assemble_video_layer(project_id, config)
        await bot.send_message(
            chat_id,
            "✅ Video ensamblado\n\n⚡ *Renderizando (1080p)...*",
            parse_mode="Markdown",
        )

        # 6. Render final
        output = await render_service.render_final(project_id, quality="full")
        size_mb = output.stat().st_size / 1024 / 1024

        await bot.send_message(
            chat_id,
            f"✅ Render listo ({size_mb:.1f}MB)\n\n📤 *Enviando video...*",
            parse_mode="Markdown",
        )

        # 7. Entregar
        caption = f"🎬 *{title}*\n_{topic}_\n\n`ID: {project_id}`"
        await _send_video(bot, chat_id, output, caption=caption)

        # 8. Botones de acción (sin escribir el ID a mano)
        await bot.send_message(
            chat_id,
            "✅ ¡Video listo! ¿Qué quieres hacer ahora?",
            reply_markup=_actions_kb(project_id),
        )

    except Exception as e:
        log.error(f"Pipeline error [chat={chat_id}]: {e}", exc_info=True)
        pid_info = f" (proyecto `{project_id}`)" if project_id else ""
        await bot.send_message(
            chat_id,
            f"❌ Error en el pipeline{pid_info}:\n```\n{str(e)[:400]}\n```",
            parse_mode="Markdown",
        )


# ── Voz ───────────────────────────────────────────────────────────────────────

async def _download_voice(bot, voice) -> Path:
    """Descarga una nota de voz de Telegram a un archivo temporal .ogg."""
    tmp_dir = Path("projects") / "_voice"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / f"{voice.file_id}.ogg"
    tg_file = await bot.get_file(voice.file_id)
    await tg_file.download_to_drive(str(dest))
    return dest


async def _transcribe_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Transcribe la nota de voz del update. Devuelve texto o '' si falla."""
    voice = update.message.voice
    audio_path = await _download_voice(context.application.bot, voice)
    try:
        return await voice_service.transcribe(audio_path)
    finally:
        audio_path.unlink(missing_ok=True)


async def on_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja notas de voz sueltas (fuera de conversación):
    transcribe → interpreta intención → actúa."""
    if not await _guard(update):
        return

    chat_id = update.effective_chat.id
    status = await update.message.reply_text("🎤 Transcribiendo tu mensaje...")

    try:
        text = await _transcribe_update(update, context)
    except Exception as e:
        await status.edit_text(
            f"❌ No pude transcribir el audio:\n`{str(e)[:150]}`",
            parse_mode="Markdown",
        )
        return

    if not text:
        await status.edit_text("🤔 No entendí nada en el audio. Intenta de nuevo.")
        return

    await status.edit_text(
        f"📝 Entendí:\n_{text}_\n\n🧠 Procesando...",
        parse_mode="Markdown",
    )

    intent = await voice_service.extract_intent(text)
    action = intent.get("action", "unknown")

    if action == "create":
        title = intent.get("title") or text[:60]
        topic = intent.get("topic") or text
        source = intent.get("source", "pexels")
        if source not in ("photos", "pexels", "mixed_photos"):
            source = "pexels"

        src_label = {"photos": "📷 Fotos", "pexels": "🎬 Video", "mixed_photos": "🔀 Mix"}[source]
        await status.edit_text(
            f"🚀 *Creando video por voz:*\n"
            f"Título: _{title}_\n"
            f"Tema: _{topic}_\n"
            f"Fuente: {src_label}\n\n"
            f"Iniciando pipeline...",
            parse_mode="Markdown",
        )
        asyncio.create_task(_run_pipeline(context.application.bot, chat_id, title, topic, source))

    elif action == "list":
        await status.delete()
        await cmd_ultimos(update, context)

    else:
        await status.edit_text(
            "🤔 No entendí qué querías hacer.\n\n"
            "Prueba diciendo algo como:\n"
            "_\"Crea un video sobre la final del Mundial con fotos\"_",
            parse_mode="Markdown",
        )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    if not await _guard(update):
        return
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
        "🎤 *Comando por voz:* envía una nota de voz como\n"
        "_\"Crea un video sobre la final del Mundial con fotos\"_\n"
        "y el bot lo genera automáticamente.\n\n"
        f"Tu chat ID: `{chat_id}`",
        parse_mode="Markdown",
    )


async def cmd_nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "🎬 *Nuevo video*\n\n¿Cuál es el *título* del video?\n"
        "_Puedes escribirlo o enviarlo por nota de voz 🎤_",
        parse_mode="Markdown",
    )
    return WAITING_TITLE


async def _input_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Obtiene el texto del mensaje: si es voz, lo transcribe; si es texto, lo devuelve."""
    if update.message.voice:
        thinking = await update.message.reply_text("🎤 Transcribiendo...")
        try:
            text = await _transcribe_update(update, context)
        finally:
            await thinking.delete()
        return (text or "").strip()
    return (update.message.text or "").strip()


async def on_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = await _input_text(update, context)
    if not title:
        await update.message.reply_text("🤔 No entendí el título. Inténtalo de nuevo.")
        return WAITING_TITLE
    context.user_data["title"] = title
    await update.message.reply_text(
        f"✅ Título: *{context.user_data['title']}*\n\n"
        "¿Cuál es el *tema o descripción* del video?\n"
        "_Ejemplo: Resumen del partido Argentina vs Francia, final del Mundial 2026_\n"
        "_También por voz 🎤_",
        parse_mode="Markdown",
    )
    return WAITING_TOPIC


async def on_tema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = await _input_text(update, context)
    if not topic:
        await update.message.reply_text("🤔 No entendí el tema. Inténtalo de nuevo.")
        return WAITING_TOPIC
    context.user_data["topic"] = topic

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
    """Mostrar últimos 10 proyectos del usuario con IDs copiables."""
    if not await _guard(update):
        return

    chat_id = update.effective_chat.id
    # Con whitelist activa, solo los proyectos del usuario; si es abierto, todos.
    owner_filter = chat_id if _allowed_chats() else None
    projects = project_service.list_projects(owner_id=owner_filter)

    if not projects:
        await update.message.reply_text(
            "No hay proyectos aún. Usa /nuevo para crear uno."
        )
        return

    # Cada proyecto como botón → abre su menú de acciones (sin escribir IDs)
    rows = []
    for p in projects[:10]:
        layers = p.get("layers", {})
        video = layers.get("video", "empty")
        ready = "✅" if video == "ready" else "⏳"
        label = f"{ready} {p['title'][:38]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"pmenu_{p['id']}")])

    await update.message.reply_text(
        "*Tus proyectos* — toca uno para gestionarlo:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )


# /listar es alias de /ultimos (mismo comportamiento)
cmd_listar = cmd_ultimos


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/estado ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    meta = _owns(project_id, update.effective_chat.id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{project_id}` no encontrado o no es tuyo.", parse_mode="Markdown")
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
    if not await _guard(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/descargar ID_proyecto`", parse_mode="Markdown"
        )
        return

    project_id = args[0]
    meta = _owns(project_id, update.effective_chat.id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{project_id}` no encontrado o no es tuyo.", parse_mode="Markdown")
        return

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

    title = meta.get("title", project_id)
    topic = meta.get("topic", "")

    msg = await update.message.reply_text("📤 Enviando video, espera...")
    caption = f"🎬 *{title}*\n_{topic}_\n\n`ID: {project_id}`"
    await _send_video(context.application.bot, update.effective_chat.id, output, caption)
    await msg.delete()


# ── Vistas reutilizables (comando o botón) ─────────────────────────────────────

async def _send_project_picker(update: Update, prompt: str, action_prefix: str):
    """Muestra los proyectos del usuario como botones para elegir sin escribir ID.
    action_prefix: 'pg' (guión) o 'pc' (capas)."""
    chat_id = update.effective_chat.id
    owner_filter = chat_id if _allowed_chats() else None
    projects = project_service.list_projects(owner_id=owner_filter)
    if not projects:
        await update.message.reply_text("No tienes proyectos aún. Usa /nuevo para crear uno.")
        return
    rows = []
    for p in projects[:10]:
        label = p["title"][:35] or p["id"]
        rows.append([InlineKeyboardButton(f"🎬 {label}", callback_data=f"{action_prefix}_{p['id']}")])
    await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(rows))


def _actions_kb(project_id: str) -> InlineKeyboardMarkup:
    """Botones de acción principal de un proyecto (sin escribir el ID a mano)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Guión", callback_data=f"pg_{project_id}"),
            InlineKeyboardButton("🎬 Capas", callback_data=f"pc_{project_id}"),
        ],
        [InlineKeyboardButton("📥 Descargar video", callback_data=f"pd_{project_id}")],
    ])


def _guion_view(meta: dict):
    """Construye (texto, keyboard) para mostrar el guión de un proyecto."""
    project_id = meta["id"]
    config = meta.get("config", {})
    script = config.get("script") if isinstance(config, dict) else None
    if not script:
        script = "(sin guión aún — genera uno con 🤖)"
    preview = script[:800]
    truncated = "..." if len(script) > 800 else ""
    text = f"📝 *Guión: {meta['title']}*\n\n```\n{preview}{truncated}\n```"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Editar guión", callback_data=f"edit_script_{project_id}")],
        [InlineKeyboardButton("🤖 Regenerar con IA", callback_data=f"regen_script_{project_id}")],
        [InlineKeyboardButton("🎬 Capas", callback_data=f"pc_{project_id}")],
    ])
    return text, kb


def _capas_view(meta: dict):
    """Construye (texto, keyboard) para el menú de capas de un proyecto."""
    project_id = meta["id"]
    layers = meta.get("layers", {})
    icons = {"ready": "✅", "pending": "⏳", "error": "❌", "empty": "⬜"}
    rows = []
    for layer in ("video", "audio", "subtitles", "music", "overlay"):
        st = layers.get(layer, "empty")
        icon = icons.get(st, "?")
        rows.append([InlineKeyboardButton(f"{icon} {layer.capitalize()}", callback_data=f"layer_{layer}_{project_id}")])
    rows.append([InlineKeyboardButton("📝 Guión", callback_data=f"pg_{project_id}")])
    text = f"🎬 *Capas de {meta['title']}*\n\nSelecciona una capa para ver opciones:"
    return text, InlineKeyboardMarkup(rows)


async def on_proj_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones de acción de proyecto: pg_ (guión), pc_ (capas), pd_ (descargar)."""
    query = update.callback_query
    await query.answer()

    kind, project_id = query.data.split("_", 1)
    chat_id = query.message.chat_id
    meta = _owns(project_id, chat_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado o no es tuyo.")
        return

    if kind == "pmenu":
        await query.edit_message_text(
            f"🎬 *{meta['title']}*\n\n¿Qué quieres hacer?",
            reply_markup=_actions_kb(project_id),
            parse_mode="Markdown",
        )
    elif kind == "pg":
        text, kb = _guion_view(meta)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif kind == "pc":
        text, kb = _capas_view(meta)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    elif kind == "pd":
        output = Path("projects") / project_id / "output" / "final.mp4"
        if not output.exists():
            output = Path("projects") / project_id / "output" / "preview.mp4"
        if not output.exists():
            await query.answer("Aún no hay render para este proyecto", show_alert=True)
            return
        await context.application.bot.send_message(chat_id, "📤 Enviando video...")
        caption = f"🎬 *{meta.get('title', project_id)}*\n_{meta.get('topic','')}_"
        await _send_video(context.application.bot, chat_id, output, caption)


async def cmd_guion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ver y editar el guión de un proyecto."""
    if not await _guard(update):
        return
    args = context.args
    if not args:
        # Sin ID: ofrecer la lista de proyectos como botones
        await _send_project_picker(update, "📝 Elige un proyecto para ver/editar su guión:", "pg")
        return

    meta = _owns(args[0], update.effective_chat.id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{args[0]}` no encontrado o no es tuyo.", parse_mode="Markdown")
        return

    text, kb = _guion_view(meta)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


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

    # Guardar nuevo guión en SQLite
    meta = project_service.get_project(project_id)
    if not meta:
        await update.message.reply_text("❌ Proyecto no encontrado.")
        return ConversationHandler.END

    project_service.update_project_config(project_id, {"script": script})

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
    if not await _guard(update):
        return
    args = context.args
    if not args:
        # Sin ID: ofrecer la lista de proyectos como botones
        await _send_project_picker(update, "🎬 Elige un proyecto para editar sus capas:", "pc")
        return

    meta = _owns(args[0], update.effective_chat.id)
    if not meta:
        await update.message.reply_text(f"❌ Proyecto `{args[0]}` no encontrado o no es tuyo.", parse_mode="Markdown")
        return

    text, kb = _capas_view(meta)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


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
        sub_cfg = meta.get("config", {}).get("subtitles", {})
        size = sub_cfg.get("font_size", 72)
        pos = {"top": "arriba", "center": "centro", "bottom": "abajo"}.get(sub_cfg.get("position", "bottom"), "abajo")
        desc += f"\nTamaño: {size}px · Posición: {pos}"
        keyboard.append([InlineKeyboardButton("📐 Ajustar tamaño y posición", callback_data=f"substyle_{project_id}")])
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


async def on_substyle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submenú: ajustar tamaño y posición de subtítulos."""
    query = update.callback_query
    await query.answer()
    project_id = query.data.split("_")[-1]

    meta = _owns(project_id, query.message.chat_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado o no es tuyo.")
        return

    sub_cfg = meta.get("config", {}).get("subtitles", {})
    size = sub_cfg.get("font_size", 72)
    pos = sub_cfg.get("position", "bottom")
    pos_label = {"top": "arriba", "center": "centro", "bottom": "abajo"}.get(pos, "abajo")

    kb = [
        [
            InlineKeyboardButton("🔡 Pequeño", callback_data=f"subsz_56_{project_id}"),
            InlineKeyboardButton("🔠 Mediano", callback_data=f"subsz_72_{project_id}"),
            InlineKeyboardButton("🔠 Grande", callback_data=f"subsz_96_{project_id}"),
        ],
        [
            InlineKeyboardButton("⬆️ Arriba", callback_data=f"subps_top_{project_id}"),
            InlineKeyboardButton("⏺ Centro", callback_data=f"subps_center_{project_id}"),
            InlineKeyboardButton("⬇️ Abajo", callback_data=f"subps_bottom_{project_id}"),
        ],
        [InlineKeyboardButton("🔄 Aplicar y re-renderizar", callback_data=f"subrender_{project_id}")],
        [InlineKeyboardButton("◀️ Volver", callback_data=f"pc_{project_id}")],
    ]
    await query.edit_message_text(
        f"📐 *Estilo de subtítulos*\n\n"
        f"Tamaño actual: *{size}px*\nPosición: *{pos_label}*\n\n"
        f"Elige tamaño y posición, luego *Aplicar y re-renderizar*.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )


async def on_sub_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Setear tamaño (subsz_) o posición (subps_) de subtítulos."""
    query = update.callback_query
    parts = query.data.split("_")
    kind = parts[0]            # 'subsz' o 'subps'
    value = parts[1]
    project_id = "_".join(parts[2:])

    meta = _owns(project_id, query.message.chat_id)
    if not meta:
        await query.answer("Proyecto no encontrado", show_alert=True)
        return

    sub_cfg = dict(meta.get("config", {}).get("subtitles", {}))
    if kind == "subsz":
        sub_cfg["font_size"] = int(value)
        await query.answer(f"Tamaño: {value}px")
    else:
        sub_cfg["position"] = value
        await query.answer(f"Posición: {value}")

    project_service.update_project_config(project_id, {"subtitles": sub_cfg})

    # Refrescar el submenú para reflejar el cambio
    query.data = f"substyle_{project_id}"
    await on_substyle(update, context)


async def on_subrender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-renderizar el video con el nuevo estilo de subtítulos y entregarlo."""
    query = update.callback_query
    await query.answer()
    project_id = query.data.split("_")[-1]
    chat_id = query.message.chat_id

    meta = _owns(project_id, chat_id)
    if not meta:
        await query.edit_message_text("❌ Proyecto no encontrado o no es tuyo.")
        return

    await query.edit_message_text("⚡ Re-renderizando con el nuevo estilo de subtítulos...")
    asyncio.create_task(_rerender_and_send(context.application.bot, chat_id, project_id, meta))


async def _rerender_and_send(bot, chat_id: int, project_id: str, meta: dict):
    try:
        output = await render_service.render_final(project_id, quality="full")
        caption = f"🎬 *{meta.get('title', project_id)}* (subtítulos actualizados)"
        await _send_video(bot, chat_id, output, caption)
    except Exception as e:
        log.error(f"Re-render error: {e}", exc_info=True)
        await bot.send_message(chat_id, f"❌ Error al re-renderizar:\n`{str(e)[:200]}`", parse_mode="Markdown")


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
            WAITING_TITLE: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, on_titulo)],
            WAITING_TOPIC: [MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, on_tema)],
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

    # Acciones de proyecto por botón (menú, guión, capas, descargar)
    application.add_handler(CallbackQueryHandler(on_proj_action, pattern="^(pmenu|pg|pc|pd)_"))

    # Capas management
    application.add_handler(CallbackQueryHandler(on_layer_selected, pattern="^layer_"))
    application.add_handler(CallbackQueryHandler(on_layer_download, pattern="^dl_"))
    application.add_handler(CallbackQueryHandler(on_edit_audio, pattern="^edit_audio_"))
    application.add_handler(CallbackQueryHandler(on_voice_menu, pattern="^voice_menu_"))
    application.add_handler(CallbackQueryHandler(on_regen_audio, pattern="^regen_audio_"))
    application.add_handler(CallbackQueryHandler(on_regen_video, pattern="^regen_video_"))
    application.add_handler(CallbackQueryHandler(on_regen_subs, pattern="^regen_subs_"))
    application.add_handler(CallbackQueryHandler(on_substyle, pattern="^substyle_"))
    application.add_handler(CallbackQueryHandler(on_sub_set, pattern="^(subsz|subps)_"))
    application.add_handler(CallbackQueryHandler(on_subrender, pattern="^subrender_"))
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

    # Notas de voz sueltas (fuera de conversación): comando por voz.
    # Va al final para que la conversación /nuevo capture la voz primero.
    application.add_handler(MessageHandler(filters.VOICE, on_voice_message))

    return application


async def start_polling(token: str):
    """Inicia el bot en background (no bloquea uvicorn)."""
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("✅ Telegram bot polling iniciado")
