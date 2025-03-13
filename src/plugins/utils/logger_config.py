import sys
from loguru import logger
from enum import Enum

class LogModule(Enum):
    BASE = "base"
    MEMORY = "memory"
    EMOJI = "emoji"
    CHAT = "chat"

def setup_logger(log_type: LogModule = LogModule.BASE):
    """配置日志格式
    
    Args:
        log_type: 日志类型，可选值：BASE(基础日志)、MEMORY(记忆系统日志)、EMOJI(表情包系统日志)
    """
    # 移除默认的处理器
    logger.remove()
    
    # 基础日志格式
    base_format = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    chat_format = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    # 记忆系统日志格式
    memory_format = "<green>{time:HH:mm}</green> | <level>{level: <8}</level> | <light-magenta>海马体</light-magenta> | <level>{message}</level>"
    
    # 表情包系统日志格式
    emoji_format = "<green>{time:HH:mm}</green> | <level>{level: <8}</level> | <yellow>表情包</yellow> | <cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    # 根据日志类型选择日志格式和输出
    if log_type == LogModule.CHAT:
        logger.add(
            sys.stderr,
            format=chat_format,
            # level="INFO"
        )
    elif log_type == LogModule.MEMORY:
        # 同时输出到控制台和文件
        logger.add(
            sys.stderr,
            format=memory_format,
            # level="INFO"
        )
        logger.add(
            "logs/memory.log",
            format=memory_format,
            level="INFO",
            rotation="1 day",
            retention="7 days"
        )
    elif log_type == LogModule.EMOJI:
        logger.add(
            sys.stderr,
            format=emoji_format,
            # level="INFO"
        )
        logger.add(
            "logs/emoji.log",
            format=emoji_format,
            level="INFO",
            rotation="1 day",
            retention="7 days"
        )
    else:  # BASE
        logger.add(
            sys.stderr,
            format=base_format,
            level="INFO"
        )
    
    return logger
