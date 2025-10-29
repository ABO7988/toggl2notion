import os
from requests.auth import HTTPBasicAuth
import pendulum
from retrying import retry
import requests
from notion_helper import NotionHelper
import utils

from config import time_properties_type_dict, TAG_ICON_URL
from utils import get_icon, split_emoji_from_string
from dotenv import load_dotenv
load_dotenv()



def insert_to_notion():
    # 缓存字典，避免重复请求相同的项目和客户端信息
    project_cache = {}
    client_cache = {}
    
    # 检查必需的数据库配置
    if not notion_helper.time_database_id:
        print("错误: 时间记录数据库未配置！请检查 Notion 配置。")
        return
    
    # 打印数据库配置状态
    print(f"=== 数据库配置状态 ===")
    print(f"时间记录数据库: {'✓ 已配置' if notion_helper.time_database_id else '✗ 未配置'}")
    print(f"项目数据库: {'✓ 已配置' if notion_helper.project_database_id else '✗ 未配置'}")
    print(f"Client 数据库: {'✓ 已配置' if notion_helper.client_database_id else '✗ 未配置'}")
    print(f"标签数据库: {'✓ 已配置' if notion_helper.tag_database_id else '✗ 未配置'}")
    print()
    
    # 获取当前UTC时间
    now = pendulum.now("Asia/Shanghai")
    # toggl只支持90天的数据
    end = now.to_iso8601_string()
    start = now.subtract(days=90)
    # 格式化时间
    start = start.to_iso8601_string()
    sorts = [{"property": "时间", "direction": "descending"}]
    page_size = 1
    response = notion_helper.query(
        database_id=notion_helper.time_database_id, sorts=sorts, page_size=page_size
    )
    if len(response.get("results")) > 0:
        start = (
            response.get("results")[0]
            .get("properties")
            .get("时间")
            .get("date")
            .get("end")
        )
    params = {"start_date": start, "end_date": end}
    print(f"Fetching time entries from {start} to {end}")
    response = requests.get(
        "https://api.track.toggl.com/api/v9/me/time_entries", params=params, auth=auth
    )
    print(f"Response : {response.text}")
    if response.ok:
        try:
            time_entries = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as e:
            print(f"Failed to parse time entries JSON. Status: {response.status_code}, Response: {response.text}")
            return
        time_entries.sort(key=lambda x: x["start"], reverse=False)
        for task in time_entries:
            if task.get("pid") is not None and task.get("stop") is not None:
                item = {}
                tags = task.get("tags")
                if tags and notion_helper.tag_database_id:
                    item["标签"] = [
                        notion_helper.get_relation_id(
                            tag, notion_helper.tag_database_id, get_icon(TAG_ICON_URL)
                        )
                        for tag in tags
                    ]
                elif tags and not notion_helper.tag_database_id:
                    print(f"警告: 标签数据库未配置，跳过标签关系")
                id = task.get("id")
                item["Id"] = id
                project_id = task.get("project_id")
                if project_id:
                    workspace_id = task.get("workspace_id")
                    start = pendulum.parse(task.get("start"))
                    stop = pendulum.parse(task.get("stop"))
                    start = start.in_timezone("Asia/Shanghai").int_timestamp
                    stop = stop.in_timezone("Asia/Shanghai").int_timestamp
                    item["时间"] = (start, stop)
                    
                    # 使用缓存，避免重复请求相同的项目
                    if project_id not in project_cache:
                        response = requests.get(
                            f"https://api.track.toggl.com/api/v9/workspaces/{workspace_id}/projects/{project_id}",
                            auth=auth,
                        )
                        if not response.ok:
                            print(f"Failed to get project info: {response.status_code}, {response.text}")
                            continue
                        
                        try:
                            project_data = response.json()
                            project_cache[project_id] = project_data
                        except (requests.exceptions.JSONDecodeError, ValueError) as e:
                            print(f"Failed to parse project JSON for project_id {project_id}. Status: {response.status_code}, Response: {response.text}")
                            continue
                    else:
                        project_data = project_cache[project_id]
                    
                    project = project_data.get("name")
                    if not project:
                        print(f"Project name not found for project_id {project_id}")
                        continue
                    
                    emoji, project = split_emoji_from_string(project)
                    item["toggl项目"] = project
                    client_id = project_data.get("cid")
                    #默认金币设置为1
                    project_properties = {"金币":{"number": 1}}
                    if client_id:
                        # 使用缓存，避免重复请求相同的客户端
                        if client_id not in client_cache:
                            response = requests.get(
                                f"https://api.track.toggl.com/api/v9/workspaces/{workspace_id}/clients/{client_id}",
                                auth=auth,
                            )
                            if response.ok:
                                try:
                                    client_data = response.json()
                                    client_cache[client_id] = client_data
                                except (requests.exceptions.JSONDecodeError, ValueError) as e:
                                    print(f"Failed to parse client JSON for client_id {client_id}. Status: {response.status_code}, Response: {response.text}")
                                    client_cache[client_id] = None
                            else:
                                print(f"Failed to get client info: {response.status_code}, {response.text}")
                                client_cache[client_id] = None
                        
                        client_data = client_cache.get(client_id)
                        if client_data:
                            client = client_data.get("name")
                            if client:
                                client_emoji, client = split_emoji_from_string(client)
                                # 检查 Client 数据库是否存在
                                if notion_helper.client_database_id:
                                    item["Client"] = [
                                        notion_helper.get_relation_id(
                                            client,
                                            notion_helper.client_database_id,
                                            {"type": "emoji", "emoji": client_emoji},
                                        )
                                    ]
                                    project_properties["Client"] = {
                                        "relation": [{"id": id} for id in item.get("Client")]
                                    }
                                else:
                                    print(f"警告: Client 数据库未配置，跳过客户端关系")
                            else:
                                print(f"Client name not found for client_id {client_id}")
                    # 检查项目数据库是否存在
                    if notion_helper.project_database_id:
                        item["项目"] = [
                            notion_helper.get_relation_id(
                                project,
                                notion_helper.project_database_id,
                                {"type": "emoji", "emoji": emoji},
                                properties=project_properties,
                            )
                        ]
                    else:
                        print(f"警告: 项目数据库未配置，跳过项目关系")
                if task.get("description") is not None:
                    item["toggl任务"] = task.get("description")
                properties = utils.get_properties(item, time_properties_type_dict)
                parent = {
                    "database_id": notion_helper.time_database_id,
                    "type": "database_id",
                }
                notion_helper.get_date_relation(
                    properties, pendulum.from_timestamp(stop, tz="Asia/Shanghai")
                )
                icon = {"type": "emoji", "emoji": emoji}
                notion_helper.create_page(parent=parent, properties=properties, icon=icon)
        
        # 打印缓存统计信息
        print(f"\n=== API 调用统计 ===")
        print(f"项目缓存数量: {len(project_cache)} 个不同项目")
        print(f"客户端缓存数量: {len(client_cache)} 个不同客户端")
    else:
        print(f"get toggl data error {response.text}")



if __name__ == "__main__":
    notion_helper = NotionHelper()
    auth = HTTPBasicAuth(f"{os.getenv('EMAIL').strip()}", f"{os.getenv('PASSWORD').strip()}")
    insert_to_notion()
