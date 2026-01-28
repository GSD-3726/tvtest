import asyncio
import http.cookies
import json
import re
import subprocess
import os
import requests
from time import time
from urllib.parse import quote, urljoin, urlparse

import m3u8
from aiohttp import ClientSession, TCPConnector
from multidict import CIMultiDictProxy

# ==============================================
# 【核心配置区】可直接修改，无需改下方代码
# ==============================================
# 远程链接地址（gh-proxy加速的raw地址，确保获取纯文本）
REMOTE_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
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
SAMPLE_SEGMENTS = 5  # M3U8抽样片段数量

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

def parse_result_file(url: str) -> list[dict]:
    """解析远程文本文件，返回包含{'name', 'url'}的字典列表"""
    items = []
    try:
        print(f"🔍 正在拉取远程文件：{url}")
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
        resp.raise_for_status()
        file_content = resp.text

        # 解析文件内容
        for line in file_content.splitlines():
            line = line.strip()
            if not line or '#genre#' in line:
                continue
            if ',' not in line:
                continue
            parts = line.split(',', 1)
            if len(parts) < 2:
                continue
            name, url = parts
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
# 【测速核心区】优化测速逻辑
# ==============================================
def print_startup_info():
    """打印启动信息和配置"""
    print("=" * 60)
    print("🎬 IPTV链接拉取+测速工具（优化版）")
    print("=" * 60)
    print(f"🔧 运行配置：")
    print(f"   - 远程链接：{REMOTE_URL}")
    print(f"   - 测速超时：{SPEED_TEST_TIMEOUT}秒 | 最低速度：{MIN_SPEED}MB/s")
    print(f"   - 分辨率过滤：{MIN_RESOLUTION}x~{MAX_RESOLUTION}x | 域名缓存：{'开启' if SPEED_TEST_FILTER_HOST else '关闭'}")
    print(f"   - M3U8抽样片段：{SAMPLE_SEGMENTS}个")
    print("=" * 60 + "\n")

async def get_headers(url: str, headers: dict = None) -> CIMultiDictProxy:
    """获取链接响应头（异步），用于判断内容类型、重定向"""
    if headers is None:
        headers = REQUEST_HEADERS.copy()
    async with ClientSession(connector=TCPConnector(ssl=False), trust_env=True) as session:
        try:
            async with session.head(url, headers=headers, timeout=SPEED_TEST_TIMEOUT, allow_redirects=True) as resp:
                return resp.headers
        except:
            # 头请求失败则用get请求获取头
            async with session.get(url, headers=headers, timeout=SPEED_TEST_TIMEOUT, allow_redirects=True) as resp:
                return resp.headers

async def get_url_content(url: str, headers: dict = None) -> str:
    """获取链接文本内容（异步），用于解析m3u8"""
    if headers is None:
        headers = REQUEST_HEADERS.copy()
    try:
        async with ClientSession(connector=TCPConnector(ssl=False), trust_env=True) as session:
            async with session.get(url, headers=headers, timeout=SPEED_TEST_TIMEOUT) as resp:
                if resp.status == 200:
                    return await resp.text()
        return ""
    except:
        return ""

async def get_speed_with_download(url: str, headers: dict = None, session: ClientSession = None) -> dict:
    """下载测速：获取延迟、下载大小、速度（优化版）"""
    start_time = time()
    delay, total_size = -1, 0
    created_session = False
    
    if session is None:
        session = ClientSession(connector=TCPConnector(ssl=False), trust_env=True)
        created_session = True
    
    try:
        async with session.get(url, headers=headers, timeout=SPEED_TEST_TIMEOUT) as resp:
            if resp.status == 200:
                # 记录首字节到达时间（延迟）
                delay = int(round((time() - start_time) * 1000))
                
                # 流式读取内容
                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        total_size += len(chunk)
    except Exception as e:
        # 忽略错误，返回默认值
        pass
    finally:
        total_time = max(time() - start_time, 0.001)  # 避免除0
        speed = total_size / total_time / 1024 / 1024  # MB/s
        
        if created_session:
            await session.close()
            
        return {
            'speed': speed,
            'delay': delay,
            'size': total_size,
            'time': total_time
        }

