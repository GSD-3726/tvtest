import requests
import json
import time
import random
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# 基础配置（原逻辑不变）
thread_mum = 10  # 线程数
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
# 原爬取分类（仅用于遍历爬取，分类逻辑已替换为智能判断）
lives = ['热门', '央视', '卫视', '地方', '体育', '影视', '综艺', '少儿', '新闻', '教育', '熊猫', '纪实']
LIVE = {'热门': 'e7716fea6aa1483c80cfc10b7795fcb8', '体育': '7538163cdac044398cb292ecf75db4e0',
        '央视': '1ff892f2b5ab4a79be6e25b69d2f5d05', '卫视': '0847b3f6c08a4ca28f85ba5701268424',
        '地方': '855e9adc91b04ea18ef3f2dbd43f495b', '影视': '10b0d04cb23d4ac5945c4bc77c7ac44e',
        '新闻': 'c584f67ad63f4bc983c31de3a9be977c', '教育': 'af72267483d94275995a4498b2799ecd',
        '熊猫': 'e76e56e88fff4c11b0168f55e826445d', '综艺': '192a12edfef04b5eb616b878f031f32f',
        '少儿': 'fc2f5b8fd7db43ff88c4243e731ecede', '纪实': 'e1165138bdaa44b9a3138d74af6c6673'}

# 输出文件配置（匹配指定格式）
m3u_path = 'migu.m3u'  # m3u输出路径
txt_path = 'migu.txt'  # txt输出路径
# m3u固定文件头（严格匹配你的要求）
M3U_HEADER = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/GSD-3726/IPTV/refs/heads/master/output/epg/epg.gz"\n'
# 按分类存储数据（用于去重和排序，初始化为空）
m3u_data = {"📺央视频道": [], "📺卫视频道": [], "📺其他": []}
txt_data = {"📺央视频道": [], "📺卫视频道": [], "📺其他": []}
# 去重集合（记录已添加的「频道名+地址」，避免重复）
exist_channels = set()

# 咪咕接口配置（原逻辑不变）
appVersion = "2600034600"
appVersionID = appVersion + "-99000-201600010010028"
FLAG = 0  # 原全局索引（逻辑不变）


# -------------------------- 核心：智能分类函数 --------------------------
def smart_classify(ch_name):
    """
    按频道名智能判断分类，彻底抛弃原爬取分类映射
    :param ch_name: 处理后的频道名
    :return: 📺央视频道 / 📺卫视频道 / 📺其他
    """
    ch_name = ch_name.strip()
    # 第一优先级：央视频道（含CCTV/CGTN，无论后缀）
    if 'CCTV-' in ch_name or 'CCTV' in ch_name or 'CGTN' in ch_name:
        return "📺央视频道"
    # 第二优先级：卫视频道（以卫视结尾）
    elif ch_name.endswith('卫视'):
        return "📺卫视频道"
    # 其他所有频道
    else:
        return "📺其他"


# -------------------------- 修复版：央视排序核心函数 --------------------------
def sort_cctv_channels(channel_list, is_m3u=True):
    """
    对央视频道进行数字从小到大排序，精准提取数字部分，过滤非数字字符
    :param channel_list: 央视频道条目列表（m3u/txt）
    :param is_m3u: 是否为m3u格式，True=m3u条目，False=txt条目
    :return: 排序后的列表
    """
    def get_cctv_num(channel):
        """
        精准提取央视数字部分，解决非数字字符转浮点报错
        示例：CCTV-5+体育赛事→5.1，CCTV-13新闻→13，CGTN→999
        """
        # 第一步：提取纯频道名（m3u/txt分别处理）
        if is_m3u:
            # m3u条目格式：#EXTINF:-1 tvg-name="CCTV-5+体育赛事" ...,CCTV-5+体育赛事\n地址
            try:
                ch_name = channel.split('tvg-name="')[1].split('"')[0]
            except IndexError:
                return 999  # 格式异常排最后
        else:
            # txt条目格式：CCTV-5+体育赛事,地址
            try:
                ch_name = channel.split(',')[0]
            except IndexError:
                return 999  # 格式异常排最后

        # 第二步：精准提取央视数字部分（过滤所有非数字/非+字符）
        ch_name = ch_name.strip()
        # 只保留CCTV相关的部分，过滤后缀（如体育赛事/科教/新闻）
        if 'CCTV-' in ch_name:
            cctv_part = ch_name.split('CCTV-')[1].split()[0]  # 取CCTV-后第一个空格前的内容
        elif 'CCTV' in ch_name and 'CCTV-' not in ch_name:
            cctv_part = ch_name.split('CCTV')[1].split()[0]   # 处理无横杠的CCTV
        else:
            return 999  # 非纯CCTV频道（如CGTN）排最后

        # 第三步：提取纯数字+处理+号，过滤其他字符
        num_str = ''
        has_plus = False
        for c in cctv_part:
            if c.isdigit():
                num_str += c
            elif c == '+':
                has_plus = True
            else:
                break  # 遇到非数字/非+字符，停止提取
        # 处理提取结果
        if not num_str:
            return 999  # 无数字排最后
        # 数字转浮点数，+号处理为.1（如5+→5.1，排在5之后6之前）
        num = float(num_str)
        if has_plus:
            num += 0.1
        return num

    # 按提取的数字升序排序
    channel_list.sort(key=get_cctv_num)
    return channel_list


