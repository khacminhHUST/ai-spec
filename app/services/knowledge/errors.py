"""Lỗi nghiệp vụ kho tri thức: status = mã HTTP gợi ý (400 dữ liệu sai, 404 không có, 409 xung đột trạng thái)."""
from __future__ import annotations


class KnowledgeError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status
