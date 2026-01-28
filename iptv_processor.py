import asyncio
import http.cookies
import json
import re
import subprocess
import os
import requests
from time import time
from urllib.parse import quote, urljoin

import m3u8
from aiohttp import ClientSession, TCPConnector
from multidict import CIMultiDictProxy

# ==============================================
# 【核心配置区】可直接修改，无需改下方代码
# ==============================================
# 本地文件路径或远程链接
RESULT_FILE_PATH = '/mnt/data/result.txt'  # 本地文件路径
REMOTE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"  # 远程链接
OUTPUT_DIR = "output"
TXT_FILENAME = "result.txt"
M3U_FILENAME = "iptv.m3u"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}
URL_PATTERN = re.compile(r'https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]')

# 测速配置
SPEED_TEST_TIMEOUT = 10  # 单链接测速超时（秒）
SPEED_TEST_FILTER_HOST = True  # 按域名缓存测速结果
OPEN_FILTER_RESOLUTION = True  # 开启分辨率过滤
MIN_RESOLUTION = 720  # 最低分辨率（宽）
MAX_RESOLUTION = 2160  # 最高分辨率（宽）
OPEN_FILTER_SPEED = True  # 开启速度过滤
MIN_SPEED = 1  # 最低有效速度（MB/s）

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
def init_output_dir():
    """初始化输出目录"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    print(f"✅ 输出目录初始化完成：{OUTPUT_DIR}")

def parse_result_file(file_path: str) -> list[dict]:
    """解析本地文本文件，返回包含{'name', 'url'}的字典列表"""
    items = []
    try:
        if file_path.startswith("http"):  # 如果是URL（远程链接）
            print(f"🔍 正在拉取远程文件：{file_path}")
            resp = requests.get(file_path, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            file_content = resp.text
        else:  # 如果是本地文件路径
            print(f"🔍 正在读取本地文件：{file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()

        # 解析文件内容
        for line in file_content.splitlines():
            line = line.strip()
            if not line or '#genre#' in line:
                continue
            if ',' not in line:
                continue
            name, url = line.split(',', 1)
            items.append({'name': name.strip(), 'url': url.strip()})

        if not items:
            raise ValueError("未匹配到任何有效链接")
        print(f"✅ 成功解析文件，找到 {len(items)} 个有效链接")
    except Exception as e:
        print(f"❌ 解析文件失败：{e}")
        raise SystemExit(1)

    return items

def save_txt(items: list[dict]):
    """保存链接到 TXT 文件（每行一个链接）"""
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
def print_startup_info():
    """打印启动信息和配置"""
    print("=" * 60)
    print("🎬 IPTV链接拉取+测速工具（单文件版）")
    print("=" * 60)
    print(f"🔧 运行配置：")
    print(f"   - 远程链接：{RESULT_FILE_PATH}")
    print(f"   - 测速超时：{SPEED_TEST_TIMEOUT}秒 | 最低速度：{MIN_SPEED}MB/s")
    print(f"   - 分辨率过滤：{MIN_RESOLUTION}x~{MAX_RESOLUTION}x | 域名缓存：{'开启' if SPEED_TEST_FILTER_HOST else '关闭'}")
    print("=" * 60 + "\n")

# ==============================================
# 【测速核心区】get_speed、get_result 和测速逻辑
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

async def get_result(url: str, headers: dict = None) -> dict:
    """单链接测速：下载测速 + 分辨率 + m3u8解析"""
    info = {'speed': 0, 'delay': -1, 'resolution': None}
    try:
        url = quote(url, safe=':/?$&=@[]%').partition('$')[0]
        res_headers = await get_headers(url, headers)
        # 处理重定向
        if location := res_headers.get('Location'):
            return await get_result(location, headers)
        # 解析m3u8流
        content = await get_url_content(url, headers)
        if content and any(h in res_headers.get('Content-Type', '').lower() for h in M3U8_HEADERS):
            m3u8_obj = m3u8.loads(content)
            segment_urls = []
            # 处理多码率m3u8，选最高码率
            if m3u8_obj.playlists:
                best_playlist = max(m3u8_obj.playlists, key=lambda p: p.stream_info.bandwidth)
                playlist_content = await get_url_content(urljoin(url, best_playlist.uri), headers)
                if playlist_content:
                    segment_urls = [urljoin(url, s.uri) for s in m3u8.loads(playlist_content).segments]
            else:
                segment_urls = [urljoin(url, s.uri) for s in m3u8_obj.segments]
            # 测速m3u8片段（跳过第一个初始化片段，取后续5个）
            if segment_urls:
                sample_segs = segment_urls[1:6] if len(segment_urls) > 1 else segment_urls
                tasks = [get_speed_with_download(seg, headers) for seg in sample_segs]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # 过滤有效结果，按大小加权计算
                valid_res = [r for r in results if isinstance(r, dict) and r['time'] > 0 and r['size'] > 0]
                if valid_res:
                    total_size = sum(r['size'] for r in valid_res)
                    weighted_time = sum((r['size']/total_size)*r['time'] for r in valid_res)
                    info['speed'] = total_size / weighted_time / 1024 / 1024
                    info['delay'] = int(round(sum(r['delay'] for r in valid_res if r['delay']>0)/len(valid_res)))
                else:
                    info['delay'] = int(round((time()-start_time)*1000))
        else:
            # 非m3u8直接测速
            download_res = await get_speed_with_download(url, headers)
            info.update({'speed': download_res['speed'], 'delay': download_res['delay']})
    except:
        pass
    return info

async def get_speed(data: dict, headers: dict = None) -> dict:
    """单链接测速入口：带缓存"""
    url = data['url']
    result = {'speed': 0, 'delay': -1, 'resolution': None, 'url': url}
    use_headers = {**REQUEST_HEADERS, **(headers or {})}
    try:
        # 生成缓存key（域名/完整URL）
        cache_key = data.get('host') or url.split('/')[2] if SPEED_TEST_FILTER_HOST else url
        # 从缓存获取
        if cache_key in CACHE:
            result = get_avg_result(CACHE[cache_key])
            result['url'] = url
        else:
            # IPv6处理
            if data.get('ipv_type') == "ipv6" and IPV6_SUPPORT:
                result.update(DEFAULT_IPV6_RESULT)
            else:
                result.update(await get_result(url, use_headers))
            # 加入缓存
            if cache_key:
                CACHE.setdefault(cache_key, []).append(result)
    finally:
        return result

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
    valid_links = [res['url'] for res in sorted_res]
    print(f"✅ 测速完成，保留 {len(valid_links)} 个有效链接\n")
    return valid_links

async def main():
    """主执行流程"""
    # 打印启动信息
    print_startup_info()
    # 1. 初始化目录
    init_output_dir()
    # 2. 拉取本地文件链接
    items = parse_result_file(RESULT_FILE_PATH if RESULT_FILE_PATH else REMOTE_URL)
    # 3. 批量测速
    valid_links = await batch_speed_test(items)
    # 4. 生成文件
    if valid_links:
        save_txt(valid_links)
        save_m3u(valid_links)
    else:
        print("❌ 无有效链接，未生成文件")
    # 执行完成
    print("\n" + "=" * 60)
    print("🎉 所有任务执行完成！输出文件在：output 目录")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