async def get_result(url: str, headers: dict = None) -> dict:
    """单链接测速：下载测速 + 分辨率 + m3u8解析（优化版）"""
    info = {
        'speed': 0,
        'delay': -1,
        'resolution': DEFAULT_IPV6_RES
    }
    
    try:
        # 清理URL参数
        clean_url = quote(url.split('$')[0], safe=':/?=&')
        
        # 获取响应头
        res_headers = await get_headers(clean_url, headers)
        
        # 处理重定向
        if location := res_headers.get('Location'):
            return await get_result(location, headers)
        
        # 检查是否为M3U8
        content_type = res_headers.get('Content-Type', '').lower()
        is_m3u8 = any(h in content_type for h in M3U8_HEADERS)
        
        if is_m3u8:
            # 获取M3U8内容
            content = await get_url_content(clean_url, headers)
            if not content:
                return info
            
            # 解析M3U8
            m3u8_obj = m3u8.loads(content)
            
            # 获取分辨率
            resolution = DEFAULT_IPV6_RES
            if m3u8_obj.playlists:
                # 多码率流：选择最高码率
                best_playlist = max(m3u8_obj.playlists, key=lambda p: p.stream_info.bandwidth)
                if best_playlist.stream_info.resolution:
                    w, h = best_playlist.stream_info.resolution
                    resolution = f"{w}x{h}"
                    
                # 获取子播放列表
                sub_url = urljoin(clean_url, best_playlist.uri)
                sub_content = await get_url_content(sub_url, headers)
                if sub_content:
                    sub_m3u8 = m3u8.loads(sub_content)
                    segments = sub_m3u8.segments
                else:
                    segments = []
            else:
                # 单码率流
                segments = m3u8_obj.segments
                if m3u8_obj.stream_info and m3u8_obj.stream_info.resolution:
                    w, h = m3u8_obj.stream_info.resolution
                    resolution = f"{w}x{h}"
            
            info['resolution'] = resolution
            
            # 抽样测速片段
            if segments:
                # 随机选择片段（避免顺序偏差）
                sample_count = min(SAMPLE_SEGMENTS, len(segments))
                sample_segments = segments[:sample_count]
                
                # 创建会话复用连接
                async with ClientSession(connector=TCPConnector(ssl=False), trust_env=True) as session:
                    tasks = []
                    for seg in sample_segments:
                        seg_url = urljoin(clean_url, seg.uri)
                        tasks.append(get_speed_with_download(seg_url, headers, session))
                    
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # 计算总速度和总时间
                    total_size = 0
                    total_time = 0
                    valid_delays = []
                    
                    for res in results:
                        if isinstance(res, dict) and res['time'] > 0:
                            total_size += res['size']
                            total_time += res['time']
                            if res['delay'] > 0:
                                valid_delays.append(res['delay'])
                    
                    # 计算平均速度（总大小/总时间）
                    if total_time > 0:
                        info['speed'] = total_size / total_time / 1024 / 1024
                    
                    # 计算平均延迟
                    if valid_delays:
                        info['delay'] = int(round(sum(valid_delays) / len(valid_delays)))
                    elif segments:
                        info['delay'] = int(round((time() - time()) * 1000))  # 简化处理
            else:
                # 无片段时使用主URL测速
                download_res = await get_speed_with_download(clean_url, headers)
                info.update({
                    'speed': download_res['speed'],
                    'delay': download_res['delay']
                })
        else:
            # 非M3U8直接测速
            download_res = await get_speed_with_download(clean_url, headers)
            info.update({
                'speed': download_res['speed'],
                'delay': download_res['delay']
            })
            
    except Exception as e:
        # 错误处理
        pass
    
    return info

