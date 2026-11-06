"""领域层 · 文献对话编排（待实现：移植 server/domain/chat.js，318 行）。"""
from __future__ import annotations


def chat_stream(services, paper, question, options=None):
    raise NotImplementedError("待实现：domain.chat.chat_stream")


def vision_stream(services, paper, question, images, label=None):
    raise NotImplementedError("待实现：domain.chat.vision_stream")


def transcribe(services, paper, image, page=None):
    raise NotImplementedError("待实现：domain.chat.transcribe")
