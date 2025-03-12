# -*- coding: utf-8 -*-
import datetime
import math
import random
import time
import os

import jieba
import networkx as nx

from loguru import logger
from nonebot import get_driver
from ...common.database import Database  # 使用正确的导入语法
from ..chat.config import global_config
from ..chat.utils import (
    calculate_information_content,
    cosine_similarity,
    get_cloest_chat_from_db,
    text_to_vector,
)
from ..models.utils_model import LLM_request

class Memory_graph:
    def __init__(self):
        self.G = nx.Graph()  # 使用 networkx 的图结构
        self.db = Database.get_instance()

    def connect_dot(self, concept1, concept2):
        # 避免自连接
        if concept1 == concept2:
            return
        
        current_time = datetime.datetime.now().timestamp()
        
        # 如果边已存在,增加 strength
        if self.G.has_edge(concept1, concept2):
            self.G[concept1][concept2]['strength'] = self.G[concept1][concept2].get('strength', 1) + 1
            # 更新最后修改时间
            self.G[concept1][concept2]['last_modified'] = current_time
        else:
            # 如果是新边,初始化 strength 为 1
            self.G.add_edge(concept1, concept2, 
                          strength=1,
                          created_time=current_time,  # 添加创建时间
                          last_modified=current_time) # 添加最后修改时间

    def add_dot(self, concept, memory, group_id=None):
        current_time = datetime.datetime.now().timestamp()
        
        # 如果memory不是字典格式，将其转换为字典
        if not isinstance(memory, dict):
            memory = {
                'content': memory,
                'group_id': group_id
            }
        # 如果memory是字典但没有group_id，添加group_id
        elif 'group_id' not in memory and group_id is not None:
            memory['group_id'] = group_id
        
        if concept in self.G:
            if 'memory_items' in self.G.nodes[concept]:
                if not isinstance(self.G.nodes[concept]['memory_items'], list):
                    self.G.nodes[concept]['memory_items'] = [self.G.nodes[concept]['memory_items']]
                self.G.nodes[concept]['memory_items'].append(memory)
                # 更新最后修改时间
                self.G.nodes[concept]['last_modified'] = current_time
            else:
                self.G.nodes[concept]['memory_items'] = [memory]
                # 如果节点存在但没有memory_items,说明是第一次添加memory,设置created_time
                if 'created_time' not in self.G.nodes[concept]:
                    self.G.nodes[concept]['created_time'] = current_time
                self.G.nodes[concept]['last_modified'] = current_time
        else:
            # 如果是新节点,创建新的记忆列表
            self.G.add_node(concept, 
                          memory_items=[memory],
                          created_time=current_time,  # 添加创建时间
                          last_modified=current_time) # 添加最后修改时间

    def get_dot(self, concept):
        # 检查节点是否存在于图中
        if concept in self.G:
            # 从图中获取节点数据
            node_data = self.G.nodes[concept]
            return concept, node_data
        return None

    def get_related_item(self, topic, depth=1):
        if topic not in self.G:
            return [], []

        first_layer_items = []
        second_layer_items = []

        # 获取相邻节点
        neighbors = list(self.G.neighbors(topic))

        # 获取当前节点的记忆项
        node_data = self.get_dot(topic)
        if node_data:
            concept, data = node_data
            if 'memory_items' in data:
                memory_items = data['memory_items']
                if isinstance(memory_items, list):
                    first_layer_items.extend(memory_items)
                else:
                    first_layer_items.append(memory_items)

        # 只在depth=2时获取第二层记忆
        if depth >= 2:
            # 获取相邻节点的记忆项
            for neighbor in neighbors:
                node_data = self.get_dot(neighbor)
                if node_data:
                    concept, data = node_data
                    if 'memory_items' in data:
                        memory_items = data['memory_items']
                        if isinstance(memory_items, list):
                            second_layer_items.extend(memory_items)
                        else:
                            second_layer_items.append(memory_items)

        return first_layer_items, second_layer_items

    @property
    def dots(self):
        # 返回所有节点对应的 Memory_dot 对象
        return [self.get_dot(node) for node in self.G.nodes()]

    def forget_topic(self, topic):
        """随机删除指定话题中的一条记忆，如果话题没有记忆则移除该话题节点"""
        if topic not in self.G:
            return None

        # 获取话题节点数据
        node_data = self.G.nodes[topic]

        # 如果节点存在memory_items
        if 'memory_items' in node_data:
            memory_items = node_data['memory_items']

            # 确保memory_items是列表
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 如果有记忆项可以删除
            if memory_items:
                # 随机选择一个记忆项删除
                removed_item = random.choice(memory_items)
                memory_items.remove(removed_item)

                # 更新节点的记忆项
                if memory_items:
                    self.G.nodes[topic]['memory_items'] = memory_items
                else:
                    # 如果没有记忆项了，删除整个节点
                    self.G.remove_node(topic)

                return removed_item

        return None


