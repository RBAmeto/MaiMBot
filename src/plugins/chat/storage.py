from typing import Optional, Union

from ...common.database import db
from .message import MessageSending, MessageRecv
from .chat_stream import ChatStream
from loguru import logger


class MessageStorage:
    async def store_message(self, message: Union[MessageSending, MessageRecv],chat_stream:ChatStream, topic: Optional[str] = None) -> None:
        """存储消息到数据库"""
        try:
            # 提取群组ID信息，如果存在的话
            group_id = None
            if chat_stream.group_info:
                group_id = str(chat_stream.group_info.group_id)
                
            message_data = {
                    "message_id": message.message_info.message_id,
                    "time": message.message_info.time,
                    "chat_id": chat_stream.stream_id,
                    "chat_info": chat_stream.to_dict(),
                    "user_info": message.message_info.user_info.to_dict(),
                    "processed_plain_text": message.processed_plain_text,
                    "detailed_plain_text": message.detailed_plain_text,
                    "topic": topic,
                    "group_id": group_id,  # 显式添加group_id字段
                }
            db.messages.insert_one(message_data)
        except Exception:
            logger.exception("存储消息失败")

# 如果需要其他存储相关的函数，可以在这里添加