async def get_speed(data: dict) -> dict:
    """单链接测速入口（封装缓存+测速逻辑）"""
    global CACHE
    name = data['name']
    url = data['url']
    host = data['host']
    headers = REQUEST_HEADERS.copy()

    # 域名缓存逻辑
    if SPEED_TEST_FILTER_HOST and host in CACHE:
        cached = CACHE[host]
        return {
            'name': name,
            'url': url,
            'host': host,
            'speed': cached['speed'],
            'delay': cached['delay'],
            'resolution': cached['resolution']
        }

    # 执行实际测速
    result = await get_result(url, headers)
    result.update({
        'name': name,
        'url': url,
        'host': host
    })

    # 缓存结果
    if SPEED_TEST_FILTER_HOST and result['speed'] >= MIN_SPEED:
        CACHE[host] = {
            'speed': result['speed'],
            'delay': result['delay'],
            'resolution': result['resolution']
        }

    return result

def get_sort_result(results: list[dict]) -> list[dict]:
    """过滤并排序测速结果：按速度从快到慢，过滤无效链接"""
    valid_results = []
    
    for res in results:
        speed = res.get('speed') or 0
        delay = res.get('delay')
        reso = res.get('resolution')
        
        # 跳过无效延迟
        if delay == -1:
            continue
            
        # 速度过滤
        if OPEN_FILTER_SPEED and speed < MIN_SPEED:
            continue
            
        # 分辨率过滤
        if OPEN_FILTER_RESOLUTION and reso and reso != "音频流":
            try:
                # 处理分辨率格式（可能包含空格等）
                reso_clean = reso.replace(' ', '')
                if 'x' in reso_clean:
                    res_w = int(reso_clean.split('x')[0])
                    if res_w < MIN_RESOLUTION or res_w > MAX_RESOLUTION:
                        continue
            except:
                # 解析失败保留
                pass
                
        valid_results.append(res)
    
    # 按速度降序排序，速度相同则按延迟升序
    return sorted(
        valid_results, 
        key=lambda x: (-(x.get('speed') or 0), x.get('delay') or 9999)
    )

async def batch_speed_test(items: list[dict]) -> list[dict]:
    """批量测速并返回有效链接"""
    global CACHE
    CACHE = {}  # 清空缓存
    
    # 准备测速任务
    test_tasks = []
    for item in items:
        try:
            parsed = urlparse(item['url'])
            host = parsed.netloc
            test_tasks.append({
                'name': item['name'],
                'url': item['url'],
                'host': host
            })
        except:
            continue
    
    # 异步批量测速
    print(f"🚀 开始批量测速（共{len(test_tasks)}个有效任务）")
    tasks = [get_speed(data) for data in test_tasks]
    test_results = await asyncio.gather(*tasks, return_exceptions=False)
    
    # 过滤排序
    sorted_res = get_sort_result(test_results)
    print(f"✅ 测速完成，保留 {len(sorted_res)} 个有效链接\n")
    return sorted_res

# ==============================================
# 【主函数】
# ==============================================
async def main():
    """主执行流程"""
    print_startup_info()
    init_output_dir()
    
    try:
        # 1. 拉取远程文件链接
        items = parse_result_file(REMOTE_URL)
        
        # 2. 批量测速
        valid_items = await batch_speed_test(items)
        
        # 3. 生成文件
        if valid_items:
            save_txt(valid_items)
            save_m3u(valid_items)
        else:
            print("❌ 无有效链接，未生成文件")
            
    except Exception as e:
        print(f"❌ 程序执行出错: {str(e)}")
    
    print("\n" + "=" * 60)
    print("🎉 所有任务执行完成！输出文件在：output 目录")
    print("=" * 60)

if __name__ == "__main__":
    # 适配Windows系统asyncio事件循环问题
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
