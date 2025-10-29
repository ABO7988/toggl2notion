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
                if tags:
                    item["标签"] = [
                        notion_helper.get_relation_id(
                            tag, notion_helper.tag_database_id, get_icon(TAG_ICON_URL)
                        )
                        for tag in tags
                    ]
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
                    response = requests.get(
                        f"https://api.track.toggl.com/api/v9/workspaces/{workspace_id}/projects/{project_id}",
                        auth=auth,
                    )
                    if not response.ok:
                        print(f"Failed to get project info: {response.status_code}, {response.text}")
                        continue
                    
                    try:
                        project_data = response.json()
                    except (requests.exceptions.JSONDecodeError, ValueError) as e:
                        print(f"Failed to parse project JSON for project_id {project_id}. Status: {response.status_code}, Response: {response.text}")
                        continue
                    
                    project = project_data.get("name")
                    if not project:
                        print(f"Project name not found for project_id {project_id}")
                        continue
                    
                    emoji, project = split_emoji_from_string(project)
                    item["标题"] = project
                    client_id = project_data.get("cid")
                    #默认金币设置为1
                    project_properties = {"金币":{"number": 1}}
                    if client_id:
                        response = requests.get(
                            f"https://api.track.toggl.com/api/v9/workspaces/{workspace_id}/clients/{client_id}",
                            auth=auth,
                        )
                        if response.ok:
                            try:
                                client_data = response.json()
                            except (requests.exceptions.JSONDecodeError, ValueError) as e:
                                print(f"Failed to parse client JSON for client_id {client_id}. Status: {response.status_code}, Response: {response.text}")
                                client_data = None
                            
                            if client_data:
                                client = client_data.get("name")
                                if client:
                                    client_emoji, client = split_emoji_from_string(client)
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
                                    print(f"Client name not found for client_id {client_id}")
                        else:
                            print(f"Failed to get client info: {response.status_code}, {response.text}")
                    item["Project"] = [
                        notion_helper.get_relation_id(
                            project,
                            notion_helper.project_database_id,
                            {"type": "emoji", "emoji": emoji},
                            properties=project_properties,
                        )
                    ]
                if task.get("description") is not None:
                    item["备注"] = task.get("description")
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
    else:
        print(f"get toggl data error {response.text}")



if __name__ == "__main__":
    notion_helper = NotionHelper()
    auth = HTTPBasicAuth(f"{os.getenv('EMAIL').strip()}", f"{os.getenv('PASSWORD').strip()}")
    insert_to_notion()
