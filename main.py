import requests
import json
import time
import random
import hashlib
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import os
import xml.etree.ElementTree as ET

# -------------------------- 核心配置修改：替换为iptv-org/epg仓库 --------------------------
# iptv-org/epg 公共EPG源（XMLTV格式，全局通用）
IPTV_ORG_EPG_BASE_URL = "https://epg.iptv-org.ru/"
# 备用：直接拉取仓库打包好的EPG文件（gzip压缩）
IPTV_ORG_EPG_GZ_URL = "https://github.com/iptv-org/epg/raw/master/epg.xml.gz"
# 本地缓存EPG文件路径（避免重复请求）
LOCAL_EPG_CACHE = "epg.xml"

thread_mum = 10
headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://m.miguvideo.com",
    "Pragma": "no-cache",
    "Referer": "https://m.miguvideo.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Support-Pendant": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "appCode": "miguvideo_default_h5",
    "appId": "miguvideo",
    "channel": "H5",
    "sec-ch-ua": "\"Chromium\";v=\"136\", \"Microsoft Edge\";v=\"136\", \"Not.A/Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "terminalId": "h5"
}

lives = ['热门', '央视', '卫视', '地方', '体育', '影视', '综艺', '少儿', '新闻', '教育', '熊猫', '纪实']
LIVE = {'热门': 'e7716fea6aa1483c80cfc10b7795fcb8', '体育': '7538163cdac044398cb292ecf75db4e0',
        '央视': '1ff892f2b5ab4a79be6e25b69d2f5d05', '卫视': '0847b3f6c08a4ca28f85ba5701268424',
        '地方': '855e9adc91b04ea18ef3f2dbd43f495b', '影视': '10b0d04cb23d4ac5945c4bc77c7ac44e',
        '新闻': 'c584f67ad63f4bc983c31de3a9be977c', '教育': 'af72267483d94275995a4498b2799ecd',
        '熊猫': 'e76e56e88fff4c11b0168f55e826445d', '综艺': '192a12edfef04b5eb616b878f031f32f',
        '少儿': 'fc2f5b8fd7db43ff88c4243e731ecede', '纪实': 'e1165138bdaa44b9a3138d74af6c6673'}

# -------------------------- 配置 --------------------------
m3u_path = 'migu.m3u'
txt_path = 'migu.txt'
# 修改M3U头部：使用iptv-org的公共EPG源
M3U_HEADER = f'#EXTM3U x-tvg-url="{IPTV_ORG_EPG_GZ_URL}"\n'

# 使用字典存储频道数据
channels_dict = {}  # key: 频道名, value: [m3u_item, txt_item, category, sort_key]
processed_pids = set()  # 用于跟踪已处理的PID
FLAG = 0

appVersion = "2600034600"
appVersionID = appVersion + "-99000-201600010010028"

# -------------------------- 新增：iptv-org EPG频道名映射 --------------------------
def get_iptv_org_tvg_name(channel_name):
    """
    适配iptv-org/epg的频道命名规范，返回标准tvg-name（保证EPG匹配）
    参考：https://github.com/iptv-org/epg/tree/master/epg/sites
    """
    # 央视频道映射（iptv-org规范：CCTV-1、CCTV-5+ 等）
    cctv_map = {
        "CCTV1": "CCTV-1",
        "CCTV2": "CCTV-2",
        "CCTV3": "CCTV-3",
        "CCTV4": "CCTV-4",
        "CCTV5": "CCTV-5",
        "CCTV5+": "CCTV-5+",
        "CCTV6": "CCTV-6",
        "CCTV7": "CCTV-7",
        "CCTV8": "CCTV-8",
        "CCTV9": "CCTV-9",
        "CCTV10": "CCTV-10",
        "CCTV11": "CCTV-11",
        "CCTV12": "CCTV-12",
        "CCTV13": "CCTV-13",
        "CCTV14": "CCTV-14",
        "CCTV15": "CCTV-15",
        "CCTV16": "CCTV-16",
        "CCTV17": "CCTV-17",
        "CCTV4K": "CCTV-4K",
        "CCTV8K": "CCTV-8K",
        "CGTN": "CGTN",
        "CGTN法语": "CGTN-Français",
        "CGTN西班牙语": "CGTN-Español",
        "CGTN俄语": "CGTN-Pусский",
        "CGTN阿拉伯语": "CGTN-العربية",
        "CGTN英语纪录": "CGTN-Documentary"
    }
    
    # 标准化输入频道名
    std_name = channel_name.strip().replace("CCTV ", "CCTV").replace("CCTV-", "CCTV")
    
    # 优先匹配央视映射
    for raw_name, tvg_name in cctv_map.items():
        if raw_name in std_name:
            return tvg_name
    
    # 卫视频道（iptv-org规范：如 湖南卫视、浙江卫视 等，直接用中文）
    satellite_keywords = ["卫视", "湖南", "浙江", "江苏", "东方", "北京", "安徽", "山东", "广东", "天津"]
    for kw in satellite_keywords:
        if kw in std_name:
            return std_name
    
    # 其他频道：直接返回标准化名称（保证和iptv-org的EPG频道名一致）
    return std_name

