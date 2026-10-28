"""Распознавание голосовых прямо на сервере (faster-whisper). Аудио никуда не отправляется.

Распознавание работает в отдельном процессе: если программа распознавания упадет,
бот продолжит работать, а при следующем голосовом процесс запустится заново.
"""

import asyncio
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

log = logging.getLogger(__name__)

IDLE_UNLOAD_SECONDS = 15 * 60  # выгружаем модель из памяти, если голосовых давно не было
TRANSCRIBE_TIMEOUT = 180  # секунд на одно голосовое, включая загрузку модели


class TranscribeError(Exception):
    """Голосовое не удалось расшифровать; текст ошибки показывается владельцу."""


# --- то, что выполняется в отдельном процессе ---

_model = None


def _init_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _transcribe_in_worker(model_name: str, cache_dir: str, path: str) -> str:
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        log.info("Загружаю модель распознавания речи %s", model_name)
        started = time.monotonic()
        _model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=cache_dir, cpu_threads=2)
        log.info("Модель распознавания речи загружена за %.0f с", time.monotonic() - started)
    started = time.monotonic()
    segments, _ = _model.transcribe(path, language="ru", beam_size=1, vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    log.info("Голосовое расшифровано за %.0f с", time.monotonic() - started)
    return text


# --- то, чем пользуется бот ---


class Transcriber:
    def __init__(self, model_name: str, cache_dir: str, worker=_transcribe_in_worker):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._worker = worker
        self._pool: ProcessPoolExecutor | None = None
        self._warm = False  # модель уже загружена в процессе распознавания
        self._last_used = 0.0
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._pool is not None and self._warm

    async def prefetch(self) -> None:
        """Заранее скачивает модель, чтобы первое голосовое не ждало загрузки."""
        from faster_whisper.utils import download_model

        try:
            await asyncio.to_thread(download_model, self.model_name, cache_dir=self.cache_dir)
            log.info("Модель распознавания речи %s готова", self.model_name)
        except Exception:
            log.exception("Не удалось скачать модель распознавания речи %s", self.model_name)

    def _stop(self, kill: bool = False) -> None:
        pool, self._pool, self._warm = self._pool, None, False
        if pool is None:
            return
        if kill:
            for process in list(getattr(pool, "_processes", {}).values()):
                process.kill()
        pool.shutdown(wait=False, cancel_futures=True)

    async def transcribe(self, path: str, timeout: float = TRANSCRIBE_TIMEOUT) -> str:
        async with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker
                )
            loop = asyncio.get_running_loop()
            job = loop.run_in_executor(self._pool, self._worker, self.model_name, self.cache_dir, path)
            try:
                text = await asyncio.wait_for(job, timeout)
            except asyncio.TimeoutError:
                log.error("Голосовое: расшифровка не уложилась в %.0f с, перезапускаю распознавание", timeout)
                self._stop(kill=True)
                raise TranscribeError("Расшифровка затянулась") from None
            except BrokenProcessPool:
                log.error("Голосовое: программа распознавания упала, при следующем голосовом запущу ее заново")
                self._stop(kill=True)
                raise TranscribeError("Программа распознавания упала") from None
            finally:
                self._last_used = time.monotonic()
            self._warm = True
            return text

    async def unload_if_idle(self) -> None:
        async with self._lock:
            if self._pool is not None and time.monotonic() - self._last_used > IDLE_UNLOAD_SECONDS:
                self._stop()
                log.info("Модель распознавания речи выгружена из памяти")

    def close(self) -> None:
        self._stop(kill=True)
