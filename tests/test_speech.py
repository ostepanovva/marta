import asyncio
import os
import time

import pytest

from marta.speech import TranscribeError, Transcriber


def fine(model, cache, path):
    return f"расшифровка {os.path.basename(path)}"


def crash(model, cache, path):
    os._exit(1)  # так выглядит падение программы распознавания


def slow(model, cache, path):
    time.sleep(30)
    return "слишком поздно"


def test_crash_does_not_take_marta_down():
    async def scenario():
        broken = Transcriber("small", "/tmp", worker=crash)
        with pytest.raises(TranscribeError):
            await broken.transcribe("a.ogg")
        assert not broken.loaded
        broken._worker = fine  # следующее голосовое — процесс распознавания запускается заново
        assert await broken.transcribe("b.ogg") == "расшифровка b.ogg"
        assert broken.loaded

        stuck = Transcriber("small", "/tmp", worker=slow)
        started = time.monotonic()
        with pytest.raises(TranscribeError):
            await stuck.transcribe("c.ogg", timeout=2)
        assert time.monotonic() - started < 10  # зависший процесс не держит Марту
        broken.close()
        stuck.close()

    asyncio.run(scenario())