def download_iptv_org_epg_cache():
    """
    可选：预下载iptv-org的EPG文件到本地（避免M3U远程加载失败）
    """
    if os.path.exists(LOCAL_EPG_CACHE):
        print(f"✅ 本地EPG缓存已存在：{LOCAL_EPG_CACHE}")
        return
    
    try:
        print(f"📥 正在下载iptv-org EPG文件...")
        # 先下载gzip压缩包并解压
        import gzip
        resp = requests.get(IPTV_ORG_EPG_GZ_URL, timeout=30)
        with gzip.open(resp.raw, 'rb') as f_in:
            with open(LOCAL_EPG_CACHE, 'wb') as f_out:
                f_out.write(f_in.read())
        print(f"✅ 本地EPG缓存下载完成：{LOCAL_EPG_CACHE}")
    except Exception as e:
        print(f"⚠️ 本地EPG缓存下载失败：{e}")

def extract_cctv_number(channel_name):
    """提取CCTV频道数字作为排序键"""
    match = re.search(r'CCTV[-\s]?(\d+)', channel_name)
    if match:
        try:
            return int(match.group(1))
        except:
            return 999
    # 对于非数字的CCTV频道，按特定顺序排序
    if 'CCTV' in channel_name:
        if 'CGTN' in channel_name:
            # CGTN系列
            if '法语' in channel_name:
                return 1001
            elif '西班牙语' in channel_name:
                return 1002
            elif '俄语' in channel_name:
                return 1003
            elif '阿拉伯语' in channel_name:
                return 1004
            elif '外语纪录' in channel_name:
                return 1005
            else:
                return 1000  # CGTN
        elif '美洲' in channel_name:
            return 1006
        elif '欧洲' in channel_name:
            return 1007
    return 9999  # 其他频道


def get_sort_key(channel_name):
    """获取排序键：CCTV频道按数字，其他频道按名称"""
    # 提取CCTV数字
    if 'CCTV' in channel_name:
        cctv_num = extract_cctv_number(channel_name)
        return (0, cctv_num, channel_name)  # 0表示CCTV频道
    else:
        return (1, channel_name)  # 1表示其他频道


def is_cctv_channel(channel_name):
    """判断是否是央视频道"""
    return 'CCTV' in channel_name or 'CGTN' in channel_name


def is_satellite_channel(channel_name):
    """判断是否是卫视频道"""
    return '卫视' in channel_name and 'CCTV' not in channel_name


def smart_classify_5_categories(channel_name):
    """5分类智能分类：央视频道、卫视频道、熊猫频道、影音娱乐、生活资讯"""
    # 先判断是否已在字典中（去重）
    if channel_name in channels_dict:
        return None
    
    # 1. 熊猫频道（独立分类）
    if '熊猫' in channel_name:
        return '🐼熊猫频道'
    
    # 2. 央视频道
    if is_cctv_channel(channel_name):
        return '📺央视频道'
    
    # 3. 卫视频道
    if is_satellite_channel(channel_name):
        return '📡卫视频道'
    
    # 4. 影音娱乐（包含影视、少儿、综艺等）
    lower_name = channel_name.lower()
    entertainment_keywords = ['电影', '影视', '影院', '影迷', '少儿', '卡通', '动漫', '动画', 
                             '综艺', '戏曲', '音乐', '秦腔', '嘉佳', '优漫', '新动漫', '经典动画']
    
    for keyword in entertainment_keywords:
        if keyword in channel_name:
            return '🎬影音娱乐'
    
    # 5. 生活资讯（默认分类，包含新闻、体育、教育、纪实、地方台等）
    return '📰生活资讯'


def format_date_ymd():
    current_date = datetime.now()
    return f"{current_date.year}{current_date.month:02d}{current_date.day:02d}"


def writefile(path, content, mode='w'):
    """写文件，支持覆盖和追加模式"""
    with open(path, mode, encoding='utf-8') as f:
        f.write(content)


