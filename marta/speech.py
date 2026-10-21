"""Распознавание голосовых прямо на сервере (faster-whisper). Аудио никуда не отправляется."""

import asyncio
import logging
import time

log = logging.getLogger(__name__)

IDLE_UNLOAD_SECONDS = 15 * 60  # выгружаем модель из памяти, если голосовых давно не было


class Transcriber:
    def __init__(self, model_name: str, cache_dir: str):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._last_used = 0.0
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        from faster_whisper import WhisperModel

        log.info("Загружаю модель распознавания речи %s", self.model_name)
        return WhisperModel(
            self.model_name, device="cpu", compute_type="int8", download_root=self.cache_dir, cpu_threads=2
        )

    async def prefetch(self) -> None:
        """Заранее скачивает модель, чтобы первое голосовое не ждало загрузки."""
        from faster_whisper.utils import download_model

        try:
            await asyncio.to_thread(download_model, self.model_name, cache_dir=self.cache_dir)
            log.info("Модель распознавания речи %s готова", self.model_name)
        except Exception:
            log.exception("Не удалось скачать модель распознавания речи %s", self.model_name)

    def _run(self, path: str) -> str:
        if self._model is None:
            self._model = self._load()
        segments, _ = self._model.transcribe(path, language="ru", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, path: str) -> str:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._run, path)
            finally:
                self._last_used = time.monotonic()

    async def unload_if_idle(self) -> None:
        async with self._lock:
            if self._model is not None and time.monotonic() - self._last_used > IDLE_UNLOAD_SECONDS:
                self._model = None
                log.info("Модель распознавания речи выгружена из памяти")
