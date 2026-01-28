import asyncio
import http.cookies
import json
import os
import re
import subprocess
import requests
from time import time
from urllib.parse import quote, urljoin
from aiohttp import ClientSession, TCPConnector
from multidict import CIMultiDictProxy
import m3u8

# ==============================================
# 【核心配置区】可直接修改，无需改下方代码
# ==============================================
# 远程链接地址（gh-proxy加速的raw地址，确保获取纯文本）
REMOTE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
# 输出目录
OUTPUT_DIR = "output"
# 生成文件名
TXT_FILENAME = "result.txt"
M3U_FILENAME = "iptv.m3u"
# 请求头（防反爬）
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}
# 链接匹配正则
URL_PATTERN = re.compile(r'https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]')

# 【测速配置】可根据需求调整
SPEED_TEST_TIMEOUT = 10  # 单链接测速超时（秒）
SPEED_TEST_FILTER_HOST = True  # 按域名缓存测速结果
OPEN_FILTER_RESOLUTION = True  # 开启分辨率过滤
MIN_RESOLUTION = 720  # 最低分辨率（宽）
MAX_RESOLUTION = 2160  # 最高分辨率（宽）
OPEN_FILTER_SPEED = True  # 开启速度过滤
MIN_SPEED = 1  # 最低有效速度（MB/s）
OPEN_SUPPLY = False  # 关闭备用源兼容
IPV6_SUPPORT = False  # 关闭IPv6（如需开启需配置代理）

# 固定配置
M3U8_HEADERS = ['application/x-mpegurl', 'application/vnd.apple.mpegurl', 'audio/mpegurl', 'audio/x-mpegurl']
DEFAULT_IPV6_DELAY = 0.1
DEFAULT_IPV6_RES = "1920x1080"
DEFAULT_IPV6_RESULT = {'speed': float("inf"), 'delay': DEFAULT_IPV6_DELAY, 'resolution': DEFAULT_IPV6_RES}
http.cookies._is_legal_key = lambda _: True
CACHE = {}  # 测速全局缓存

# ==============================================
# 【工具函数区】拉取链接/生成文件/初始化
# ==============================================

def parse_tvbox_lines(text: str) -> list[dict]:
    """
    解析 TVBox / IPTV CSV 格式
    返回：[{name, url}]
    """
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or '#genre#' in line:
            continue
        if ',' not in line:
            continue
        name, url = line.split(',', 1)
        url = url.strip()
        if url.startswith(('http://', 'https://')):
            items.append({'name': name.strip(), 'url': url})
    return items

def init_output_dir():
    """初始化输出目录"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    print(f"✅ 输出目录初始化完成：{OUTPUT_DIR}")

def get_remote_links() -> list[dict]:
    """拉取远程txt中的所有链接，去重并保留原顺序"""
    try:
        print(f"🔍 拉取远程链接：{REMOTE_URL}")
        resp = requests.get(REMOTE_URL, headers=REQUEST_HEADERS, timeout=30)
        resp.raise_for_status()
        items = parse_tvbox_lines(resp.text)
        if not items:
            raise Exception("未匹配到任何有效链接")
        print(f"✅ 成功拉取 {len(items)} 个有效链接")
        return items
    except Exception as e:
        print(f"❌ 拉取链接失败：{str(e)}")
        raise SystemExit(1)

def save_txt(items: list[dict]):
    """按原格式保存TXT文件（每行一个链接）"""
    txt_path = os.path.join(OUTPUT_DIR, TXT_FILENAME)
    with open(txt_path, 'w', encoding='utf-8') as f:
        for item in items:
            f.write(f"{item['name']},{item['url']}\n")
    print(f"✅ TXT文件生成：{txt_path}（{len(items)}个链接）")

def save_m3u(items: list[dict]):
    """生成标准IPTV M3U文件（适配VLC/TVBox/PotPlayer，含EPG）"""
    m3u_path = os.path.join(OUTPUT_DIR, M3U_FILENAME)
    with open(m3u_path, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U x-tvg-url=\"https://epg.112114.xyz/epg.xml.gz\"\n\n")
        for idx, item in enumerate(items, 1):
            f.write(f"#EXTINF:-1,{item['name']}\n{item['url']}\n\n")
    print(f"✅ M3U文件生成：{m3u_path}（{len(items)}个频道）")

# ==============================================
# 【测速核心区】保留所有原测速优化逻辑
# ==============================================

async def get_speed_with_download(url: str, headers: dict = None, session: ClientSession = None) -> dict:
    """下载测速：获取延迟、下载大小、速度"""
    start_time = time()
    delay, total_size = -1, 0
    created_session = False
    if session is None:
        session = ClientSession(connector=TCPConnector(ssl=False), trust_env=True)
        created_session = True
    try:
        async with session.get(url, headers=headers, timeout=SPEED_TEST_TIMEOUT) as resp:
            if resp.status == 200:
                delay = int(round((time() - start_time) * 1000))
                async for chunk in resp.content.iter_any():
                    if chunk:
                        total_size += len(chunk)
    except:
        pass
    finally:
        total_time = max(time() - start_time, 0.001)  # 避免除0
        speed = total_size / total_time / 1024 / 1024
        if created_session:
            await session.close()
        return {'speed': speed, 'delay': delay, 'size': total_size, 'time': total_time}

async def batch_speed_test(items: list[dict]) -> list[dict]:
    """批量测速并返回有效链接"""
    global CACHE
    CACHE = {}  # 清空缓存
    # 构造测速任务
    test_tasks = [{'name': item['name'], 'url': item['url'], 'host': item['url'].split('/')[2], 'ipv_type': 'ipv4'} for item in items]
    # 异步批量测速
    print(f"🚀 开始批量测速（共{len(test_tasks)}个链接）")
    tasks = [get_speed(data) for data in test_tasks]
    test_results = await asyncio.gather(*tasks, return_exceptions=False)
    # 过滤排序
    sorted_res = get_sort_result(test_results)
    valid_items = [{'name': res['name'], 'url': res['url']} for res in sorted_res]
    print(f"✅ 测速完成，保留 {len(valid_items)} 个有效链接\n")
    return valid_items

async def main():
    """主执行流程"""
    # 打印启动信息
    print("=" * 60)
    print("🎬 IPTV链接拉取+测速工具（TVBox专用版）")
    print("=" * 60)
    # 1. 初始化目录
    init_output_dir()
    # 2. 拉取远程链接
    items = get_remote_links()
    # 3. 批量测速
    valid_items = await batch_speed_test(items)
    # 4. 生成文件
    if valid_items:
        save_txt(valid_items)
        save_m3u(valid_items)
    else:
        print("❌ 无有效链接，未生成文件")
    # 执行完成
    print("\n" + "=" * 60)
    print("🎉 所有任务执行完成！输出文件在：output 目录")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
