"""Cancellable local subprocess I/O. No shell execution, output logging or zombies."""

import asyncio

from app.voice.base import VoiceError


async def run_process(*arguments: str, timeout: float, input_data: bytes | None = None) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise VoiceError("VOICE_BINARY_UNAVAILABLE", "The configured local voice binary could not start.") from exc
    communication = asyncio.create_task(process.communicate(input_data))
    try:
        output, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
        if process.returncode:
            raise VoiceError("VOICE_PROCESS_FAILED", "The local voice process failed. Check its binary, model and voice configuration.")
        return output
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 0.5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await communication
