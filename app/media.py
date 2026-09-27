"""Bounded ISO-BMFF helpers for uploaded MP4 files."""

import os
import struct
from pathlib import Path


class InvalidMP4(ValueError):
    """The file is not a structurally valid MP4 with a readable movie header."""


def _boxes(stream, start: int, end: int):
    position = start
    count = 0
    while position < end:
        count += 1
        if count > 100_000 or end - position < 8:
            raise InvalidMP4("Invalid MP4 box layout")
        stream.seek(position)
        header = stream.read(8)
        if len(header) != 8:
            raise InvalidMP4("Truncated MP4 box header")
        size32, box_type = struct.unpack(">I4s", header)
        header_size = 8
        if size32 == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                raise InvalidMP4("Truncated extended MP4 box header")
            box_size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size32 == 0:
            box_size = end - position
        else:
            box_size = size32
        if box_size < header_size or box_size > end - position:
            raise InvalidMP4("Invalid MP4 box size")
        yield box_type, position + header_size, position + box_size
        position += box_size


def mp4_duration_ms(path: str | Path) -> int:
    """Read `moov/mvhd` duration without loading media payloads into memory."""
    file_path = Path(path)
    file_size = os.path.getsize(file_path)
    if file_size < 20:
        raise InvalidMP4("MP4 file is too small")

    with file_path.open("rb") as stream:
        ftyp_found = False
        moov_range = None
        for box_type, payload_start, box_end in _boxes(stream, 0, file_size):
            if box_type == b"ftyp":
                ftyp_found = True
            elif box_type == b"moov":
                moov_range = (payload_start, box_end)
                break
        if not ftyp_found or moov_range is None:
            raise InvalidMP4("MP4 must contain ftyp and moov boxes")

        mvhd_range = None
        for box_type, payload_start, box_end in _boxes(stream, *moov_range):
            if box_type == b"mvhd":
                mvhd_range = (payload_start, box_end)
                break
        if mvhd_range is None:
            raise InvalidMP4("MP4 movie header is missing")

        payload_start, box_end = mvhd_range
        stream.seek(payload_start)
        full_box = stream.read(min(32, box_end - payload_start))
        if len(full_box) < 20:
            raise InvalidMP4("MP4 movie header is truncated")
        version = full_box[0]
        if version == 0:
            timescale = struct.unpack(">I", full_box[12:16])[0]
            duration = struct.unpack(">I", full_box[16:20])[0]
        elif version == 1:
            if len(full_box) < 32:
                raise InvalidMP4("MP4 version 1 movie header is truncated")
            timescale = struct.unpack(">I", full_box[20:24])[0]
            duration = struct.unpack(">Q", full_box[24:32])[0]
        else:
            raise InvalidMP4("Unsupported MP4 movie header version")
        if timescale == 0 or duration == 0:
            raise InvalidMP4("MP4 movie duration is invalid")
        return (duration * 1000 + timescale - 1) // timescale