# 海马体
class Hippocampus:
    def __init__(self, memory_graph: Memory_graph):
        self.memory_graph = memory_graph
        self.llm_topic_judge = LLM_request(model=global_config.llm_topic_judge, temperature=0.5)
        self.llm_summary_by_topic = LLM_request(model=global_config.llm_summary_by_topic, temperature=0.5)

    def get_all_node_names(self) -> list:
        """获取记忆图中所有节点的名字列表
        
        Returns:
            list: 包含所有节点名字的列表
        """
        return list(self.memory_graph.G.nodes())

    def calculate_node_hash(self, concept, memory_items):
        """计算节点的特征值"""
        if not isinstance(memory_items, list):
            memory_items = [memory_items] if memory_items else []
        sorted_items = sorted(memory_items)
        content = f"{concept}:{'|'.join(sorted_items)}"
        return hash(content)

    def calculate_edge_hash(self, source, target):
        """计算边的特征值"""
        nodes = sorted([source, target])
        return hash(f"{nodes[0]}:{nodes[1]}")

    def get_memory_sample(self, chat_size=20, time_frequency: dict = {'near': 2, 'mid': 4, 'far': 3}):
        """获取记忆样本
        
        Returns:
            list: 消息记录列表，每个元素是一个消息记录字典列表
        """
        current_timestamp = datetime.datetime.now().timestamp()
        chat_samples = []

        # 短期：1h   中期：4h   长期：24h
        for _ in range(time_frequency.get('near')):
            random_time = current_timestamp - random.randint(1, 3600)
            messages = get_cloest_chat_from_db(db=self.memory_graph.db, length=chat_size, timestamp=random_time)
            if messages:
                chat_samples.append(messages)

        for _ in range(time_frequency.get('mid')):
            random_time = current_timestamp - random.randint(3600, 3600 * 4)
            messages = get_cloest_chat_from_db(db=self.memory_graph.db, length=chat_size, timestamp=random_time)
            if messages:
                chat_samples.append(messages)

        for _ in range(time_frequency.get('far')):
            random_time = current_timestamp - random.randint(3600 * 4, 3600 * 24)
            messages = get_cloest_chat_from_db(db=self.memory_graph.db, length=chat_size, timestamp=random_time)
            if messages:
                chat_samples.append(messages)

        return chat_samples

    async def memory_compress(self, messages: list, compress_rate=0.1):
        """压缩消息记录为记忆
        
        Returns:
            tuple: (压缩记忆集合, 相似主题字典)
        """
        if not messages:
            return set(), {}

        # 提取群聊ID信息
        group_id = None
        for msg in messages:
            if 'group_id' in msg and msg['group_id']:
                group_id = msg['group_id']
                break
                
        # 获取群组类型
        group_type = self.get_group_type(group_id) if group_id else None

        # 合并消息文本，同时保留时间信息
        input_text = ""
        time_info = ""
        # 计算最早和最晚时间
        earliest_time = min(msg['time'] for msg in messages)
        latest_time = max(msg['time'] for msg in messages)

        earliest_dt = datetime.datetime.fromtimestamp(earliest_time)
        latest_dt = datetime.datetime.fromtimestamp(latest_time)

        # 如果是同一年
        if earliest_dt.year == latest_dt.year:
            earliest_str = earliest_dt.strftime("%m-%d %H:%M:%S")
            latest_str = latest_dt.strftime("%m-%d %H:%M:%S")
            time_info += f"是在{earliest_dt.year}年，{earliest_str} 到 {latest_str} 的对话:\n"
        else:
            earliest_str = earliest_dt.strftime("%Y-%m-%d %H:%M:%S")
            latest_str = latest_dt.strftime("%Y-%m-%d %H:%M:%S")
            time_info += f"是从 {earliest_str} 到 {latest_str} 的对话:\n"

        for msg in messages:
            input_text += f"{msg['detailed_plain_text']}\n"

        logger.debug(input_text)

        topic_num = self.calculate_topic_num(input_text, compress_rate)
        topics_response = await self.llm_topic_judge.generate_response(self.find_topic_llm(input_text, topic_num))

        # 过滤topics
        filter_keywords = global_config.memory_ban_words
        topics = [topic.strip() for topic in
                  topics_response[0].replace("，", ",").replace("、", ",").replace(" ", ",").split(",") if topic.strip()]
        filtered_topics = [topic for topic in topics if not any(keyword in topic for keyword in filter_keywords)]

        logger.info(f"过滤后话题: {filtered_topics}")

        # 创建所有话题的请求任务
        tasks = []
        for topic in filtered_topics:
            topic_what_prompt = self.topic_what(input_text, topic, time_info)
            task = self.llm_summary_by_topic.generate_response_async(topic_what_prompt)
            tasks.append((topic.strip(), task))

        # 等待所有任务完成
        compressed_memory = set()
        similar_topics_dict = {}  # 存储每个话题的相似主题列表
        
        for topic, task in tasks:
            response = await task
            if response:
                # 为每个主题创建特定群聊/群组的主题节点名称
                topic_node_name = topic
                
                # 如果是私有群组的群聊，使用群组前缀
                if group_type:
                    topic_node_name = f"{topic}_GT{group_type}"
                # 否则使用群聊ID前缀（如果有）
                elif group_id:
                    topic_node_name = f"{topic}_g{group_id}"
                
                # 记录主题内容
                memory_content = response[0]
                compressed_memory.add((topic_node_name, memory_content))
                
                # 为每个话题查找相似的已存在主题（不考虑群聊/群组前缀）
                existing_topics = list(self.memory_graph.G.nodes())
                similar_topics = []
                
                for existing_topic in existing_topics:
                    # 提取基础主题名和群组/群聊信息
                    base_existing_topic = existing_topic
                    existing_group_type = None
                    existing_group_id = None
                    
                    # 检查是否有群组前缀
                    if "_GT" in existing_topic:
                        parts = existing_topic.split("_GT")
                        base_existing_topic = parts[0]
                        if len(parts) > 1:
                            existing_group_type = parts[1]
                    # 检查是否有群聊前缀
                    elif "_g" in existing_topic:
                        parts = existing_topic.split("_g")
                        base_existing_topic = parts[0]
                        if len(parts) > 1:
                            existing_group_id = parts[1]
                    
                    # 计算基础主题的相似度
                    topic_words = set(jieba.cut(topic))
                    existing_words = set(jieba.cut(base_existing_topic))
                    
                    all_words = topic_words | existing_words
                    v1 = [1 if word in topic_words else 0 for word in all_words]
                    v2 = [1 if word in existing_words else 0 for word in all_words]
                    
                    similarity = cosine_similarity(v1, v2)
                    
                    # 如果相似度高且不是完全相同的主题
                    if similarity >= 0.6 and existing_topic != topic_node_name:
                        # 如果当前主题属于群组，只连接该群组内的主题或公共主题
                        if group_type:
                            # 只连接同群组主题或没有群组/群聊标识的通用主题
                            if (existing_group_type == group_type) or (not existing_group_type and not existing_group_id):
                                similar_topics.append((existing_topic, similarity))
                        # 如果当前主题不属于群组但有群聊ID
                        elif group_id:
                            # 只连接同群聊主题或没有群组/群聊标识的通用主题
                            if (existing_group_id == group_id) or (not existing_group_type and not existing_group_id):
                                similar_topics.append((existing_topic, similarity))
                        # 如果当前主题既不属于群组也没有群聊ID（通用主题）
                        else:
                            # 只连接没有群组标识的主题
                            if not existing_group_type:
                                similar_topics.append((existing_topic, similarity))
                
                similar_topics.sort(key=lambda x: x[1], reverse=True)
                similar_topics = similar_topics[:5]
                similar_topics_dict[topic_node_name] = similar_topics

        return compressed_memory, similar_topics_dict

    def calculate_topic_num(self, text, compress_rate):
        """计算文本的话题数量"""
        information_content = calculate_information_content(text)
        topic_by_length = text.count('\n') * compress_rate
        topic_by_information_content = max(1, min(5, int((information_content - 3) * 2)))
        topic_num = int((topic_by_length + topic_by_information_content) / 2)
        logger.debug(
            f"topic_by_length: {topic_by_length}, topic_by_information_content: {topic_by_information_content}, "
            f"topic_num: {topic_num}")
        return topic_num

    async def operation_build_memory(self, chat_size=20):
        time_frequency = {'near': 1, 'mid': 4, 'far': 4}
        memory_samples = self.get_memory_sample(chat_size, time_frequency)
        
        for i, messages in enumerate(memory_samples, 1):
            all_topics = []
            # 加载进度可视化
            progress = (i / len(memory_samples)) * 100
            bar_length = 30
            filled_length = int(bar_length * i // len(memory_samples))
            bar = '█' * filled_length + '-' * (bar_length - filled_length)
            logger.debug(f"进度: [{bar}] {progress:.1f}% ({i}/{len(memory_samples)})")

            # 获取该批次消息的group_id
            group_id = None
            if messages and len(messages) > 0 and 'group_id' in messages[0]:
                group_id = messages[0]['group_id']

            compress_rate = global_config.memory_compress_rate
            compressed_memory, similar_topics_dict = await self.memory_compress(messages, compress_rate)
            logger.info(f"压缩后记忆数量: {len(compressed_memory)}，似曾相识的话题: {len(similar_topics_dict)}")
            
            current_time = datetime.datetime.now().timestamp()
            
            for topic, memory in compressed_memory:
                logger.info(f"添加节点: {topic}")
                self.memory_graph.add_dot(topic, memory)  # 不再需要传递group_id，因为已经包含在topic名称中
                all_topics.append(topic)
                
                # 连接相似的已存在主题，但使用较弱的连接强度
                if topic in similar_topics_dict:
                    similar_topics = similar_topics_dict[topic]
                    for similar_topic, similarity in similar_topics:
                        if topic != similar_topic:
                            # 如果是跨群聊的相似主题，使用较弱的连接强度
                            is_cross_group = False
                            if ("_g" in topic and "_g" in similar_topic and 
                                topic.split("_g")[1] != similar_topic.split("_g")[1]):
                                is_cross_group = True
                            
                            # 跨群聊的相似主题使用较弱的连接强度
                            strength = int(similarity * 10)
                            if is_cross_group:
                                strength = int(similarity * 5)  # 降低跨群聊连接的强度
                            
                            logger.info(f"连接相似节点: {topic} 和 {similar_topic} (强度: {strength})")
                            self.memory_graph.G.add_edge(topic, similar_topic, 
                                                       strength=strength,
                                                       created_time=current_time,
                                                       last_modified=current_time)
            
            # 连接同批次的相关话题
            for i in range(len(all_topics)):
                for j in range(i + 1, len(all_topics)):
                    logger.info(f"连接同批次节点: {all_topics[i]} 和 {all_topics[j]}")
                    self.memory_graph.connect_dot(all_topics[i], all_topics[j])

        self.sync_memory_to_db()

    def sync_memory_to_db(self):
        """检查并同步内存中的图结构与数据库"""
        # 获取数据库中所有节点和内存中所有节点
        db_nodes = list(self.memory_graph.db.db.graph_data.nodes.find())
        memory_nodes = list(self.memory_graph.G.nodes(data=True))

        # 转换数据库节点为字典格式,方便查找
        db_nodes_dict = {node['concept']: node for node in db_nodes}

        # 检查并更新节点
        for concept, data in memory_nodes:
            memory_items = data.get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []

            # 计算内存中节点的特征值
            memory_hash = self.calculate_node_hash(concept, memory_items)
            
            # 获取时间信息
            created_time = data.get('created_time', datetime.datetime.now().timestamp())
            last_modified = data.get('last_modified', datetime.datetime.now().timestamp())

            if concept not in db_nodes_dict:
                # 数据库中缺少的节点,添加
                node_data = {
                    'concept': concept,
                    'memory_items': memory_items,
                    'hash': memory_hash,
                    'created_time': created_time,
                    'last_modified': last_modified
                }
                self.memory_graph.db.db.graph_data.nodes.insert_one(node_data)
            else:
                # 获取数据库中节点的特征值
                db_node = db_nodes_dict[concept]
                db_hash = db_node.get('hash', None)

                # 如果特征值不同,则更新节点
                if db_hash != memory_hash:
                    self.memory_graph.db.db.graph_data.nodes.update_one(
                        {'concept': concept},
                        {'$set': {
                            'memory_items': memory_items,
                            'hash': memory_hash,
                            'created_time': created_time,
                            'last_modified': last_modified
                        }}
                    )

        # 处理边的信息
        db_edges = list(self.memory_graph.db.db.graph_data.edges.find())
        memory_edges = list(self.memory_graph.G.edges(data=True))

        # 创建边的哈希值字典
        db_edge_dict = {}
        for edge in db_edges:
            edge_hash = self.calculate_edge_hash(edge['source'], edge['target'])
            db_edge_dict[(edge['source'], edge['target'])] = {
                'hash': edge_hash,
                'strength': edge.get('strength', 1)
            }

        # 检查并更新边
        for source, target, data in memory_edges:
            edge_hash = self.calculate_edge_hash(source, target)
            edge_key = (source, target)
            strength = data.get('strength', 1)
            
            # 获取边的时间信息
            created_time = data.get('created_time', datetime.datetime.now().timestamp())
            last_modified = data.get('last_modified', datetime.datetime.now().timestamp())

            if edge_key not in db_edge_dict:
                # 添加新边
                edge_data = {
                    'source': source,
                    'target': target,
                    'strength': strength,
                    'hash': edge_hash,
                    'created_time': created_time,
                    'last_modified': last_modified
                }
                self.memory_graph.db.db.graph_data.edges.insert_one(edge_data)
            else:
                # 检查边的特征值是否变化
                if db_edge_dict[edge_key]['hash'] != edge_hash:
                    self.memory_graph.db.db.graph_data.edges.update_one(
                        {'source': source, 'target': target},
                        {'$set': {
                            'hash': edge_hash,
                            'strength': strength,
                            'created_time': created_time,
                            'last_modified': last_modified
                        }}
                    )

    def sync_memory_from_db(self):
        """从数据库同步数据到内存中的图结构"""
        current_time = datetime.datetime.now().timestamp()
        need_update = False
        
        # 清空当前图
        self.memory_graph.G.clear()

        # 从数据库加载所有节点
        nodes = list(self.memory_graph.db.db.graph_data.nodes.find())
        for node in nodes:
            concept = node['concept']
            memory_items = node.get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []
            
            # 检查时间字段是否存在
            if 'created_time' not in node or 'last_modified' not in node:
                need_update = True
                # 更新数据库中的节点
                update_data = {}
                if 'created_time' not in node:
                    update_data['created_time'] = current_time
                if 'last_modified' not in node:
                    update_data['last_modified'] = current_time
                
                self.memory_graph.db.db.graph_data.nodes.update_one(
                    {'concept': concept},
                    {'$set': update_data}
                )
                logger.info(f"为节点 {concept} 添加缺失的时间字段")
            
            # 获取时间信息(如果不存在则使用当前时间)
            created_time = node.get('created_time', current_time)
            last_modified = node.get('last_modified', current_time)
            
            # 添加节点到图中
            self.memory_graph.G.add_node(concept, 
                                       memory_items=memory_items,
                                       created_time=created_time,
                                       last_modified=last_modified)

        # 从数据库加载所有边
        edges = list(self.memory_graph.db.db.graph_data.edges.find())
        for edge in edges:
            source = edge['source']
            target = edge['target']
            strength = edge.get('strength', 1)
            
            # 检查时间字段是否存在
            if 'created_time' not in edge or 'last_modified' not in edge:
                need_update = True
                # 更新数据库中的边
                update_data = {}
                if 'created_time' not in edge:
                    update_data['created_time'] = current_time
                if 'last_modified' not in edge:
                    update_data['last_modified'] = current_time
                
                self.memory_graph.db.db.graph_data.edges.update_one(
                    {'source': source, 'target': target},
                    {'$set': update_data}
                )
                logger.info(f"为边 {source} - {target} 添加缺失的时间字段")
            
            # 获取时间信息(如果不存在则使用当前时间)
            created_time = edge.get('created_time', current_time)
            last_modified = edge.get('last_modified', current_time)
            
            # 只有当源节点和目标节点都存在时才添加边
            if source in self.memory_graph.G and target in self.memory_graph.G:
                self.memory_graph.G.add_edge(source, target, 
                                           strength=strength,
                                           created_time=created_time,
                                           last_modified=last_modified)
        
        if need_update:
            logger.success("已为缺失的时间字段进行补充")

    async def operation_forget_topic(self, percentage=0.1):
        """随机选择图中一定比例的节点和边进行检查,根据时间条件决定是否遗忘"""
        # 检查数据库是否为空
        all_nodes = list(self.memory_graph.G.nodes())
        all_edges = list(self.memory_graph.G.edges())
        
        if not all_nodes and not all_edges:
            logger.info("记忆图为空,无需进行遗忘操作")
            return
            
        check_nodes_count = max(1, int(len(all_nodes) * percentage))
        check_edges_count = max(1, int(len(all_edges) * percentage))
        
        nodes_to_check = random.sample(all_nodes, check_nodes_count)
        edges_to_check = random.sample(all_edges, check_edges_count)
        
        edge_changes = {'weakened': 0, 'removed': 0}
        node_changes = {'reduced': 0, 'removed': 0}
        
        current_time = datetime.datetime.now().timestamp()
        
        # 检查并遗忘连接
        logger.info("开始检查连接...")
        for source, target in edges_to_check:
            edge_data = self.memory_graph.G[source][target]
            last_modified = edge_data.get('last_modified')
            # print(source,target)
            # print(f"float(last_modified):{float(last_modified)}"    )
            # print(f"current_time:{current_time}")
            # print(f"current_time - last_modified:{current_time - last_modified}")
            if current_time - last_modified > 3600*global_config.memory_forget_time:  # test
                current_strength = edge_data.get('strength', 1)
                new_strength = current_strength - 1
                
                if new_strength <= 0:
                    self.memory_graph.G.remove_edge(source, target)
                    edge_changes['removed'] += 1
                    logger.info(f"\033[1;31m[连接移除]\033[0m {source} - {target}")
                else:
                    edge_data['strength'] = new_strength
                    edge_data['last_modified'] = current_time
                    edge_changes['weakened'] += 1
                    logger.info(f"\033[1;34m[连接减弱]\033[0m {source} - {target} (强度: {current_strength} -> {new_strength})")
        
        # 检查并遗忘话题
        logger.info("开始检查节点...")
        for node in nodes_to_check:
            node_data = self.memory_graph.G.nodes[node]
            last_modified = node_data.get('last_modified', current_time)
            
            if current_time - last_modified > 3600*24:  # test
                memory_items = node_data.get('memory_items', [])
                if not isinstance(memory_items, list):
                    memory_items = [memory_items] if memory_items else []
                
                if memory_items:
                    current_count = len(memory_items)
                    removed_item = random.choice(memory_items)
                    memory_items.remove(removed_item)
                    
                    if memory_items:
                        self.memory_graph.G.nodes[node]['memory_items'] = memory_items
                        self.memory_graph.G.nodes[node]['last_modified'] = current_time
                        node_changes['reduced'] += 1
                        logger.info(f"\033[1;33m[记忆减少]\033[0m {node} (记忆数量: {current_count} -> {len(memory_items)})")
                    else:
                        self.memory_graph.G.remove_node(node)
                        node_changes['removed'] += 1
                        logger.info(f"\033[1;31m[节点移除]\033[0m {node}")
        
        if any(count > 0 for count in edge_changes.values()) or any(count > 0 for count in node_changes.values()):
            self.sync_memory_to_db()
            logger.info("\n遗忘操作统计:")
            logger.info(f"连接变化: {edge_changes['weakened']} 个减弱, {edge_changes['removed']} 个移除")
            logger.info(f"节点变化: {node_changes['reduced']} 个减少记忆, {node_changes['removed']} 个移除")
        else:
            logger.info("\n本次检查没有节点或连接满足遗忘条件")

    async def merge_memory(self, topic):
        """
        对指定话题的记忆进行合并压缩
        
        Args:
            topic: 要合并的话题节点
        """
        # 获取节点的记忆项
        memory_items = self.memory_graph.G.nodes[topic].get('memory_items', [])
        if not isinstance(memory_items, list):
            memory_items = [memory_items] if memory_items else []

        # 如果记忆项不足，直接返回
        if len(memory_items) < 10:
            return

        # 随机选择10条记忆
        selected_memories = random.sample(memory_items, 10)

        # 拼接成文本
        merged_text = "\n".join(selected_memories)
        logger.debug(f"\n[合并记忆] 话题: {topic}")
        logger.debug(f"选择的记忆:\n{merged_text}")

        # 使用memory_compress生成新的压缩记忆
        compressed_memories, _ = await self.memory_compress(selected_memories, 0.1)

        # 从原记忆列表中移除被选中的记忆
        for memory in selected_memories:
            memory_items.remove(memory)

        # 添加新的压缩记忆
        for _, compressed_memory in compressed_memories:
            memory_items.append(compressed_memory)
            logger.info(f"添加压缩记忆: {compressed_memory}")

        # 更新节点的记忆项
        self.memory_graph.G.nodes[topic]['memory_items'] = memory_items
        logger.debug(f"完成记忆合并，当前记忆数量: {len(memory_items)}")

    async def operation_merge_memory(self, percentage=0.1):
        """
        随机检查一定比例的节点，对内容数量超过100的节点进行记忆合并
        
        Args:
            percentage: 要检查的节点比例，默认为0.1（10%）
        """
        # 获取所有节点
        all_nodes = list(self.memory_graph.G.nodes())
        # 计算要检查的节点数量
        check_count = max(1, int(len(all_nodes) * percentage))
        # 随机选择节点
        nodes_to_check = random.sample(all_nodes, check_count)

        merged_nodes = []
        for node in nodes_to_check:
            # 获取节点的内容条数
            memory_items = self.memory_graph.G.nodes[node].get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []
            content_count = len(memory_items)

            # 如果内容数量超过100，进行合并
            if content_count > 100:
                logger.debug(f"检查节点: {node}, 当前记忆数量: {content_count}")
                await self.merge_memory(node)
                merged_nodes.append(node)

        # 同步到数据库
        if merged_nodes:
            self.sync_memory_to_db()
            logger.debug(f"完成记忆合并操作，共处理 {len(merged_nodes)} 个节点")
        else:
            logger.debug("本次检查没有需要合并的节点")

    def find_topic_llm(self, text, topic_num):
        prompt = f'这是一段文字：{text}。请你从这段话中总结出{topic_num}个关键的概念，可以是名词，动词，或者特定人物，帮我列出来，用逗号,隔开，尽可能精简。只需要列举{topic_num}个话题就好，不要有序号，不要告诉我其他内容。'
        return prompt

    def topic_what(self, text, topic, time_info):
        prompt = f'这是一段文字，{time_info}：{text}。我想让你基于这段文字来概括"{topic}"这个概念，帮我总结成一句自然的话，可以包含时间和人物，以及具体的观点。只输出这句话就好'
        return prompt

    async def _identify_topics(self, text: str, group_id: str = None) -> list:
        """从文本中识别可能的主题
        
        Args:
            text: 输入文本
            group_id: 群聊ID，用于生成群聊特定的主题名
            
        Returns:
            list: 识别出的主题列表
        """
        if text == '[图片]' or text == '[表情包]':
            return None
        topics_response = await self.llm_topic_judge.generate_response(self.find_topic_llm(text, 5))
        # print(f"话题: {topics_response[0]}")
        topics = [topic.strip() for topic in
                  topics_response[0].replace("，", ",").replace("、", ",").replace(" ", ",").split(",") if topic.strip()]
        # print(f"话题: {topics}")

        # 如果提供了群聊ID，添加群聊/群组标识
        if group_id:
            # 检查群聊是否属于特定群组
            group_type = self.get_group_type(group_id)
            if group_type:
                topics = [f"{topic}_GT{group_type}" for topic in topics]
            else:
                topics = [f"{topic}_g{group_id}" for topic in topics]

        return topics

    def _find_similar_topics(self, topics: list, similarity_threshold: float = 0.4, debug_info: str = "") -> list:
        """查找与给定主题相似的记忆主题
        
        Args:
            topics: 主题列表
            similarity_threshold: 相似度阈值
            debug_info: 调试信息前缀
            
        Returns:
            list: (主题, 相似度) 元组列表
        """
        all_memory_topics = self.get_all_node_names()
        all_similar_topics = []

        # 计算每个识别出的主题与记忆主题的相似度
        for topic in topics:
            if debug_info:
                # print(f"\033[1;32m[{debug_info}]\033[0m 正在思考有没有见过: {topic}")
                pass

            # 提取基础主题（去除群聊/群组标识）
            base_topic = topic
            topic_group_type = None
            topic_group_id = None
            
            # 检查是否有群组前缀
            if "_GT" in topic:
                parts = topic.split("_GT")
                base_topic = parts[0]
                if len(parts) > 1:
                    topic_group_type = parts[1]
            # 检查是否有群聊前缀
            elif "_g" in topic:
                parts = topic.split("_g")
                base_topic = parts[0]
                if len(parts) > 1:
                    topic_group_id = parts[1]
                
            topic_vector = text_to_vector(base_topic)
            has_similar_topic = False

            for memory_topic in all_memory_topics:
                # 提取记忆主题的基础主题和群聊/群组ID
                base_memory_topic = memory_topic
                memory_group_type = None
                memory_group_id = None
                
                # 检查是否有群组前缀
                if "_GT" in memory_topic:
                    parts = memory_topic.split("_GT")
                    base_memory_topic = parts[0]
                    if len(parts) > 1:
                        memory_group_type = parts[1]
                # 检查是否有群聊前缀
                elif "_g" in memory_topic:
                    parts = memory_topic.split("_g")
                    base_memory_topic = parts[0]
                    if len(parts) > 1:
                        memory_group_id = parts[1]
                
                memory_vector = text_to_vector(base_memory_topic)
                # 获取所有唯一词
                all_words = set(topic_vector.keys()) | set(memory_vector.keys())
                # 构建向量
                v1 = [topic_vector.get(word, 0) for word in all_words]
                v2 = [memory_vector.get(word, 0) for word in all_words]
                # 计算相似度
                similarity = cosine_similarity(v1, v2)

                # 检查是否应该考虑这个主题
                should_consider = False
                
                # 如果当前主题属于群组
                if topic_group_type:
                    # 只考虑同群组主题或公共主题
                    if (memory_group_type == topic_group_type) or (not memory_group_type and not memory_group_id):
                        should_consider = True
                # 如果当前主题属于特定群聊但不属于群组
                elif topic_group_id:
                    # 只考虑同群聊主题或公共主题
                    if (memory_group_id == topic_group_id) or (not memory_group_type and not memory_group_id):
                        should_consider = True
                # 如果当前主题是公共主题
                else:
                    # 只考虑公共主题
                    if not memory_group_type and not memory_group_id:
                        should_consider = True
                
                # 如果基础主题相似且应该考虑该主题
                if similarity >= similarity_threshold and should_consider:
                    # 如果两个主题属于同一群组/群聊，提高相似度
                    if (topic_group_type and memory_group_type and topic_group_type == memory_group_type) or \
                       (topic_group_id and memory_group_id and topic_group_id == memory_group_id):
                        similarity *= 1.2  # 提高20%的相似度
                        
                    has_similar_topic = True
                    if debug_info:
                        # print(f"\033[1;32m[{debug_info}]\033[0m 找到相似主题: {topic} -> {memory_topic} (相似度: {similarity:.2f})")
                        pass
                    all_similar_topics.append((memory_topic, similarity))

            if not has_similar_topic and debug_info:
                # print(f"\033[1;31m[{debug_info}]\033[0m 没有见过: {topic}  ，呃呃")
                pass

        return all_similar_topics

    def _get_top_topics(self, similar_topics: list, max_topics: int = 5) -> list:
        """获取相似度最高的主题
        
        Args:
            similar_topics: (主题, 相似度) 元组列表
            max_topics: 最大主题数量
            
        Returns:
            list: (主题, 相似度) 元组列表
        """
        seen_topics = set()
        top_topics = []

        for topic, score in sorted(similar_topics, key=lambda x: x[1], reverse=True):
            if topic not in seen_topics and len(top_topics) < max_topics:
                seen_topics.add(topic)
                top_topics.append((topic, score))

        return top_topics

    async def memory_activate_value(self, text: str, max_topics: int = 5, similarity_threshold: float = 0.3) -> int:
        """计算输入文本对记忆的激活程度"""
        logger.info(f"识别主题: {text}")

        # 识别主题
        identified_topics = await self._identify_topics(text)
        if not identified_topics:
            return 0

        # 查找相似主题
        all_similar_topics = self._find_similar_topics(
            identified_topics,
            similarity_threshold=similarity_threshold,
            debug_info="记忆激活"
        )

        if not all_similar_topics:
            return 0

        # 获取最相关的主题
        top_topics = self._get_top_topics(all_similar_topics, max_topics)

        # 如果只找到一个主题，进行惩罚
        if len(top_topics) == 1:
            topic, score = top_topics[0]
            # 获取主题内容数量并计算惩罚系数
            memory_items = self.memory_graph.G.nodes[topic].get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []
            content_count = len(memory_items)
            penalty = 1.0 / (1 + math.log(content_count + 1))

            activation = int(score * 50 * penalty)
            logger.info(
                f"[记忆激活]单主题「{topic}」- 相似度: {score:.3f}, 内容数: {content_count}, 激活值: {activation}")
            return activation

        # 计算关键词匹配率，同时考虑内容数量
        matched_topics = set()
        topic_similarities = {}

        for memory_topic, similarity in top_topics:
            # 计算内容数量惩罚
            memory_items = self.memory_graph.G.nodes[memory_topic].get('memory_items', [])
            if not isinstance(memory_items, list):
                memory_items = [memory_items] if memory_items else []
            content_count = len(memory_items)
            penalty = 1.0 / (1 + math.log(content_count + 1))

            # 对每个记忆主题，检查它与哪些输入主题相似
            for input_topic in identified_topics:
                topic_vector = text_to_vector(input_topic)
                memory_vector = text_to_vector(memory_topic)
                all_words = set(topic_vector.keys()) | set(memory_vector.keys())
                v1 = [topic_vector.get(word, 0) for word in all_words]
                v2 = [memory_vector.get(word, 0) for word in all_words]
                sim = cosine_similarity(v1, v2)
                if sim >= similarity_threshold:
                    matched_topics.add(input_topic)
                    adjusted_sim = sim * penalty
                    topic_similarities[input_topic] = max(topic_similarities.get(input_topic, 0), adjusted_sim)
                    logger.info(
                        f"[记忆激活]主题「{input_topic}」-> 「{memory_topic}」(内容数: {content_count}, 相似度: {adjusted_sim:.3f})")

        # 计算主题匹配率和平均相似度
        topic_match = len(matched_topics) / len(identified_topics)
        average_similarities = sum(topic_similarities.values()) / len(topic_similarities) if topic_similarities else 0

        # 计算最终激活值
        activation = int((topic_match + average_similarities) / 2 * 100)
        logger.info(
            f"[记忆激活]匹配率: {topic_match:.3f}, 平均相似度: {average_similarities:.3f}, 激活值: {activation}")

        return activation

    async def get_relevant_memories(self, text: str, max_topics: int = 5, similarity_threshold: float = 0.4,
                                    max_memory_num: int = 5, group_id: str = None) -> list:
        """根据输入文本获取相关的记忆内容
        
        Args:
            text: 输入文本
            max_topics: 最大主题数量
            similarity_threshold: 相似度阈值
            max_memory_num: 最大记忆数量
            group_id: 群聊ID，用于优先获取指定群聊的记忆
        """
        # 获取群组类型
        group_type = self.get_group_type(group_id) if group_id else None
        
        # 识别主题，传入群聊ID
        identified_topics = await self._identify_topics(text, group_id)

        # 查找相似主题
        all_similar_topics = self._find_similar_topics(
            identified_topics,
            similarity_threshold=similarity_threshold,
            debug_info="记忆检索"
        )

        # 获取最相关的主题
        relevant_topics = self._get_top_topics(all_similar_topics, max_topics)

        # 获取相关记忆内容
        current_group_memories = []  # 当前群组/群聊的记忆
        other_memories = []          # 其他群聊/公共的记忆
        
        for topic, score in relevant_topics:
            # 检查主题是否属于当前群组/群聊
            topic_group_type = None
            topic_group_id = None
            
            # 检查是否有群组前缀
            if "_GT" in topic:
                parts = topic.split("_GT")
                if len(parts) > 1:
                    topic_group_type = parts[1]
            # 检查是否有群聊前缀
            elif "_g" in topic:
                parts = topic.split("_g")
                if len(parts) > 1:
                    topic_group_id = parts[1]
                    
            # 获取该主题的记忆内容
            first_layer, _ = self.memory_graph.get_related_item(topic, depth=1)
            if first_layer:
                # 构建记忆项
                for memory in first_layer:
                    memory_item = {
                        'topic': topic,
                        'similarity': score,
                        'content': memory if not isinstance(memory, dict) else memory.get('content', memory),
                        'group_id': topic_group_id,
                        'group_type': topic_group_type
                    }
                    
                    # 分类记忆
                    # 如果主题属于当前群组
                    if group_type and topic_group_type == group_type:
                        current_group_memories.append(memory_item)
                    # 如果主题属于当前群聊（且群聊不属于任何群组）
                    elif not group_type and topic_group_id == group_id:
                        current_group_memories.append(memory_item)
                    # 如果主题是公共主题（没有群组/群聊标识）
                    elif not topic_group_type and not topic_group_id:
                        other_memories.append(memory_item)
                    # 如果是其他群聊/群组的主题且不是私有群组，也可以添加
                    elif (topic_group_type and not topic_group_type in global_config.memory_private_groups) or \
                         (not topic_group_type and topic_group_id):
                        other_memories.append(memory_item)
        
        # 按相似度排序
        current_group_memories.sort(key=lambda x: x['similarity'], reverse=True)
        other_memories.sort(key=lambda x: x['similarity'], reverse=True)
        
        # 记录日志
        logger.debug(f"[记忆检索] 当前群聊/群组找到 {len(current_group_memories)} 条记忆，其他群聊/公共找到 {len(other_memories)} 条记忆")
        
        # 控制返回的记忆数量
        # 优先添加当前群组/群聊的记忆，如果不足，再添加其他群聊/公共的记忆
        final_memories = current_group_memories
        remaining_slots = max_memory_num - len(final_memories)
        
        if remaining_slots > 0 and other_memories:
            # 添加其他记忆，但最多添加remaining_slots个
            final_memories.extend(other_memories[:remaining_slots])
        
        # 如果记忆总数仍然超过限制，随机采样
        if len(final_memories) > max_memory_num:
            final_memories = random.sample(final_memories, max_memory_num)
            
        # 记录日志，显示最终返回多少条记忆
        logger.debug(f"[记忆检索] 最终返回 {len(final_memories)} 条记忆")
        
        return final_memories

    def get_group_memories(self, group_id: str) -> list:
        """获取特定群聊的所有记忆
        
        Args:
            group_id: 群聊ID
            
        Returns:
            list: 该群聊的记忆列表，每个记忆包含主题和内容
        """
        all_memories = []
        all_nodes = list(self.memory_graph.G.nodes(data=True))
        
        # 获取群组类型
        group_type = self.get_group_type(group_id)
        
        for concept, data in all_nodes:
            # 检查是否应该包含该主题的记忆
            should_include = False
            
            # 如果群聊属于群组
            if group_type and f"_GT{group_type}" in concept:
                should_include = True
            # 如果是特定群聊的记忆
            elif f"_g{group_id}" in concept:
                should_include = True
            # 如果是公共记忆（没有群组/群聊标识）
            elif not "_GT" in concept and not "_g" in concept:
                should_include = True
            
            if should_include:
                memory_items = data.get('memory_items', [])
                if not isinstance(memory_items, list):
                    memory_items = [memory_items] if memory_items else []
                    
                # 添加所有记忆项
                for memory in memory_items:
                    all_memories.append({
                        'topic': concept,
                        'content': memory if not isinstance(memory, dict) else memory.get('content', str(memory))
                    })
        
        return all_memories

    def get_group_type(self, group_id: str) -> str:
        """获取群聊所属的群组类型
        
        Args:
            group_id: 群聊ID
            
        Returns:
            str: 群组类型名称，如果不属于任何群组则返回None
        """
        if not group_id:
            return None
            
        # 检查该群聊ID是否属于任何群组
        for group_name, group_ids in global_config.memory_private_groups.items():
            if group_id in group_ids:
                return group_name
                
        # 如果不属于任何群组，返回None
        return None


def segment_text(text):
    seg_text = list(jieba.cut(text))
    return seg_text

driver = get_driver()
config = driver.config

start_time = time.time()

Database.initialize(
    uri=os.getenv("MONGODB_URI"),
    host=os.getenv("MONGODB_HOST", "127.0.0.1"),
    port=int(os.getenv("MONGODB_PORT", "27017")),
    db_name=os.getenv("DATABASE_NAME", "MegBot"),
    username=os.getenv("MONGODB_USERNAME"),
    password=os.getenv("MONGODB_PASSWORD"),
    auth_source=os.getenv("MONGODB_AUTH_SOURCE"),
)
# 创建记忆图
memory_graph = Memory_graph()
# 创建海马体
hippocampus = Hippocampus(memory_graph)
# 从数据库加载记忆图
hippocampus.sync_memory_from_db()

end_time = time.time()
logger.success(f"加载海马体耗时: {end_time - start_time:.2f} 秒")
