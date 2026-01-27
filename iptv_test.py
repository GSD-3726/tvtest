import aiohttp
import asyncio
import time
import statistics
from urllib.parse import urljoin
from datetime import datetime
import requests
from aiohttp import ClientTimeout

# ===================== 适配【海外访问国内源】核心配置（关键！）=====================
RAW_TXT_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
OUTPUT_FILE = "result.txt"
CONCURRENT_LIMIT = 6  # 调低并发，避免被国内服务器屏蔽（海外专用）
TEST_SHARD_COUNT = 2  # 分片测试数，兼顾速度和准确性
# 适配海外高延迟：大幅放宽超时时间
TIMEOUT_LIGHT = 4     # 轻量检测超时（原2秒，海外调4秒）
TIMEOUT_DEEP = 5      # 深度测速超时（原3秒，海外调5秒）
# 适配海外访问的流畅阈值（核心：优先保证能播放，而非极致1080P）
FAIL_RATE_THRESHOLD = 0.1    # 失败率≤10%（原5%）
AVG_TIME_THRESHOLD = 3.0     # 平均耗时≤3秒（原1.5秒，适配跨境延迟）
MAX_TIME_THRESHOLD = 6.0     # 最大耗时≤6秒（原4秒）
MIN_HD_SHARD_SIZE = 81920    # 分片≥80KB即可（原100KB，兼容国内准高清源）
SUPPORTED_PROTOCOLS = ("http://", "https://")
# 国内源请求重试次数
RETRY_TIMES = 1

# ===================== 工具函数：适配国内源+海外访问 =====================
async def async_retry_request(coro, times=RETRY_TIMES):
    """请求重试机制：国内源偶尔抽风，重试1次即可"""
    for _ in range(times + 1):
        try:
            return await coro
        except Exception:
            continue
    return None

async def async_light_check(session, url):
    """弱化前置轻量检测：失败不丢弃，仅做参考"""
    try:
        async with session.get(url, timeout=ClientTimeout(total=TIMEOUT_LIGHT)) as resp:
            await resp.content.read(2048)  # 轻量检测调2KB，更稳
            return resp.status in [200, 301, 302]
    except Exception:
        return False  # 失败仅返回False，后续直接进深度测速

async def async_download_hd(session, url, max_bytes):
    """异步下载：适配国内源，带超时/重试"""
    try:
        start_time = time.time()
        async with session.get(url, timeout=ClientTimeout(total=TIMEOUT_DEEP)) as resp:
            if resp.status not in [200, 301, 302]:
                return 0, False, 0
            total_bytes = 0
            async for chunk in resp.content.iter_chunked(1024):
                total_bytes += len(chunk)
                if total_bytes >= max_bytes:
                    break
            cost_time = round(time.time() - start_time, 3)
            # 国内源放宽有效判定：至少1KB即可
            return cost_time, total_bytes >= 1024, total_bytes
    except Exception:
        return 0, False, 0

async def parse_m3u8_async(session, m3u8_url):
    """异步解析m3u8：国内源兼容，带重试"""
    result = await async_retry_request(session.get(m3u8_url, timeout=ClientTimeout(total=TIMEOUT_DEEP)))
    if not result or result.status not in [200, 301, 302]:
        return None
    text = await result.text()
    base_url = m3u8_url.rsplit('/', 1)[0] + '/' if '/' in m3u8_url else ''
    shards = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and line.endswith('.ts'):
            shards.append(urljoin(base_url, line))
            if len(shards) >= TEST_SHARD_COUNT:
                break
    return shards if shards else None

async def test_m3u8_async(session, m3u8_url):
    """测试m3u8：适配海外访问国内源，阈值放宽"""
    shards = await parse_m3u8_async(session, m3u8_url)
    if not shards:
        return False
    # 并发测试分片
    tasks = [async_download_hd(session, shard, MIN_HD_SHARD_SIZE) for shard in shards]
    results = await asyncio.gather(*tasks)
    # 统计有效结果（分片≥80KB+下载成功）
    cost_times = []
    for t, ok, b in results:
        if ok and b >= MIN_HD_SHARD_SIZE:
            cost_times.append(t)
    if not cost_times:
        return False
    # 适配海外的阈值判定
    fail_rate = (len(results) - len(cost_times)) / len(results)
    avg_time = statistics.mean(cost_times)
    max_time = max(cost_times)
    return (fail_rate <= FAIL_RATE_THRESHOLD and
            avg_time <= AVG_TIME_THRESHOLD and
            max_time <= MAX_TIME_THRESHOLD)

async def test_flv_async(session, flv_url):
    """测试FLV：国内源专用，放宽判定"""
    cost_time, ok, total_bytes = await async_download_hd(session, flv_url, 102400)
    # FLV仅需下载≥10KB+耗时≤阈值即可（适配国内源）
    return ok and total_bytes >= 10240 and cost_time <= MAX_TIME_THRESHOLD

