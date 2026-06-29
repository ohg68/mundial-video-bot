from pydantic import BaseModel
from typing import Optional, Literal
from enum import Enum

class VideoSource(str, Enum):
    local = "local"
    pexels = "pexels"
    pixabay = "pixabay"
    coverr = "coverr"
    youtube = "youtube"
    mixed = "mixed"
    photos = "photos"
    mixed_photos = "mixed_photos"

class TTSProvider(str, Enum):
    edge = "edge"
    openai = "openai"
    elevenlabs = "elevenlabs"

class VoiceModel(str, Enum):
    alvaro = "es-ES-AlvaroNeural"
    elvira = "es-ES-ElviraNeural"
    duarte = "pt-PT-DuarteNeural"
    ines = "pt-PT-InesNeural"
    custom = "custom"

class LLMProvider(str, Enum):
    deepseek = "deepseek"
    claude = "claude"
    openai = "openai"

class ScriptTemplate(str, Enum):
    free = "free"
    preview = "preview"
    summary = "summary"
    top5 = "top5"
    tutorial = "tutorial"

class LayerStatus(str, Enum):
    empty = "empty"
    pending = "pending"
    ready = "ready"
    error = "error"

class VideoLayerConfig(BaseModel):
    source: VideoSource = VideoSource.mixed
    clip_duration: int = 4
    local_folder: Optional[str] = None
    # A/B split: segmenta el guion y baja 2 visuales (A/B) por escena para que
    # la imagen siga lo que narra el locutor. Off por defecto (sin cambio de comportamiento).
    ab_split: bool = False
    scene_count: int = 6

class AudioLayerConfig(BaseModel):
    voice: VoiceModel = VoiceModel.alvaro
    tts_provider: TTSProvider = TTSProvider.edge
    openai_voice: str = "onyx"
    elevenlabs_voice_id: Optional[str] = None
    speed: float = 1.0
    volume: float = 0.9
    custom_file: Optional[str] = None

class MusicLayerConfig(BaseModel):
    source: Literal["library", "local", "suno"] = "local"
    track_file: Optional[str] = None
    volume: float = 0.25
    fade_in: int = 2
    fade_out: int = 3

class SubtitleLayerConfig(BaseModel):
    font: str = "Arial"
    font_size: int = 72  # píxeles reales (resolución del video)
    color: str = "white"
    outline: bool = True
    position: Literal["top", "center", "bottom"] = "bottom"
    custom_srt: Optional[str] = None

class OverlayLayerConfig(BaseModel):
    logo_file: Optional[str] = None
    logo_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"
    logo_opacity: float = 0.8
    intro_file: Optional[str] = None
    outro_file: Optional[str] = None
    lower_third: Optional[str] = None

class ProjectConfig(BaseModel):
    title: str
    topic: str
    match: Optional[str] = None
    match_date: Optional[str] = None
    aspect: Literal["9:16", "16:9"] = "9:16"
    language: str = "es"
    script: Optional[str] = None
    llm_provider: LLMProvider = LLMProvider.deepseek
    script_template: ScriptTemplate = ScriptTemplate.free
    video: VideoLayerConfig = VideoLayerConfig()
    audio: AudioLayerConfig = AudioLayerConfig()
    music: MusicLayerConfig = MusicLayerConfig()
    subtitles: SubtitleLayerConfig = SubtitleLayerConfig()
    overlay: OverlayLayerConfig = OverlayLayerConfig()

class LayerUpdate(BaseModel):
    layer: Literal["video", "audio", "music", "subtitles", "overlay"]
    file_path: Optional[str] = None
    config: Optional[dict] = None
