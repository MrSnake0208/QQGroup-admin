"""Persistent, content-addressed image assets for authenticated plugin pages."""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import threading
import tempfile
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image

from .keyword_rules import IMAGE_ID

MAX_BYTES = 8 * 1024 * 1024
MAX_BASE64 = 4 * ((MAX_BYTES + 2) // 3)
MAX_PIXELS = 16_000_000
STORE_BUDGET = 256 * 1024 * 1024
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
           "GIF": ("gif", "image/gif"), "WEBP": ("webp", "image/webp")}


class KeywordImages:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()

    def _path(self, image_id: str) -> Path:
        if not isinstance(image_id, str) or not IMAGE_ID.fullmatch(image_id):
            raise ValueError("图片标识无效")
        path = self.root / image_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("图片路径无效")
        return path

    def read(self, image_id: str) -> bytes:
        path = self._path(image_id)
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        except FileNotFoundError as exc:
            raise LookupError("图片不存在，请重新上传") from exc
        if not data or len(data) > MAX_BYTES:
            raise ValueError("图片大小无效")
        return data

    def preview(self, image_id: str) -> dict[str, str]:
        data = self.read(image_id)
        with Image.open(BytesIO(data)) as source:
            source.thumbnail((360, 240))
            out = BytesIO()
            source.convert("RGBA").save(out, "PNG")
        return {"id": image_id, "data_url": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")}

    def upload(self, encoded: str) -> dict[str, str]:
        if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BASE64:
            raise ValueError("图片不能为空或超过 8 MB")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("图片编码无效") from exc
        if not data or len(data) > MAX_BYTES:
            raise ValueError("图片不能为空或超过 8 MB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as img:
                    if img.format not in FORMATS:
                        raise ValueError("仅支持 PNG、JPEG、GIF、WebP 图片")
                    extension, _ = FORMATS[img.format]
                    if img.width * img.height > MAX_PIXELS:
                        raise ValueError("图片最多 1600 万像素")
                    img.verify()
                # Decode the first frame too; some formats only inspect headers in verify().
                with Image.open(BytesIO(data)) as img:
                    img.load()
        except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("图片损坏或尺寸过大") from exc
        image_id = hashlib.sha256(data).hexdigest() + "." + extension
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(image_id)
            if not path.exists():
                used = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
                if used + len(data) > STORE_BUDGET:
                    raise ValueError("关键词图片总量已达 256 MB，请先备份并清理不用的图片")
                with tempfile.NamedTemporaryFile(dir=self.root, prefix=".upload-", delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(data)
                        stream.close()
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
        return {"id": image_id}
