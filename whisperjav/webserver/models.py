"""Request models for the web API."""

from typing import List, Optional

from pydantic import BaseModel, Field


class TranscribeOptions(BaseModel):
    mode: str = "balanced"
    model: Optional[str] = None
    language: str = "japanese"
    subs_language: str = "native"
    sensitivity: str = "balanced"
    # "source" is a literal sentinel meaning "next to the input file"
    # (whisperjav/main.py:286), not a path.
    output_dir: Optional[str] = None
    output_format: str = "srt"
    temp_dir: Optional[str] = None
    keep_temp: bool = False
    skip_existing: bool = False
    accept_cpu_mode: bool = False
    log_level: str = "INFO"
    check_only: bool = False


class TranslateOptions(BaseModel):
    provider: str = "ollama"
    target: str = "english"
    source: str = "japanese"
    tone: str = "standard"
    model: Optional[str] = None
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    include_original: Optional[bool] = None
    output_dir: Optional[str] = None
    scene_threshold: Optional[float] = None
    max_batch_size: Optional[int] = None
    ollama_url: Optional[str] = None
    ollama_num_ctx: Optional[int] = None
    movie_title: Optional[str] = None
    actress: Optional[str] = None
    movie_plot: Optional[str] = None
    debug: bool = False


class SubmitRequest(BaseModel):
    flow: str = Field(default="transcribe")
    inputs: List[str] = Field(default_factory=list)
    transcribe: TranscribeOptions = Field(default_factory=TranscribeOptions)
    translate: TranslateOptions = Field(default_factory=TranslateOptions)