# -------------------------- 工具函数（原逻辑+小优化） --------------------------
def format_date_ymd():
    """格式化日期为「年+补0月+补0日」字符串（对应JS逻辑）"""
    current_date = datetime.now()
    return f"{current_date.year}{current_date.month:02d}{current_date.day:02d}"


def writefile(path, content):
    """覆盖写文件（utf-8编码，避免乱码）"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def appendfile(path, content):
    """追加写文件（utf-8编码）"""
    with open(path, 'a+', encoding='utf-8') as f:
        f.write(content)


def md5(text):
    """MD5加密：返回32位小写结果"""
    md5_obj = hashlib.md5()
    md5_obj.update(text.encode('utf-8'))
    return md5_obj.hexdigest()


def getSaltAndSign(pid):
    """生成签名（原逻辑不变）"""
    timestamp = str(int(time.time() * 1000))
    random_num = random.randint(0, 999999)
    salt = f"{random_num:06d}25"
    suffix = "2cac4f2c6c3346a5b34e085725ef7e33migu" + salt[:4]
    app_t = timestamp + pid + appVersion[:8]
    sign = md5(md5(app_t) + suffix)
    return {"salt": salt, "sign": sign, "timestamp": timestamp}


# -------------------------- 修复版：接口请求（增加None判断，拦截异常） --------------------------
def get_content(pid):
    """获取播放地址接口数据（增加异常拦截，返回None则代表失败）"""
    try:
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
            "sec-ch-ua": '"Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cookie": "apipost-token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJwYXlsb2FkIjp7InVzZXJfaWQiOjM5NDY2NDM3MTIyMzAwMzEzNywidGltZSI6MTc2NTYzMjU2NSwidXVpZCI6ImJlNDJjOTMxLWQ4MjctMTFmMC1hNThiLTUyZTY1ODM4NDNhOSJ9fQ.QU0RXa0e-yB-fwJNjYt_OnyM6RteY3L1BaUWqCrdAB4; SERVERID=236fe4f21bf23223c449a2ac2dc20aa4|1765632725|1765632691; SERVERCORSID=236fe4f21bf23223c449a2ac2dc20aa4|1765632725|1765632691",
            "Referer": "https://workspace.apipost.net/57a21612a051000/apis",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        }
        result = getSaltAndSign(pid)
        rateType = "2" if pid == "608831231" else "3"  # 广东卫视特殊处理
        URL = f"https://play.miguvideo.com/playurl/v1/play/playurl?sign={result['sign']}&rateType={rateType}&contId={pid}&timestamp={result['timestamp']}&salt={result['salt']}"
        params = URL.split("?")[1].split("&")
        # 修复核心：重新梳理body括号嵌套，完全匹配无错误
        body = {
            "option": {
                "scene": "http_request",
                "lang": "zh-cn",
                "globals": {},
                "project": {
                    "request": {
                        "header": {
                            "parameter": [
                                {"key": "Accept", "value": "*/*", "is_checked": 1, "field_type": "String", "is_system": 1},
                                {"key": "Accept-Encoding", "value": "gzip, deflate, br", "is_checked": 1, "field_type": "String", "is_system": 1},
                                {"key": "User-Agent", "value": "PostmanRuntime-ApipostRuntime/1.1.0", "is_checked": 1, "field_type": "String", "is_system": 1},
                                {"key": "Connection", "value": "keep-alive", "is_checked": 1, "field_type": "String", "is_system": 1}
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
                "collection": [
                    {
                        "target_id": "3c5fd6a9786002",
                        "target_type": "api",
                        "parent_id": "0",
                        "name": "MIGU",
                        "request": {
                            "auth": {"type": "inherit"},
                            "body": {
                                "mode": "None",
                                "parameter": [],
                                "raw": "",
                                "raw_parameter": [],
                                "raw_schema": {"type": "object"},
                                "binary": None
                            },
                            "pre_tasks": [],
                            "post_tasks": [],
                            "header": {
                                "parameter": [
                                    {"description": "", "field_type": "string", "is_checked": 1, "key": " AppVersion", "value": "2600034600", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c60653273e0b3"},
                                    {"description": "", "field_type": "string", "is_checked": 1, "key": "TerminalId", "value": "android", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c6075c1f3e0e1"},
                                    {"description": "", "field_type": "string", "is_checked": 1, "key": "X-UP-CLIENT-CHANNEL-ID", "value": "2600034600-99000-201600010010028", "not_None": 1, "schema": {"type": "string"}, "param_id": "3c60858bb3e10c"}
                                ]
                            },
                            "query": {
                                "parameter": [
                                    {"param_id": "3c5fd74233e004", "field_type": "string", "is_checked": 1, "key": "sign", "not_None": 1, "value": params[0].split("=")[1], "description": ""},
                                    {"param_id": "3c6022f433e030", "field_type": "string", "is_checked": 1, "key": "rateType", "not_None": 1, "value": params[1].split("=")[1], "description": ""},
                                    {"param_id": "3c60354133e05b", "field_type": "string", "is_checked": 1, "key": "contId", "not_None": 1, "value": params[2].split("=")[1], "description": ""},
                                    {"param_id": "3c605e4bf860b1", "field_type": "String", "is_checked": 1, "key": "timestamp", "not_None": 1, "value": params[3].split("=")[1], "description": ""},
                                    {"param_id": "3c605e4c3860b2", "field_type": "String", "is_checked": 1, "key": "salt", "not_None": 1, "value": params[4].split("=")[1], "description": ""}
                                ],
                                "query_add_equal": 1
                            },
                            "cookie": {"parameter": [], "cookie_encode": 1},
                            "restful": {"parameter": []},
                            "tabs_default_active_key": "query"
                        },
                        "parents": [],
                        "method": "POST",
                        "protocol": "http/1.1",
                        "url": URL,
                        "pre_url": ""
                    }
                ],
                "database_configs": {}
            },
            "test_events": [
                {
                    "type": "api",
                    "data": {"target_id": "3c5fd6a9786002", "project_id": "57a21612a051000", "parent_id": "0", "target_type": "api"}
                }
            ]
        }
        body = json.dumps(body, separators=(",", ":"))
        url = "https://workspace.apipost.net/proxy/v2/http"
        resp = requests.post(url, headers=_headers, data=body, timeout=10).json()
        # 增加返回值非空判断
        if resp and "data" in resp and resp["data"] and "data" in resp["data"] and resp["data"]["data"]:
            return json.loads(resp["data"]["data"]["response"]["body"])
        else:
            return None
    except Exception as e:
        print(f"接口请求失败：{e}")
        return None


# -------------------------- 修复版：地址解密（增加关键字段判断） --------------------------
def getddCalcu720p(url, pID):
    """解密播放地址（增加&puData=存在判断，避免list index out of range）"""
    try:
        # 先判断关键字段是否存在
        if "&puData=" not in url:
            return ""
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
    except Exception as e:
        print(f"地址解密失败：{e}")
        return ""


# -------------------------- 修复版：单频道处理（全链路异常拦截） --------------------------
def append_All_Live(live, flag, data):
    """处理单频道数据（增加全链路None判断，智能分类，自动去重）"""
    global exist_channels
    try:
        # 拦截1：data或pID为空
        if not data or "pID" not in data or not data["pID"] or "name" not in data:
            print(f"频道数据异常：{data}")
            return
        ch_origin_name = data["name"].strip()
        # 处理频道名（统一格式，CCTV→CCTV-）
        ch_name = ch_origin_name.replace("CCTV", "CCTV-") if "CCTV" in ch_origin_name and "CCTV-" not in ch_origin_name else ch_origin_name

        # 拦截2：接口返回None
        respData = get_content(data["pID"])
        if not respData or "body" not in respData or not respData["body"] or "urlInfo" not in respData["body"]:
            print(f'频道 [{ch_name}] 接口返回空，更新失败！')
            return
        urlInfo = respData["body"]["urlInfo"]
        if not urlInfo or "url" not in urlInfo or not urlInfo["url"]:
            print(f'频道 [{ch_name}] 无播放地址，更新失败！')
            return

        # 拦截3：解密后地址为空
        playurl = getddCalcu720p(urlInfo["url"], data["pID"])
        if not playurl:
            print(f'频道 [{ch_name}] 解密后地址为空，更新失败！')
            return

        # 拦截4：重定向处理（增加None判断）
        if playurl != "":
            z = 1
            while z <= 6:
                try:
                    obj = requests.get(playurl, allow_redirects=False, timeout=5)
                    # 拦截：响应头为空
                    if not obj or not obj.headers:
                        z += 1
                        time.sleep(0.15)
                        continue
                    location = obj.headers.get("Location", "")
                    if location and location.startswith("http://hlsz"):
                        playurl = location
                        break
                    z += 1
                    if z <= 6:
                        time.sleep(0.15)
                except Exception as e:
                    z += 1
                    time.sleep(0.15)
                    continue
        if z == 7:
            print(f'频道 [{ch_name}] 重定向超过6次，更新失败！')
            return

        # 核心：智能分类（抛弃原爬取分类）
        new_category = smart_classify(ch_name)
        # 生成tvg-logo（匹配指定格式：xxx.png）
        tvg_logo = f"{ch_name.replace('CCTV-', 'CCTV').replace('+', '').replace(' ', '').replace('体育赛事', '')}.png"

        # 自动去重：以「频道名+播放地址」为唯一标识
        unique_key = f"{ch_name}_{playurl}"
        if unique_key in exist_channels:
            print(f'频道 [{ch_name}]【{new_category}】地址已存在，自动去重！')
            return
        exist_channels.add(unique_key)

        # 构造m3u/txt条目（严格匹配你的格式要求）
        m3u_item = f'#EXTINF:-1 tvg-name="{ch_name}" tvg-logo="{tvg_logo}" group-title="{new_category}",{ch_name}\n{playurl}\n'
        txt_item = f"{ch_name},{playurl}\n"

        # 按智能分类收集数据
        m3u_data[new_category].append(m3u_item)
        txt_data[new_category].append(txt_item)
        print(f'频道 [{ch_name}]【{new_category}】更新成功！')

    except Exception as e:
        ch_name = data["name"].strip() if data and "name" in data else "未知频道"
        print(f'频道 [{ch_name}] 更新失败！')
        print(f"ERROR:{e}")


# -------------------------- 原逻辑：多线程处理 --------------------------
def update(live, url):
    """多线程处理分类（原逻辑不变）"""
    global FLAG
    global headers
    try:
        pool = ThreadPoolExecutor(thread_mum)
        response = requests.get(url, headers=headers, timeout=10).json()
        if not response or "body" not in response or not response["body"] or "dataList" not in response["body"]:
            print(f"分类 [{live}] 无频道数据，更新失败！")
            return
        dataList = response["body"]["dataList"]
        for flag, data in enumerate(dataList):
            pool.submit(append_All_Live, live, FLAG + flag, data)
        pool.shutdown()
        FLAG += len(dataList)
    except Exception as e:
        print(f"分类 [{live}] 多线程处理失败：{e}")


# -------------------------- 主函数 --------------------------
def main():
    """主函数（央视频道排序，按格式写入双文件）"""
    # 1. 初始化文件
    writefile(m3u_path, M3U_HEADER)
    writefile(txt_path, "")
    print("===== 开始爬取所有频道数据 =====")
    # 2. 遍历原分类爬取（仅爬取，分类由智能函数判断）
    for live in lives:
        print(f"\n分类 ----- [{live}] ----- 开始更新. . .")
        url = f'https://program-sc.miguvideo.com/live/v2/tv-data/{LIVE[live]}'
        update(live, url)

    # 3. 央视频道数字升序排序
    if m3u_data["📺央视频道"]:
        m3u_data["📺央视频道"] = sort_cctv_channels(m3u_data["📺央视频道"], is_m3u=True)
        txt_data["📺央视频道"] = sort_cctv_channels(txt_data["📺央视频道"], is_m3u=False)
        print("\n✅ 央视频道已按数字从小到大排序完成！")

    # 4. 写入m3u文件（按分类：央视→卫视→其他）
    print("\n===== 开始写入migu.m3u文件 =====")
    for cate in ["📺央视频道", "📺卫视频道", "📺其他"]:
        if m3u_data[cate]:
            print(f"写入{cate}：{len(m3u_data[cate])}条")
            for item in m3u_data[cate]:
                appendfile(m3u_path, item)

    # 5. 写入txt文件（按分类：央视→卫视→其他，匹配genre格式）
    print("\n===== 开始写入migu.txt文件 =====")
    for cate in ["📺央视频道", "📺卫视频道", "📺其他"]:
        if txt_data[cate]:
            appendfile(txt_path, f"{cate},#genre#\n")
            print(f"写入{cate}：{len(txt_data[cate])}条")
            for item in txt_data[cate]:
                appendfile(txt_path, item)

    # 统计结果
    total_cctv = len(m3u_data["📺央视频道"])
    total_weishi = len(m3u_data["📺卫视频道"])
    total_other = len(m3u_data["📺其他"])
    total = total_cctv + total_weishi + total_other
    print(f"\n🎉 双格式文件生成完成！总计：{total}条有效频道")
    print(f"📺 央视频道：{total_cctv}条 | 📺 卫视频道：{total_weishi}条 | 📺 其他频道：{total_other}条")
    print(f"📁 M3U格式文件：{m3u_path}")
    print(f"📁 TXT格式文件：{txt_path}")


if __name__ == "__main__":
    main()
