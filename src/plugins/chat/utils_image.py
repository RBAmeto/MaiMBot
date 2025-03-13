import base64
import os
import time
import aiohttp
import hashlib
from typing import Optional, Union
from PIL import Image
import io

from loguru import logger
from nonebot import get_driver

from ...common.database import db
from ..chat.config import global_config
from ..models.utils_model import LLM_request

driver = get_driver()
config = driver.config


class ImageManager:
    _instance = None
    IMAGE_DIR = "data"  # 图像存储根目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._ensure_image_collection()
            self._ensure_description_collection()
            self._ensure_image_dir()
            self._initialized = True
            self._llm = LLM_request(model=global_config.vlm, temperature=0.4, max_tokens=300)

    def _ensure_image_dir(self):
        """确保图像存储目录存在"""
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

    def _ensure_image_collection(self):
        """确保images集合存在并创建索引"""
        if "images" not in db.list_collection_names():
            db.create_collection("images")

        # 删除旧索引
        db.images.drop_indexes()
        # 创建新的复合索引
        db.images.create_index([("hash", 1), ("type", 1)], unique=True)
        db.images.create_index([("url", 1)])
        db.images.create_index([("path", 1)])

    def _ensure_description_collection(self):
        """确保image_descriptions集合存在并创建索引"""
        if "image_descriptions" not in db.list_collection_names():
            db.create_collection("image_descriptions")

        # 删除旧索引
        db.image_descriptions.drop_indexes()
        # 创建新的复合索引
        db.image_descriptions.create_index([("hash", 1), ("type", 1)], unique=True)

    def _get_description_from_db(self, image_hash: str, description_type: str) -> Optional[str]:
        """从数据库获取图片描述

        Args:
            image_hash: 图片哈希值
            description_type: 描述类型 ('emoji' 或 'image')

        Returns:
            Optional[str]: 描述文本，如果不存在则返回None
        """
        result = db.image_descriptions.find_one({"hash": image_hash, "type": description_type})
        return result["description"] if result else None

    def _save_description_to_db(self, image_hash: str, description: str, description_type: str) -> None:
        """保存图片描述到数据库

        Args:
            image_hash: 图片哈希值
            description: 描述文本
            description_type: 描述类型 ('emoji' 或 'image')
        """
        try:
            db.image_descriptions.update_one(
                {"hash": image_hash, "type": description_type},
                {
                    "$set": {
                        "description": description,
                        "timestamp": int(time.time()),
                        "hash": image_hash,  # 确保hash字段存在
                        "type": description_type,  # 确保type字段存在
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.error(f"保存描述到数据库失败: {str(e)}")
  
        
    async def get_emoji_description(self, image_base64: str) -> str:
        """获取表情包描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()
            #区分emoji和image
            filetype = 'image'
            if 'pic' not in db.list_collection_names():
                    db.create_collection('pic')
                    db.pic.create_index([('hash', 1)], unique=True)
                    db.pic.create_index([('count')])
            exist_pic = db.pic.find_one({'hash': image_hash})
            if not exist_pic:
                pic_record={
                        'hash':image_hash,
                        'count':1
                    }
                db.pic.insert_one(pic_record)
            else:
                logger.info(f"{image_hash}出现{exist_pic['count']}次了")
                pic_cnt = exist_pic['count'] + 1
                db.pic.update_one({'hash': image_hash},{ "$set": { 'count': pic_cnt } })
                if pic_cnt >= 5:
                    filetype = 'emoji'
              # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, filetype)
            if cached_description:
                logger.info(f"缓存表情包描述: {cached_description}")
                return f"[表情包：{cached_description}]"

            # 调用AI获取描述
            if filetype == 'emoji':
                prompt = "这是一个表情包，使用中文简洁的描述一下表情包的内容和表情包所表达的情感"
            else:
                prompt = "请用中文描述这张图片的内容。如果有文字，请把文字都描述出来。并尝试猜测这个图片的含义。最多200个字。"
            description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)
            
            # 根据配置决定是否保存图片
            if global_config.EMOJI_SAVE:
                # 生成文件名和路径
                timestamp = int(time.time())
                filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
                file_dir = os.path.join(self.IMAGE_DIR, filetype)
                for file in os.listdir(file_dir):
                    if image_hash[:8] in file:
                        print(f"\033[1;33m[提示]\033[0m 发现重复表情包: {file}")
                        return f"[图片：{description}]"
                file_path = os.path.join(self.IMAGE_DIR, filetype,filename)
                try:
                    # 保存文件
                    with open(file_path, "wb") as f:
                        f.write(image_bytes)

                    # 保存到数据库
                    image_doc = {
                        'hash': image_hash,
                        'path': file_path,
                        'type': filetype,
                        'description': description,
                        'timestamp': timestamp
                    }
                    db.images.update_one(
                        {'hash': image_hash},
                        {'$set': image_doc},
                        upsert=True
                    )
                    logger.success(f"保存表情包: {file_path}")
                except Exception as e:
                    logger.error(f"保存表情包文件失败: {str(e)}")

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, filetype)
            return f"[图片：{description}]"
        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "[表情包]"


    async def get_image_description(self, image_base64: str) -> str:
        """获取普通图片描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, "image")
            if cached_description:
                logger.info(f"图片描述缓存中 {cached_description}")
                return f"[图片：{cached_description}]"

            # 调用AI获取描述
            prompt = (
                "请用中文描述这张图片的内容。如果有文字，请把文字都描述出来。并尝试猜测这个图片的含义。最多200个字。"
            )
            description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)

            cached_description = self._get_description_from_db(image_hash, "image")
            if cached_description:
                logger.warning(f"虽然生成了描述，但是找到缓存图片描述 {cached_description}")
                return f"[图片：{cached_description}]"

            logger.info(f"描述是{description}")

            if description is None:
                logger.warning("AI未能生成图片描述")
                return "[图片]"

            # 根据配置决定是否保存图片
            if global_config.EMOJI_SAVE:
                # 生成文件名和路径
                timestamp = int(time.time())
                filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
                if not os.path.exists(os.path.join(self.IMAGE_DIR, "image")):
                    os.makedirs(os.path.join(self.IMAGE_DIR, "image"))
                file_path = os.path.join(self.IMAGE_DIR, "image", filename)

                try:
                    # 保存文件
                    with open(file_path, "wb") as f:
                        f.write(image_bytes)

                    # 保存到数据库
                    image_doc = {
                        "hash": image_hash,
                        "path": file_path,
                        "type": "image",
                        "description": description,
                        "timestamp": timestamp,
                    }
                    db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                    logger.success(f"保存图片: {file_path}")
                except Exception as e:
                    logger.error(f"保存图片文件失败: {str(e)}")

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, "image")

            return f"[图片：{description}]"
        except Exception as e:
            logger.error(f"获取图片描述失败: {str(e)}")
            return "[图片]"


# 创建全局单例
image_manager = ImageManager()


def image_path_to_base64(image_path: str) -> str:
    """将图片路径转换为base64编码
    Args:
        image_path: 图片文件路径
    Returns:
        str: base64编码的图片数据
    """
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
            return base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        logger.error(f"读取图片失败: {image_path}, 错误: {str(e)}")
        return None