def md5(text):
    md5_obj = hashlib.md5()
    md5_obj.update(text.encode('utf-8'))
    return md5_obj.hexdigest()


def getSaltAndSign(pid):
    timestamp = str(int(time.time() * 1000))
    random_num = random.randint(0, 999999)
    salt = f"{random_num:06d}25"
    suffix = "2cac4f2c6c3346a5b34e085725ef7e33migu" + salt[:4]
    app_t = timestamp + pid + appVersion[:8]
    sign = md5(md5(app_t) + suffix)
    return {
        "salt": salt,
        "sign": sign,
        "timestamp": timestamp
    }


def get_content(pid):
    _headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "apipost-client-id": "465aea51-4548-495a-8709-7e532dbe3703",
        "apipost-language": "zh-cn",
        "apipost-machine": "3a214a07786002",
        "apipost-platform": "Win",
        "apipost-terminal": "web",
        "apipost-token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJwYXlsb2FkIjp7InVzZXJfaWQiOjM5NDY2NDM3MTIyMzAwMzEzNywidGltZSI6MTc2NTYzMjU2NSwidXVpZCI6ImJlNDJjOTMxLWQ4MjctMTFmMC1hNThiLTUyZTY1ODM4NDNhOSJ9fQ.QU0RXa0e-yB-fwJNjYt_OnyM6RteY3L1BaUWqCrdAB4",
        "apipost-version": "8.2.6",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "sec-ch-ua": '"Chromium";v="136", "Microsoft Edge\";v="136", \"Not.A/Brand\";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "cookie": "apipost-token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJwYXlsb2FkIjp7InVzZXJfaWQiOjM5NDY2NDM3MTIyMzAwMzEzNywidGltZSI6MTc2NTYzMjU2NSwidXVpZCI6ImJlNDJjOTMxLWQ4yjctMTFmMC1hNThiLTUyZTY1ODM4NDNhOSJ9fQ.QU0RXa0e-yB-fwJNjYt_OnyM6RteY3L1BaUWqCrdAB4; SERVERID=236fe4f21bf23223c449a2ac2dc20aa4|1765632725|1765632691; SERVERCORSID=236fe4f21bf23223c449a2ac2dc20aa4|1765632725|1765632691",
        "Referer": "https://workspace.apipost.net/57a21612a051000/apis",
        "Referrer-Policy": "strict-origin-when-cross-origin"
    }
    result = getSaltAndSign(pid)
    rateType = "2" if pid == "608831231" else "3"
    URL = f"https://play.miguvideo.com/playurl/v1/play/playurl?sign={result['sign']}&rateType={rateType}&contId={pid}&timestamp={result['timestamp']}&salt={result['salt']}"
    params = URL.split("?")[1].split("&")
    body = {
        "option": {
            "scene": "http_request",
            "lang": "zh-cn",
            "globals": {},
            "project": {
                "request": {
                    "header": {
                        "parameter": [
                            {
                                "key": "Accept",
                                "value": "*/*",
                                "is_checked": 1,
                                "field_type": "String",
                                "is_system": 1
                            },
                            {
                                "key": "Accept-Encoding",
                                "value": "gzip, deflate, br",
                                "is_checked": 1,
                                "field_type": "String",
                                "is_system": 1
                            },
                            {
                                "key": "User-Agent",
                                "value": "PostmanRuntime-ApipostRuntime/1.1.0",
                                "is_checked": 1,
                                "field_type": "String",
                                "is_system": 1
                            },
                            {
                                "key": "Connection",
                                "value": "keep-alive",
                                "is_checked": 1,
                                "field_type": "String",
                                "is_system": 1
                            }
                        ]
                    },
                    "query": {"parameter": []},
                    "body": {"parameter": []},
                    "cookie": {"parameter": []},
                    "auth": {"type": "noauth"},
                    "pre_tasks": [],
                    "post_tasks": []
                }
            },
            "env": {
                "env_id": "1",
                "env_name": "默认环境",
                "env_pre_url": "",
                "env_pre_urls": {
                    "1": {"server_id": "1", "name": "默认服务", "sort": 1000, "uri": ""},
                    "default": {"server_id": "1", "name": "默认服务", "sort": 1000, "uri": ""}
                },
                "environment": {}
            },
            "cookies": {"switch": 1, "data": []},
            "system_configs": {
                "send_timeout": 0,
                "auto_redirect": -1,
                "max_redirect_time": 5,
                "auto_gen_mock_url": -1,
                "request_param_auto_json": -1,
                "proxy": {
                    "type": 2, "envfirst": 1, "bypass": [], "protocols": ["http"],
                    "auth": {"authenticate": -1, "host": "", "username": "", "password": ""}
                },
                "ca_cert": {"open": -1, "path": "", "base64": ""},
                "client_cert": {}
            },
            "custom_functions": {},
            "collection": [{
                "target_id": "3c5fd6a9786002", "target_type": "api", "parent_id": "0", "name": "MIGU",
                "request": {
                    "auth": {"type": "inherit"},
                    "body": {
                        "mode": "None", "parameter": [], "raw": "", "raw_parameter": [],
                        "raw_schema": {"type": "object"}, "binary": None
                    },
                    "pre_tasks": [], "post_tasks": [],
                    "header": {"parameter": [
                        {"description": "", "field_type": "string", "is_checked": 1, "key": " AppVersion", "value": "2600034600", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c60653273e0b3"},
                        {"description": "", "field_type": "string", "is_checked": 1, "key": "TerminalId", "value": "android", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c6075c1f3e0e1"},
                        {"description": "", "field_type": "string", "is_checked": 1, "key": "X-UP-CLIENT-CHANNEL-ID", "value": "2600034600-99000-201600010010028", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c60858bb3e10c"}
                    ]},
                    "query": {"parameter": [
                        {"param_id": "3c5fd74233e004", "field_type": "string", "is_checked": 1, "key": "sign", "not_None": 1, "value": params[0].split("=")[1], "description": ""},
                        {"param_id": "3c6022f433e030", "field_type": "string", "is_checked": 1, "key": "rateType", "not_None": 1, "value": params[1].split("=")[1], "description": ""},
                        {"param_id": "3c60354133e05b", "field_type": "string", "is_checked": 1, "key": "contId", "not_None": 1, "value": params[2].split("=")[1], "description": ""},
                        {"param_id": "3c605e4bf860b1", "field_type": "String", "is_checked": 1, "key": "timestamp", "not_None": 1, "value": params[3].split("=")[1], "description": ""},
                        {"param_id": "3c605e4c3860b2", "field_type": "String", "is_checked": 1, "key": "salt", "not_None": 1, "value": params[4].split("=")[1], "description": ""}
                    ], "query_add_equal": 1},
                    "cookie": {"parameter": [], "cookie_encode": 1},
                    "restful": {"parameter": []},
                    "tabs_default_active_key": "query"
                },
                "parents": [], "method": "POST", "protocol": "http/1.1", "url": URL, "pre_url": ""
            }],
            "database_configs": {}
        },
        "test_events": [{
            "type": "api",
            "data": {"target_id": "3c5fd6a9786002", "project_id": "57a21612a051000", "parent_id": "0", "target_type": "api"}
        }]
    }
    body = json.dumps(body, separators=(",", ":"))
    url = "https://workspace.apipost.net/proxy/v2/http"
    resp = requests.post(url, headers=_headers, data=body).json()
    return json.loads(resp["data"]["data"]["response"]["body"])


def getddCalcu720p(url, pID):
    puData = url.split("&puData=")[1]
    keys = "cdabyzwxkl"
    ddCalcu = []
    for i in range(0, int(len(puData) / 2)):
        ddCalcu.append(puData[int(len(puData)) - i - 1])
        ddCalcu.append(puData[i])
        if i == 1:
            ddCalcu.append("v")
        if i == 2:
            ddCalcu.append(keys[int(format_date_ymd()[2])])
        if i == 3:
            ddCalcu.append(keys[int(pID[6])])
        if i == 4:
            ddCalcu.append("a")
    return f'{url}&ddCalcu={"".join(ddCalcu)}&sv=10004&ct=android'


def append_All_Live(live, flag, data):
    try:
        # 检查是否已处理过该PID
        if data["pID"] in processed_pids:
            return
        processed_pids.add(data["pID"])
        
        respData = get_content(data["pID"])
        playurl = getddCalcu720p(respData["body"]["urlInfo"]["url"], data["pID"])

        if playurl != "":
            z = 1
            while z <= 6:
                obj = requests.get(playurl, allow_redirects=False)
                location = obj.headers.get("Location", "")
                if not location:
                    continue
                if location.startswith("http://hlsz"):
                    playurl = location
                    break
                if z <= 6:
                    time.sleep(0.15)
                z += 1

        if z != 7:
            # 处理频道名
            ch_name = data["name"].replace("CCTV", "CCTV-") if "CCTV" in data["name"] else data["name"]
            
            # 智能分类（使用5分类方案）
            category = smart_classify_5_categories(ch_name)
            if category is None:
                return  # 频道已存在，跳过
                
            # 获取排序键
            sort_key = get_sort_key(ch_name)
            
            # 核心修改：1. 适配iptv-org的tvg-name（保证EPG匹配）
            tvg_name = get_iptv_org_tvg_name(ch_name)
            # 核心修改：2. 使用iptv-org仓库的logo（可选，也可保留原logo逻辑）
            epg_logo_base = "https://raw.githubusercontent.com/iptv-org/iptv/refs/heads/master/logos/"
            standard_logo_name = tvg_name.replace("CCTV-", "cctv-").replace("+", "plus").lower()
            tvg_logo = f"{epg_logo_base}{standard_logo_name}.png"
            
            # 构造m3u条目（适配iptv-org EPG）
            m3u_item = f'#EXTINF:-1 tvg-name="{tvg_name}" tvg-logo="{tvg_logo}" group-title="{category}",{ch_name}\n{playurl}\n'
            
            # 构造txt条目
            txt_item = f"{ch_name},{playurl}\n"
            
            # 存储到字典
            channels_dict[ch_name] = [m3u_item, txt_item, category, sort_key]
            print(f'频道 [{ch_name}]【{category}】更新成功！(tvg-name: {tvg_name}, EPG源: iptv-org)')
        else:
            print(f'频道 [{data["name"]}] 更新失败！')
    except Exception as e:
        print(f'频道 [{data["name"]}] 更新失败！')
        print(f"ERROR:{e}")


def update(live, url):
    global FLAG
    pool = ThreadPoolExecutor(thread_mum)
    response = requests.get(url, headers=headers).json()
    dataList = response["body"]["dataList"]
    for flag, data in enumerate(dataList):
        pool.submit(append_All_Live, live, FLAG + flag, data)
    pool.shutdown()
    FLAG += len(dataList)


def main():
    # 可选：预下载iptv-org的EPG到本地（提升稳定性）
    download_iptv_org_epg_cache()
    
    # 1. 初始化文件
    writefile(m3u_path, M3U_HEADER, 'w')
    writefile(txt_path, "", 'w')
    
    # 2. 遍历爬取
    for live in lives:
        print(f"\n分类 ----- [{live}] ----- 开始更新. . .")
        url = f'https://program-sc.miguvideo.com/live/v2/tv-data/{LIVE[live]}'
        update(live, url)
    
    # 3. 按分类组织频道数据
    category_channels = defaultdict(list)
    
    for ch_name, (m3u_item, txt_item, category, sort_key) in channels_dict.items():
        category_channels[category].append((sort_key, ch_name, m3u_item, txt_item))
    
    # 4. 对每个分类下的频道进行排序（从小到大）
    for category in category_channels:
        category_channels[category].sort(key=lambda x: x[0])
    
    # 5. 按分类顺序写入m3u文件
    category_order = [
        '📺央视频道',
        '📡卫视频道',
        '🐼熊猫频道',
        '🎬影音娱乐',
        '📰生活资讯'
    ]
    
    for category in category_order:
        if category in category_channels:
            for sort_key, ch_name, m3u_item, txt_item in category_channels[category]:
                writefile(m3u_path, m3u_item, 'a')
    
    # 6. 按分类写入txt文件
    for category in category_order:
        if category in category_channels and category_channels[category]:
            # 写分类头
            writefile(txt_path, f"{category},#genre#\n", 'a')
            # 写该分类下的频道
            for sort_key, ch_name, m3u_item, txt_item in category_channels[category]:
                writefile(txt_path, txt_item, 'a')
    
    # 7. 输出统计信息
    total_channels = len(channels_dict)
    
    # 统计各分类数量
    category_stats = {}
    for category in category_order:
        if category in category_channels:
            category_stats[category] = len(category_channels[category])
        else:
            category_stats[category] = 0
    
    print(f"\n✅ 双格式文件生成完成！")
    print(f"📁 M3U格式：{m3u_path} (EPG源: {IPTV_ORG_EPG_GZ_URL})")
    print(f"📁 TXT格式：{txt_path}")
    print(f"📊 总计频道数：{total_channels}")
    
    # 打印分类统计
    print("\n📋 5分类统计：")
    for category in category_order:
        count = category_stats[category]
        percentage = (count / total_channels * 100) if total_channels > 0 else 0
        print(f"  {category}: {count} 个 ({percentage:.1f}%)")


if __name__ == "__main__":
    main()