async def test_url_async(session, name, url, result_queue):
    """核心测试逻辑：弱化前置检测，失败直接进深度测速（关键修复！）"""
    print(f"测试中：{name} | {url[:60]}...", end=" ")
    # 步骤1：轻量检测（仅参考，失败不丢弃）
    light_ok = await async_light_check(session, url)
    if not light_ok:
        print("⚠️  轻量检测超时，进入深度测速...", end=" ")
    # 步骤2：深度测速（无论前置检测是否成功，都执行）
    try:
        if url.endswith('.m3u8'):
            is_smooth = await test_m3u8_async(session, url)
        elif url.endswith('.flv'):
            is_smooth = await test_flv_async(session, url)
        else:
            is_smooth = False
            print("❌ 非m3u8/flv协议")
            return
    except Exception:
        is_smooth = False
        print("❌ 深度测速异常")
        return
    # 步骤3：结果判定
    if is_smooth:
        print("✅ 流畅（保留）")
        await result_queue.put((name, url))
    else:
        print("❌ 卡顿/低清（海外访问受限）")

# ===================== 主逻辑：保留原始格式+海外适配 =====================
def download_original_txt():
    """下载原始txt：带国内代理，更稳"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://github.com/",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
        resp = requests.get(RAW_TXT_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return [line.rstrip('\n') for line in resp.text.splitlines() if line.strip()]
    except Exception as e:
        print(f"❌ 下载原始文件失败：{e}")
        return []

async def main_async():
    print("="*70)
    print(f"IPTV测速（海外访问国内源专用）| {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"并发数：{CONCURRENT_LIMIT} | 超时：轻量{TIMEOUT_LIGHT}s / 深度{TIMEOUT_DEEP}s")
    print("="*70)
    
    # 1. 下载原始txt（保留所有格式）
    original_lines = download_original_txt()
    if not original_lines:
        print("❌ 无原始数据，终止流程")
        return
    
    # 2. 解析原始行：分离分类/地址/更新时间
    genre_map = {}        # 分类行位置映射
    url_tasks = []        # 待测试地址 [(name, url)]
    update_time_line = "" # 更新时间行
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for line in original_lines:
        if line.startswith("🕘️") and "#genre#" in line:
            update_time_line = f"🕘️{current_datetime},#genre#"
        elif "#genre#" in line and not line.startswith("🕘️"):
            genre_map[len(url_tasks)] = line
        elif "," in line:
            name_part, url_part = line.split(",", 1)
            name = name_part.strip()
            url = url_part.strip()
            if url.startswith(SUPPORTED_PROTOCOLS):
                url_tasks.append((name, url))

    # 3. 异步Session配置：【国内源专用核心配置】
    connector = aiohttp.TCPConnector(
        limit=CONCURRENT_LIMIT,
        verify_ssl=False,  # 关闭SSL验证（国内很多源证书不规范，海外访问会报错）
        ttl_dns_cache=300  # DNS缓存，提升国内源访问速度
    )
    # 模拟国内浏览器请求头（避免被国内源地域屏蔽）
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.baidu.com/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache"
    }
    # 创建Session：复用连接+国内头+关闭SSL
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        result_queue = asyncio.Queue()
        # 创建所有测试任务
        tasks = [test_url_async(session, n, u, result_queue) for n, u in url_tasks]
        await asyncio.gather(*tasks)

        # 4. 提取测试结果
        smooth_urls = []
        while not result_queue.empty():
            smooth_urls.append(await result_queue.get())

    # 5. 整理结果：严格保留原始分类+顺序+格式
    output_lines = [update_time_line] if update_time_line else []
    current_url_idx = 0
    # 按原始顺序插入分类行和对应地址
    sorted_genre = sorted(genre_map.items(), key=lambda x: x[0])
    for idx, genre_line in sorted_genre:
        output_lines.append(genre_line)
        # 插入该分类下的流畅地址
        while current_url_idx < len(smooth_urls):
            url_pos = url_tasks.index(smooth_urls[current_url_idx]) if smooth_urls[current_url_idx] in url_tasks else -1
            if url_pos >= idx:
                n, u = smooth_urls[current_url_idx]
                output_lines.append(f"{n},{u}")
                current_url_idx += 1
            else:
                break
    # 补充剩余流畅地址
    while current_url_idx < len(smooth_urls):
        n, u = smooth_urls[current_url_idx]
        output_lines.append(f"{n},{u}")
        current_url_idx += 1

    # 6. 写入结果：1:1匹配原始txt格式（无任何多余字符）
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    # 7. 统计输出
    print("="*70)
    print(f"✅ 测速完成 | 总测试地址：{len(url_tasks)} | 保留流畅地址：{len(smooth_urls)}")
    print(f"📄 结果文件：仓库根目录/{OUTPUT_FILE}（格式与原始完全一致）")
    print("="*70)

if __name__ == "__main__":
    # 适配所有系统（Windows/Linux/Ubuntu/GitHub Actions）
    try:
        if asyncio.get_event_loop_policy().__class__.__name__ == "WindowsProactorEventLoopPolicy":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass
    asyncio.run(main_async())
